"""Betriebszustand (Dev-Befund 0.12.0: "wo laufen Dienste nicht?") - Ampel je Pruefung.

Nur lesend und aus dem, was Automato ohnehin weiss: Datenbank/Migration, Zeitplan (letzter
Durchgang), zentraler Worker (holt er zentrale Laeufe ab?), Agenten (zuletzt gemeldet,
Update-Probleme), Quellsysteme (letzter Lauf fehlgeschlagen bzw. zu alt), NetBox-Sync und
Sicherung, seit 0.48.0 die DB-Rolle (ohne DDL?) und die Aufbewahrung (zuletzt aufgeraeumt), seit
0.49.0 TLS intern (eingeschaltet, Zertifikat gueltig?). Je Pruefung `ok` | `warn` | `error` mit
Satz und Ziel-Ansicht im UI.
"""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import scheduler, tls
from app.config import Settings
from app.i18n import L
from app.models import (
    Agent,
    CollectorRun,
    RunStatus,
    SourceSystem,
    SyncPlan,
    SyncStatus,
)
from app.store import data_quality, db_roles, retention, runs

AGENT_OFFLINE = timedelta(minutes=30)  # Wach-Modus fragt alle 20 s, Neustart alle 15 min
CENTRAL_WAIT = timedelta(minutes=10)
BACKUP_MAX_AGE = timedelta(hours=26)
SYNC_RECENT = timedelta(hours=24)
PURGE_LATE = timedelta(days=2)  # Zeitplan raeumt taeglich auf
CERT_WARN_DAYS = 30  # Zertifikat der internen CA bald erneuern
ORDER = {"ok": 0, "warn": 1, "error": 2}


@dataclass
class Check:
    key: str
    area: str  # Plattform | Agenten | Quellsysteme | NetBox-Sync | Sicherung
    status: str  # ok | warn | error
    title: str
    detail: str | None = None
    view: str | None = None  # Ansicht im UI (uebersicht | agenten | netbox-sync | system ...)
    since: datetime | None = None


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def _ago(value: datetime | None, now: datetime) -> str:
    if value is None:
        return L("nie", "never")
    minutes = int((now - value).total_seconds() // 60)
    if minutes < 60:
        return L(f"vor {minutes} min", f"{minutes} min ago")
    if minutes < 48 * 60:
        return L(f"vor {minutes // 60} h", f"{minutes // 60} h ago")
    return L(f"vor {minutes // (24 * 60)} Tagen", f"{minutes // (24 * 60)} days ago")


def checks(
    session: Session,
    settings: Settings,
    tenant_id: int | None,
    database_ok: bool,
    now: datetime | None = None,
) -> list[Check]:
    now = now or datetime.now(UTC)
    out = [_database(database_ok), _db_role(session), _internal_tls(session, settings, now)]
    out += [_scheduler(session, settings, now), _central(session, now)]
    out += _agents(session, tenant_id, now)
    out += _sources(session, settings, tenant_id, now)
    out += _netbox_sync(session, tenant_id, now)
    out.append(_backup(settings, now))
    out.append(_retention(session, settings, now))
    return [c for c in out if c is not None]


def summary(items: list[Check]) -> dict:
    worst = max((ORDER[c.status] for c in items), default=0)
    return {
        "status": next(k for k, v in ORDER.items() if v == worst),
        "problems": sum(1 for c in items if c.status != "ok"),
        "checks": [asdict(c) for c in items],
    }


def _database(ok: bool) -> Check:
    if ok:
        return Check(
            "database",
            "Plattform",
            "ok",
            L(
                "Datenbank erreichbar, Migrationen aktuell",
                "Database reachable, migrations current",
            ),
        )
    return Check(
        "database",
        "Plattform",
        "error",
        L("Datenbank-Migrationen nicht aktuell", "Database migrations not current"),
        L(
            "Orchestrator neu starten (führt `alembic upgrade head` aus) bzw. deploy/update.sh",
            "Restart the orchestrator (runs `alembic upgrade head`) or deploy/update.sh",
        ),
        "system",
    )


def _db_role(session: Session) -> Check | None:
    """Getrennte DB-Rollen (0.48.0): arbeitet der Orchestrator ohne DDL?"""
    try:
        role = db_roles.describe(session)
    except Exception:  # noqa: BLE001 - Anzeige; die Datenbank-Pruefung meldet Ausfaelle
        session.rollback()
        return None
    if role is None:
        return None  # nicht PostgreSQL (Tests)
    if role["separated"]:
        return Check(
            "db_role",
            "Plattform",
            "ok",
            L(
                f"Datenbank-Rolle {role['role']} ohne DDL",
                f"Database role {role['role']} without DDL",
            ),
        )
    what = (
        "Superuser"
        if role["superuser"]
        else L("Eigentümer der Tabellen bzw. mit CREATE", "owner of the tables or with CREATE")
    )
    return Check(
        "db_role",
        "Plattform",
        "warn",
        L(
            f"Orchestrator arbeitet als {role['role']} ({what})",
            f"Orchestrator runs as {role['role']} ({what})",
        ),
        L(
            "getrennte Rollen einrichten: deploy/aufbau.sh db-rollen (Migration als Eigentümer, "
            "Betrieb ohne DDL)",
            "set up separate roles: deploy/aufbau.sh db-rollen (migration as owner, "
            "operation without DDL)",
        ),
        "system",
    )


def _internal_tls(session: Session, settings: Settings, now: datetime) -> Check | None:
    """TLS intern (0.49.0): eingeschaltet, Datenbank verschluesselt, Zertifikat gueltig?"""
    try:
        secure = tls.db_tls(session)
    except Exception:  # noqa: BLE001 - Anzeige; die Datenbank-Pruefung meldet Ausfaelle
        session.rollback()
        return None
    if secure is None:
        return None  # nicht PostgreSQL (Tests)
    hint = "deploy/aufbau.sh tls-intern"
    if not settings.internal_tls:
        return Check("internal_tls", "Plattform", "warn",
                     L("Verbindungen zwischen den Containern unverschlüsselt",
                       "Connections between the containers unencrypted"),
                     L(f"TLS intern mit eigener CA einschalten: {hint}",
                       f"enable internal TLS with an own CA: {hint}"), "system")  # fmt: skip
    info = tls.cert_info(tls.server_files(settings)[0])
    if info is None:
        return Check("internal_tls", "Plattform", "error",
                     L("TLS intern: Zertifikat fehlt", "Internal TLS: certificate missing"),
                     L(f"Zertifikate ausstellen: {hint}", f"issue certificates: {hint}"),
                     "system")  # fmt: skip
    if not secure[0]:
        return Check("internal_tls", "Plattform", "error",
                     L("Datenbank-Verbindung ohne TLS", "Database connection without TLS"),
                     L("AUTOMATO_INTERNAL_TLS im Orchestrator prüfen",
                       "check AUTOMATO_INTERNAL_TLS in the orchestrator"), "system")  # fmt: skip
    left = info.days_left(now)
    until = f"{info.not_after:%d.%m.%Y}"
    renew = L(f"neu ausstellen: {hint} --erneuern", f"reissue: {hint} --erneuern")
    if left < 0:
        return Check("internal_tls", "Plattform", "error",
                     L(f"Zertifikat abgelaufen ({until})", f"Certificate expired ({until})"),
                     renew, "system")  # fmt: skip
    if left < CERT_WARN_DAYS:
        return Check("internal_tls", "Plattform", "warn",
                     L(f"Zertifikat läuft in {left} Tagen ab ({until})",
                       f"Certificate expires in {left} days ({until})"),
                     renew, "system")  # fmt: skip
    return Check("internal_tls", "Plattform", "ok",
                 L(f"TLS intern aktiv ({secure[1]}, Zertifikat bis {until})",
                   f"Internal TLS active ({secure[1]}, certificate until {until})"))  # fmt: skip


def _retention(session: Session, settings: Settings, now: datetime) -> Check | None:
    """Aufbewahrung (0.48.0): raeumt der Zeitplan alte Laeufe auf?"""
    policy = retention.Policy.of(settings)
    if not policy.active:
        if settings.environment == "dev":
            return None
        return Check("retention", "Plattform", "warn",
                     L("Keine Aufbewahrungsfristen gesetzt", "No retention periods set"),
                     L("AUTOMATO_RETENTION_RUNS_DAYS usw. > 0 (R14)",
                       "AUTOMATO_RETENTION_RUNS_DAYS etc. > 0 (R14)"), "system")  # fmt: skip
    last = retention.last_purge(session)
    at = last["at"] if last else None
    if (at is None or now - at > PURGE_LATE) and retention.overdue(session, policy, now):
        detail = (
            L(
                "Zeitplan aus – System → Aufbewahrung „Jetzt aufräumen“ bzw. automato purge",
                "Schedule off – System → Retention “Purge now” or automato purge",
            )
            if not settings.scheduler_enabled
            else L(
                f"Zeitplan räumt täglich auf, zuletzt {_ago(at, now)} – Log des Orchestrators",
                f"Schedule purges daily, last {_ago(at, now)} – orchestrator log",
            )
        )
        return Check("retention", "Plattform", "warn",
                     L("Alte Läufe über der Frist", "Old runs past the retention period"),
                     detail, "system", at)  # fmt: skip
    title = (
        L(f"zuletzt aufgeräumt {_ago(at, now)}", f"last purged {_ago(at, now)}")
        if at
        else L("nichts fällig", "nothing due")
    )
    return Check("retention", "Plattform", "ok", L(f"Aufbewahrung: {title}", f"Retention: {title}"),
                 since=at)  # fmt: skip


def _scheduler(session: Session, settings: Settings, now: datetime) -> Check | None:
    scheduled = session.scalars(
        select(SourceSystem.id).where(
            SourceSystem.schedule.is_not(None), SourceSystem.enabled.is_(True)
        )
    ).all()
    if not settings.scheduler_enabled:
        if not scheduled:
            return None
        return Check(
            "scheduler",
            "Plattform",
            "warn",
            L(
                f"Zeitplan ausgeschaltet – {len(scheduled)} Quellsysteme mit Zeitplan laufen nicht",
                f"Schedule switched off – {len(scheduled)} source systems with a schedule "
                "do not run",
            ),
            L("AUTOMATO_SCHEDULER_ENABLED=true setzen", "set AUTOMATO_SCHEDULER_ENABLED=true"),
            "system",
        )
    last = scheduler.last_tick()
    limit = timedelta(seconds=max(settings.scheduler_interval_seconds * 3, 120))
    if last is None or now - last > limit:
        return Check(
            "scheduler",
            "Plattform",
            "error",
            L("Zeitplan läuft nicht", "Schedule not running"),
            L(
                f"letzter Durchgang {_ago(last, now)} – Orchestrator-Log prüfen",
                f"last pass {_ago(last, now)} – check the orchestrator log",
            ),
            "system",
            last,
        )
    return Check(
        "scheduler",
        "Plattform",
        "ok",
        L(f"Zeitplan aktiv ({_ago(last, now)})", f"Schedule active ({_ago(last, now)})"),
        since=last,
    )


def _central(session: Session, now: datetime) -> Check | None:
    """Zentraler Worker: warten zentrale Laeufe laenger als CENTRAL_WAIT, holt ihn keiner ab."""
    waiting = session.scalars(
        select(CollectorRun)
        .where(
            CollectorRun.execution == "inprocess",
            CollectorRun.status == RunStatus.QUEUED,
            CollectorRun.queued_at < now - CENTRAL_WAIT,
        )
        .order_by(CollectorRun.queued_at)
    ).all()
    if not waiting:
        return None
    return Check(
        "worker",
        "Plattform",
        "error",
        L(
            f"Zentraler Worker holt keine Läufe ab ({len(waiting)} warten)",
            f"Central worker does not pick up runs ({len(waiting)} waiting)",
        ),
        L(
            f"ältester wartet seit {_ago(_aware(waiting[0].queued_at), now)} – Container "
            "„collector“ (Celery) und Redis prüfen",
            f"oldest waiting since {_ago(_aware(waiting[0].queued_at), now)} – check container "
            "“collector” (Celery) and Redis",
        ),
        "uebersicht",
        _aware(waiting[0].queued_at),
    )


def _agents(session: Session, tenant_id: int | None, now: datetime) -> list[Check]:
    stmt = select(Agent).where(Agent.enabled.is_(True))
    if tenant_id is not None:
        stmt = stmt.where((Agent.tenant_id == tenant_id) | Agent.tenant_id.is_(None))
    out = []
    for agent in session.scalars(stmt.order_by(Agent.name)):
        assigned = session.scalars(
            select(SourceSystem.id).where(
                SourceSystem.agent_id == agent.id, SourceSystem.enabled.is_(True)
            )
        ).all()
        seen = _aware(agent.last_seen)
        key = f"agent:{agent.id}"
        if assigned and (seen is None or now - seen > AGENT_OFFLINE):
            out.append(
                Check(
                    key,
                    "Agenten",
                    "error",
                    L(
                        f"Agent „{agent.name}“ meldet sich nicht",
                        f"Agent “{agent.name}” does not report",
                    ),
                    L(
                        f"zuletzt {_ago(seen, now)}; {len(assigned)} Geräte werden nicht gelesen "
                        "– geplante Aufgabe auf der Station prüfen (logs\\agent-*.log)",
                        f"last {_ago(seen, now)}; {len(assigned)} devices are not read – "
                        "check the scheduled task on the station (logs\\agent-*.log)",
                    ),
                    "agenten",
                    seen,
                )
            )
        elif agent.update_note:
            out.append(
                Check(
                    key,
                    "Agenten",
                    "warn",
                    L(
                        f"Agent „{agent.name}“ aktualisiert nicht",
                        f"Agent “{agent.name}” does not update",
                    ),
                    runs.show_message(agent.update_note),  # gespeichert deutsch (ADR 0028)
                    "agenten",
                    seen,
                )  # fmt: skip
            )
        elif assigned:
            out.append(
                Check(
                    key,
                    "Agenten",
                    "ok",
                    L(
                        f"Agent „{agent.name}“ aktiv ({_ago(seen, now)})",
                        f"Agent “{agent.name}” active ({_ago(seen, now)})",
                    ),
                    view="agenten",
                    since=seen,
                )  # fmt: skip
            )
    return out


def _sources(
    session: Session, settings: Settings, tenant_id: int | None, now: datetime
) -> list[Check]:
    """Je Quellsystem: nie eingelesen, letzter Lauf fehlgeschlagen bzw. Stand veraltet - die
    Grenze wie beim Befund `source_stale` (`data_quality.stale_limit`, ADR 0029)."""
    stmt = select(SourceSystem).where(SourceSystem.enabled.is_(True))
    if tenant_id is not None:
        stmt = stmt.where(SourceSystem.tenant_id == tenant_id)
    out = []
    for src in session.scalars(stmt.order_by(SourceSystem.name)):
        last = session.scalars(
            select(CollectorRun)
            .where(
                CollectorRun.source_system_id == src.id,
                CollectorRun.status.in_((RunStatus.COMPLETED, RunStatus.FAILED)),
            )
            .order_by(CollectorRun.queued_at.desc())
            .limit(1)
        ).first()
        good = session.scalars(
            select(CollectorRun.finished_at)
            .where(
                CollectorRun.source_system_id == src.id,
                CollectorRun.status == RunStatus.COMPLETED,
            )
            .order_by(CollectorRun.queued_at.desc())
            .limit(1)
        ).first()
        good = _aware(good)
        key = f"source:{src.id}"
        if last is None:
            out.append(Check(key, "Quellsysteme", "warn",
                             L(f"{src.name}: noch nie eingelesen", f"{src.name}: never collected"),
                             view="uebersicht"))  # fmt: skip
        elif last.status is RunStatus.FAILED:
            out.append(
                Check(
                    key,
                    "Quellsysteme",
                    "error",
                    L(f"{src.name}: letzter Lauf fehlgeschlagen", f"{src.name}: last run failed"),
                    (runs.show_message(last.message) or "")[:300] or None,
                    "uebersicht",
                    _aware(last.finished_at),
                )
            )
        elif good is not None and _stale(src, settings, now, good):
            out.append(
                Check(
                    key,
                    "Quellsysteme",
                    "warn",
                    L(f"{src.name}: Stand veraltet", f"{src.name}: data outdated"),
                    L(f"zuletzt eingelesen {_ago(good, now)}", f"last collected {_ago(good, now)}"),
                    "uebersicht",
                    good,
                )  # fmt: skip
            )
        else:
            out.append(
                Check(
                    key,
                    "Quellsysteme",
                    "ok",
                    L(
                        f"{src.name}: eingelesen {_ago(good, now)}",
                        f"{src.name}: collected {_ago(good, now)}",
                    ),
                    view="uebersicht",
                    since=good,
                )  # fmt: skip
            )
    return out


def _stale(src: SourceSystem, settings: Settings, now: datetime, good: datetime) -> bool:
    limit, _period = data_quality.stale_limit(src, settings, now)
    return limit is not None and now - good > limit


def _netbox_sync(session: Session, tenant_id: int | None, now: datetime) -> list[Check]:
    stmt = select(SyncPlan).order_by(SyncPlan.id.desc()).limit(50)
    if tenant_id is not None:
        stmt = stmt.where(SyncPlan.tenant_id == tenant_id)
    latest: dict[int, SyncPlan] = {}
    for plan in session.scalars(stmt):
        latest.setdefault(plan.source_system_id, plan)
    out = []
    for plan in latest.values():
        when = _aware(plan.applied_at) or _aware(plan.created_at)
        if plan.status == SyncStatus.FAILED.value and when and now - when < SYNC_RECENT:
            out.append(
                Check(
                    f"sync:{plan.source_system_id}",
                    "NetBox-Sync",
                    "warn",
                    L(f"NetBox-Sync #{plan.id} fehlgeschlagen", f"NetBox sync #{plan.id} failed"),
                    (plan.message or "")[:300] or None,
                    "netbox-sync",
                    when,
                )
            )
    return out


def _backup(settings: Settings, now: datetime) -> Check | None:
    try:
        data = json.loads(Path(settings.backup_status_file).read_text(encoding="utf-8"))
        at = datetime.fromisoformat(str(data.get("at")))
    except (OSError, ValueError, TypeError):
        if settings.environment == "dev":
            return None
        return Check("backup", "Sicherung", "warn", L("Keine Sicherung bekannt", "No backup known"),
                     L("deploy/backup/backup.sh täglich ausführen",
                       "run deploy/backup/backup.sh daily"), "system")  # fmt: skip
    at = _aware(at)
    if now - at > BACKUP_MAX_AGE:
        return Check("backup", "Sicherung", "warn",
                     L("Letzte Sicherung zu alt", "Last backup too old"),
                     L(f"zuletzt {_ago(at, now)}", f"last {_ago(at, now)}"), "system",
                     at)  # fmt: skip
    return Check("backup", "Sicherung", "ok",
                 L(f"Sicherung {_ago(at, now)}", f"Backup {_ago(at, now)}"), since=at)  # fmt: skip
