"""Collector-Lauf anlegen - gemeinsam fuer "Jetzt einlesen" (API/CLI) und den Zeitplan.

Prueft Quellsystem, Mandant, Plattform und (bei Referenzquellen) die Lese-Zugangsdaten;
ein Geraet mit Agent wird immer extern gelesen (ADR 0009). Auditiert `collector_run.queued`.

Haengende Laeufe (Agent beendet, Ergebnis-Upload verloren, nie abgeholt) blockierten bis
0.11.2 "Alle einlesen" und den Zeitplan fuer immer (Dev-Befund): `expire_stale` beendet sie
nach einer Frist, `cancel` von Hand; `blocking` nennt Lauf, Zustand und seit wann.

Meldungen der Laeufe werden deutsch gespeichert; `show_message` uebersetzt sie beim Abruf
(0.90.0, ADR 0028 Schritt 4).
"""

import logging
import threading
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import audit, credentials
from app.config import get_settings
from app.i18n import L
from app.models import REFERENCE_PLATFORMS, Agent, CollectorRun, RunStatus, SourceSystem
from app.store import run_text, tenant_lock

log = logging.getLogger(__name__)

OPEN = (RunStatus.QUEUED, RunStatus.RUNNING)


class RunRejected(ValueError):
    """Lauf nicht anlegbar (Grund fuer 422 bzw. Scheduler-Log)."""


def queue_collect(
    session: Session,
    *,
    platform: str,
    tenant_id: int | None,
    source_system_id: int | None,
    execution: str,
    actor: str,
    trigger: str = "manual",
) -> CollectorRun:
    from app import modules  # spaet: modules nutzt die Modelle

    # Module je Mandant (0.107.0, ADR 0036): Worker aus = keine neuen Laeufe, Gateway aus =
    # keine Referenzquellen (IPAM) - auch nicht nach Zeitplan
    if not modules.enabled(session, tenant_id, "worker"):
        raise RunRejected(modules.disabled_text("worker"))
    if platform in {p.value for p in REFERENCE_PLATFORMS} and not modules.enabled(
            session, tenant_id, "gateway"):  # fmt: skip
        raise RunRejected(modules.disabled_text("gateway"))
    if source_system_id is not None:
        src = session.get(SourceSystem, source_system_id)
        if src is None:
            raise RunRejected(L("Quellsystem nicht gefunden", "source system not found"))
        if src.tenant_id is None or src.tenant_id != tenant_id:
            raise RunRejected(L("Quellsystem gehört zu einem anderen Mandanten",
                                "source system belongs to another tenant"))  # fmt: skip
        if src.platform.value != platform:
            raise RunRejected(L("Plattform passt nicht zum Quellsystem",
                                "platform does not match source system"))  # fmt: skip
        if src.platform in REFERENCE_PLATFORMS and not credentials.complete(
            src.platform.value, src.credentials
        ):
            # Referenzquellen haben keine Beispieldaten - ohne Lese-Zugang scheitert der Lauf
            # sicher; das NetBox-Schreib-Token wird bewusst nie zum Lesen verwendet. Nur
            # Antwort bzw. Log (Grund in „Alle einlesen“), nicht gespeichert
            kind, name = src.platform.value, src.name
            raise RunRejected(
                L(f"{kind}-Quelle „{name}“ hat kein Lese-Token („API-Token (lesen)“)",
                  f"{kind} source '{name}' has no read token ('API token (read)')")
                if kind == "netbox"
                else L(f"{kind}-Quelle „{name}“ hat keinen Benutzer/kein Passwort",
                       f"{kind} source '{name}' has no username/password")
            )  # fmt: skip
        if src.agent_id is not None:
            execution = "external"  # Geraet wird von seinem Agenten gelesen (ADR 0009)
    run = CollectorRun(
        platform=platform,
        tenant_id=tenant_id,
        source_system_id=source_system_id,
        execution=execution,
        trigger=trigger,
    )
    session.add(run)
    session.flush()  # run.id verfuegbar machen
    audit.record(
        session,
        actor=actor,
        action="collector_run.queued",
        subject=run.id,
        detail={
            "platform": platform,
            "tenant_id": tenant_id,
            "source_system_id": source_system_id,
            "execution": execution,
            "trigger": trigger,
        },
    )
    return run


def blocking(session: Session, source_system_id: int) -> CollectorRun | None:
    """Offener Lauf (wartet/laeuft) dieses Quellsystems (haengende vorher mit
    `expire_stale` beenden)."""
    stmt = (
        select(CollectorRun)
        .where(CollectorRun.source_system_id == source_system_id, CollectorRun.status.in_(OPEN))
        .order_by(CollectorRun.queued_at)
    )
    return session.scalars(stmt.limit(1)).first()


def busy(session: Session, source_system_id: int) -> bool:
    """Laeuft oder wartet schon ein Lauf fuer dieses Quellsystem? Haengende zaehlen nicht."""
    expire_stale(session)
    return blocking(session, source_system_id) is not None


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def describe(session: Session, run: CollectorRun) -> str:
    """Warum ein Quellsystem uebersprungen wird: Zustand, seit wann, wer liest (nur Antwort,
    nicht gespeichert - folgt der Sprache)."""
    zone = ZoneInfo(get_settings().scheduler_timezone)
    since = _aware(run.started_at if run.status is RunStatus.RUNNING else None) or _aware(
        run.queued_at
    )
    when = since.astimezone(zone).strftime("%d.%m. %H:%M") if since else "?"
    agent = session.get(Agent, run.agent_id) if run.agent_id else None
    if run.status is RunStatus.RUNNING:
        who = (
            L(f"Agent „{agent.name}“", f"agent “{agent.name}”")
            if agent
            else L("zentraler Worker", "central worker")
        )
        return L(f"Lauf läuft seit {when} ({who})", f"Run running since {when} ({who})")
    if run.execution == "external":
        src = session.get(SourceSystem, run.source_system_id) if run.source_system_id else None
        assigned = session.get(Agent, src.agent_id) if src is not None and src.agent_id else None
        who = (
            L(f"Agent „{assigned.name}“", f"agent “{assigned.name}”")
            if assigned
            else L("einen Agenten", "an agent")
        )
        return L(f"Lauf wartet seit {when} auf {who}", f"Run waiting since {when} for {who}")
    return L(
        f"Lauf wartet seit {when} auf den zentralen Worker",
        f"Run waiting since {when} for the central worker",
    )


def expire_stale(session: Session, now: datetime | None = None) -> int:
    """Laeufe ohne Ergebnis beenden: laufend laenger als `run_timeout_minutes`, wartend
    laenger als `run_queue_timeout_hours`. Ein spaeter eintreffendes Ergebnis wird trotzdem
    eingespielt (die Ergebnis-API nimmt es auch fuer beendete Laeufe an)."""
    settings = get_settings()
    now = now or datetime.now(UTC)
    running_limit = now - timedelta(minutes=settings.run_timeout_minutes)
    queued_limit = now - timedelta(hours=settings.run_queue_timeout_hours)
    expired = 0
    for run in session.scalars(select(CollectorRun).where(CollectorRun.status.in_(OPEN))):
        if run.status is RunStatus.RUNNING:
            since = _aware(run.started_at) or _aware(run.queued_at)
            if since is None or since > running_limit:
                continue
            reason = (
                f"abgebrochen: kein Ergebnis nach {settings.run_timeout_minutes} min - Agent "
                "beendet bzw. neu gestartet oder Ergebnis-Upload verloren; neu einlesen"
            )
        else:
            since = _aware(run.queued_at)
            if since is None or since > queued_limit:
                continue
            reason = (
                f"abgebrochen: nach {settings.run_queue_timeout_hours} h nicht abgeholt - "
                "Agent offline bzw. kein zentraler Worker"
            )
        if tenant_lock.state(run.id) is not None:
            continue  # Ergebnis angekommen: wartet aufs Einspielen bzw. wird eingespielt
        if _lock_open(session, run.id) is None:
            continue  # Ergebnis wird gerade eingespielt bzw. eine andere Abfrage beendet ihn
        _finish(session, run, reason, actor="system:timeout", now=now)
        expired += 1
    return expired


# -- Wartende Laeufe erneut anstossen (0.109.1) ---------------------------------------------
# Redis haelt die Warteschlange zum zentralen Worker ohne Datentraeger: wird der Container neu
# erstellt (Update, Neustart), sind wartende Auftraege weg und der Lauf bliebe bis zur Frist
# „wartet“ (Befund des Anwenders: NetBox-Lauf wird nicht abgeholt). Der Zeitplan schickt sie
# deshalb nach `run_redispatch_minutes` erneut; den Lauf liest trotzdem nur einer (`claim`).
# Beides im Prozess (der Orchestrator laeuft als ein Prozess, wie `app_refresh`).

_dispatched: dict[str, datetime] = {}  # Lauf -> letzter Auftrag an den zentralen Worker
_claims: dict[str, str] = {}  # Lauf -> Auftrag (Celery-Task), der ihn liest
_CLAIMS_MAX = 5000


def waiting_central(session: Session) -> list[CollectorRun]:
    """Zentral gelesene Laeufe, die noch auf den Worker warten (aelteste zuerst)."""
    query = (
        select(CollectorRun)
        .where(CollectorRun.status == RunStatus.QUEUED, CollectorRun.execution == "inprocess")
        .order_by(CollectorRun.queued_at)
    )
    return list(session.scalars(query))


def redispatch_waiting(session: Session, dispatch, now: datetime | None = None) -> list[str]:
    """Wartende zentrale Laeufe erneut in die Warteschlange (`dispatch(platform, run_id)`),
    hoechstens einmal je `run_redispatch_minutes`. Rueckgabe = erneut geschickte Laeufe."""
    minutes = get_settings().run_redispatch_minutes
    if minutes <= 0:
        return []
    now = now or datetime.now(UTC)
    limit = now - timedelta(minutes=minutes)
    waiting = waiting_central(session)
    keep = {r.id for r in waiting}
    for run_id in [k for k in _dispatched if k not in keep]:
        _dispatched.pop(run_id, None)
    sent = []
    for run in waiting:
        last = _dispatched.get(run.id) or _aware(run.queued_at)
        if last is None or last > limit:
            continue
        try:
            dispatch(run.platform, run.id)
        except Exception:  # noqa: BLE001 - Redis weg: beim naechsten Durchgang wieder
            log.warning("Lauf %s nicht erneut geschickt (Warteschlange nicht erreichbar)", run.id,
                        exc_info=True)  # fmt: skip
            break
        _dispatched[run.id] = now
        sent.append(run.id)
        log.info("Lauf %s (%s) wartet seit %s - erneut an den zentralen Worker", run.id,
                 run.platform, _aware(run.queued_at))  # fmt: skip
    return sent


def claim(run: CollectorRun, task: str | None, now: datetime | None = None) -> bool:
    """Zentraler Worker holt das Ziel: der erste Auftrag (`task`) liest den Lauf, ein erneut
    geschickter bzw. spaeter eintreffender zweiter nicht. Ohne `task` (Worker vor 0.109.1)
    wie bisher: jeder darf. Setzt den Lauf von „wartet“ auf „läuft“."""
    if run.status is RunStatus.QUEUED:
        run.status = RunStatus.RUNNING
        run.started_at = now or datetime.now(UTC)
        if task:
            if len(_claims) >= _CLAIMS_MAX:
                for old in list(_claims)[: _CLAIMS_MAX // 2]:
                    _claims.pop(old, None)
            _claims[run.id] = task
        return True
    if not task:
        return True
    return _claims.get(run.id) == task  # derselbe Auftrag fragt erneut (Wiederholung)


def waiting_text(session: Session, run: CollectorRun) -> str | None:
    """Wer einen wartenden Lauf abholen muss (Liste der Laeufe, 0.109.1): Agent mit letztem
    Lebenszeichen bzw. der zentrale Worker, ob er schon erneut geschickt wurde."""
    if run.status is not RunStatus.QUEUED:
        return None
    zone = ZoneInfo(get_settings().scheduler_timezone)

    def at(dt: datetime | None) -> str:
        return _aware(dt).astimezone(zone).strftime("%d.%m. %H:%M") if dt else "?"

    if run.execution == "external":
        src = session.get(SourceSystem, run.source_system_id) if run.source_system_id else None
        agent = session.get(Agent, src.agent_id) if src is not None and src.agent_id else None
        if agent is None:
            return L("wartet auf einen Agenten", "waiting for an agent")
        seen = (L(f"zuletzt gesehen {at(agent.last_seen)}", f"last seen {at(agent.last_seen)}")
                if agent.last_seen else L("noch nie gesehen", "never seen"))  # fmt: skip
        return L(f"wartet auf Agent „{agent.name}“ ({seen})",
                 f"waiting for agent “{agent.name}” ({seen})")  # fmt: skip
    again = _dispatched.get(run.id)
    if again is not None:
        return L(f"wartet auf den zentralen Worker – erneut geschickt {at(again)}; Worker prüfen "
                 "(System → Verbindungen)",
                 f"waiting for the central worker – sent again {at(again)}; check the worker "
                 "(System → Connections)")  # fmt: skip
    return L("wartet auf den zentralen Worker", "waiting for the central worker")


_expiring = threading.Lock()


def expire_quietly(session: Session) -> int:
    """`expire_stale` fuer Lese-Abfragen (Liste der Laeufe, 0.80.1): eine Abfrage zugleich,
    im Savepoint - ein Fehler beim Beenden haengender Laeufe macht die Liste nicht zu einem
    HTTP 500 (Befund des Anwenders beim Aktualisieren der letzten Laeufe)."""
    if not _expiring.acquire(blocking=False):
        return 0  # die andere Abfrage beendet sie
    try:
        with session.begin_nested():
            return expire_stale(session)
    except SQLAlchemyError:
        log.warning("Haengende Laeufe nicht beendet", exc_info=True)
        return 0
    finally:
        _expiring.release()


def _lock_open(session: Session, run_id: str) -> CollectorRun | None:
    """Offenen Lauf sperren - gesperrte ueberspringen (SKIP LOCKED, 0.78.5): die Liste der
    Laeufe wartet nicht auf ein laufendes Einspielen bzw. eine zweite Abfrage der Liste und
    beendet keinen Lauf, dessen Ergebnis gerade ankommt (Befund: Fehler 500 in „Letzte
    Läufe“ waehrend des Einspielens)."""
    query = (
        select(CollectorRun)
        .where(CollectorRun.id == run_id, CollectorRun.status.in_(OPEN))
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    return session.scalars(query).first()


def show_message(message: str | None) -> str | None:
    """Gespeicherte (deutsche) Meldung eines Laufs bzw. Grund der Update-Pruefung eines Agenten
    in der Sprache der Anfrage (0.90.0, ADR 0028 Schritt 4): bekannte Teile aus
    `store/run_text.py`, Unbekanntes (Texte der Geraete, Ausnahmen) unveraendert."""
    return run_text.show(message)


def cancel(session: Session, run: CollectorRun, actor: str) -> None:
    """Offenen Lauf von Hand beenden (z. B. haengt nach einem Neustart des Agenten). Die
    Meldung wird gespeichert und bleibt deutsch, die Anzeige uebersetzt (`show_message`)."""
    who = actor.removeprefix("user:")
    _finish(session, run, f"abgebrochen von {who}", actor=actor)


def fail(session: Session, run: CollectorRun, message: str, actor: str) -> None:
    """Lauf mit Grund beenden, bevor er gelesen wird (z. B. Zugangsdaten nicht
    entschluesselbar, 0.113.2). Gespeichert deutsch, wie `cancel`."""
    _finish(session, run, message, actor=actor)


def _finish(
    session: Session, run: CollectorRun, message: str, actor: str, now: datetime | None = None
) -> None:
    run.status = RunStatus.FAILED
    run.finished_at = now or datetime.now(UTC)
    run.message = message
    audit.record(
        session,
        actor=actor,
        action="collector_run.cancelled",
        subject=run.id,
        detail={"platform": run.platform, "source_system_id": run.source_system_id},
    )
    session.flush()
