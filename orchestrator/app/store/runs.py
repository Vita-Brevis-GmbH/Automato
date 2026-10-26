"""Collector-Lauf anlegen - gemeinsam fuer "Jetzt einlesen" (API/CLI) und den Zeitplan.

Prueft Quellsystem, Mandant, Plattform und (bei Referenzquellen) die Lese-Zugangsdaten;
ein Geraet mit Agent wird immer extern gelesen (ADR 0009). Auditiert `collector_run.queued`.

Haengende Laeufe (Agent beendet, Ergebnis-Upload verloren, nie abgeholt) blockierten bis
0.11.2 "Alle einlesen" und den Zeitplan fuer immer (Dev-Befund): `expire_stale` beendet sie
nach einer Frist, `cancel` von Hand; `blocking` nennt Lauf, Zustand und seit wann.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, credentials
from app.config import get_settings
from app.models import REFERENCE_PLATFORMS, Agent, CollectorRun, RunStatus, SourceSystem

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
    if source_system_id is not None:
        src = session.get(SourceSystem, source_system_id)
        if src is None:
            raise RunRejected("source system not found")
        if src.tenant_id is None or src.tenant_id != tenant_id:
            raise RunRejected("source system belongs to another tenant")
        if src.platform.value != platform:
            raise RunRejected("platform does not match source system")
        if src.platform in REFERENCE_PLATFORMS and not credentials.complete(
            src.platform.value, src.credentials
        ):
            # Referenzquellen haben keine Beispieldaten - ohne Lese-Zugang scheitert der Lauf
            # sicher; das NetBox-Schreib-Token wird bewusst nie zum Lesen verwendet
            missing = "read token" if src.platform.value == "netbox" else "username/password"
            raise RunRejected(f"{src.platform.value} source '{src.name}' has no {missing}")
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
    """Warum ein Quellsystem uebersprungen wird: Zustand, seit wann, wer liest."""
    zone = ZoneInfo(get_settings().scheduler_timezone)
    since = _aware(run.started_at if run.status is RunStatus.RUNNING else None) or _aware(
        run.queued_at
    )
    when = since.astimezone(zone).strftime("%d.%m. %H:%M") if since else "?"
    agent = session.get(Agent, run.agent_id) if run.agent_id else None
    if run.status is RunStatus.RUNNING:
        who = f"Agent „{agent.name}“" if agent else "zentraler Worker"
        return f"Lauf läuft seit {when} ({who})"
    if run.execution == "external":
        src = session.get(SourceSystem, run.source_system_id) if run.source_system_id else None
        assigned = session.get(Agent, src.agent_id) if src is not None and src.agent_id else None
        who = f"Agent „{assigned.name}“" if assigned else "einen Agenten"
        return f"Lauf wartet seit {when} auf {who}"
    return f"Lauf wartet seit {when} auf den zentralen Worker"


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
        _finish(session, run, reason, actor="system:timeout", now=now)
        expired += 1
    return expired


def cancel(session: Session, run: CollectorRun, actor: str) -> None:
    """Offenen Lauf von Hand beenden (z. B. haengt nach einem Neustart des Agenten)."""
    who = actor.removeprefix("user:")
    _finish(session, run, f"abgebrochen von {who}", actor=actor)


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
