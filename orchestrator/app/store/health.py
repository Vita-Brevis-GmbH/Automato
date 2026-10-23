"""Betriebszustand (Dev-Befund 0.12.0: "wo laufen Dienste nicht?") - Ampel je Pruefung.

Nur lesend und aus dem, was Automato ohnehin weiss: Datenbank/Migration, Zeitplan (letzter
Durchgang), zentraler Worker (holt er zentrale Laeufe ab?), Agenten (zuletzt gemeldet,
Update-Probleme), Quellsysteme (letzter Lauf fehlgeschlagen bzw. zu alt), NetBox-Sync und
Sicherung, seit 0.48.0 die DB-Rolle (ohne DDL?) und die Aufbewahrung (zuletzt aufgeraeumt). Je
Pruefung `ok` | `warn` | `error` mit Satz und Ziel-Ansicht im UI.
"""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import scheduler
from app.config import Settings
from app.models import (
    Agent,
    CollectorRun,
    RunStatus,
    SourceSystem,
    SyncPlan,
    SyncStatus,
)
from app.store import db_roles, retention

AGENT_OFFLINE = timedelta(minutes=30)  # Wach-Modus fragt alle 20 s, Neustart alle 15 min
SOURCE_STALE = timedelta(days=7)
CENTRAL_WAIT = timedelta(minutes=10)
BACKUP_MAX_AGE = timedelta(hours=26)
SYNC_RECENT = timedelta(hours=24)
PURGE_LATE = timedelta(days=2)  # Zeitplan raeumt taeglich auf
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
        return "nie"
    minutes = int((now - value).total_seconds() // 60)
    if minutes < 60:
        return f"vor {minutes} min"
    if minutes < 48 * 60:
        return f"vor {minutes // 60} h"
    return f"vor {minutes // (24 * 60)} Tagen"


def checks(
    session: Session,
    settings: Settings,
    tenant_id: int | None,
    database_ok: bool,
    now: datetime | None = None,
) -> list[Check]:
    now = now or datetime.now(UTC)
    out = [_database(database_ok), _db_role(session)]
    out += [_scheduler(session, settings, now), _central(session, now)]
    out += _agents(session, tenant_id, now)
    out += _sources(session, tenant_id, now)
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
        return Check("database", "Plattform", "ok", "Datenbank erreichbar, Migrationen aktuell")
    return Check(
        "database",
        "Plattform",
        "error",
        "Datenbank-Migrationen nicht aktuell",
        "Orchestrator neu starten (führt `alembic upgrade head` aus) bzw. deploy/update.sh",
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
        return Check("db_role", "Plattform", "ok", f"Datenbank-Rolle {role['role']} ohne DDL")
    what = "Superuser" if role["superuser"] else "Eigentümer der Tabellen bzw. mit CREATE"
    return Check(
        "db_role",
        "Plattform",
        "warn",
        f"Orchestrator arbeitet als {role['role']} ({what})",
        "getrennte Rollen einrichten: deploy/aufbau.sh db-rollen (Migration als Eigentümer, "
        "Betrieb ohne DDL)",
        "system",
    )


def _retention(session: Session, settings: Settings, now: datetime) -> Check | None:
    """Aufbewahrung (0.48.0): raeumt der Zeitplan alte Laeufe auf?"""
    policy = retention.Policy.of(settings)
    if not policy.active:
        if settings.environment == "dev":
            return None
        return Check("retention", "Plattform", "warn", "Keine Aufbewahrungsfristen gesetzt",
                     "AUTOMATO_RETENTION_RUNS_DAYS usw. > 0 (R14)", "system")  # fmt: skip
    last = retention.last_purge(session)
    at = last["at"] if last else None
    if (at is None or now - at > PURGE_LATE) and retention.overdue(session, policy, now):
        detail = (
            "Zeitplan aus – System → Aufbewahrung „Jetzt aufräumen“ bzw. automato purge"
            if not settings.scheduler_enabled
            else f"Zeitplan räumt täglich auf, zuletzt {_ago(at, now)} – Log des Orchestrators"
        )
        return Check("retention", "Plattform", "warn", "Alte Läufe über der Frist", detail,
                     "system", at)  # fmt: skip
    title = f"zuletzt aufgeräumt {_ago(at, now)}" if at else "nichts fällig"
    return Check("retention", "Plattform", "ok", f"Aufbewahrung: {title}", since=at)


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
            f"Zeitplan ausgeschaltet – {len(scheduled)} Quellsysteme mit Zeitplan laufen nicht",
            "AUTOMATO_SCHEDULER_ENABLED=true setzen",
            "system",
        )
    last = scheduler.last_tick()
    limit = timedelta(seconds=max(settings.scheduler_interval_seconds * 3, 120))
    if last is None or now - last > limit:
        return Check(
            "scheduler",
            "Plattform",
            "error",
            "Zeitplan läuft nicht",
            f"letzter Durchgang {_ago(last, now)} – Orchestrator-Log prüfen",
            "system",
            last,
        )
    return Check("scheduler", "Plattform", "ok", f"Zeitplan aktiv ({_ago(last, now)})", since=last)


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
        f"Zentraler Worker holt keine Läufe ab ({len(waiting)} warten)",
        f"ältester wartet seit {_ago(_aware(waiting[0].queued_at), now)} – Container "
        "„collector“ (Celery) und Redis prüfen",
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
                    f"Agent „{agent.name}“ meldet sich nicht",
                    f"zuletzt {_ago(seen, now)}; {len(assigned)} Geräte werden nicht gelesen – "
                    "geplante Aufgabe auf der Station prüfen (logs\\agent-*.log)",
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
                    f"Agent „{agent.name}“ aktualisiert nicht",
                    agent.update_note,
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
                    f"Agent „{agent.name}“ aktiv ({_ago(seen, now)})",
                    view="agenten",
                    since=seen,
                )  # fmt: skip
            )
    return out


def _sources(session: Session, tenant_id: int | None, now: datetime) -> list[Check]:
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
            out.append(Check(key, "Quellsysteme", "warn", f"{src.name}: noch nie eingelesen",
                             view="uebersicht"))  # fmt: skip
        elif last.status is RunStatus.FAILED:
            out.append(
                Check(
                    key,
                    "Quellsysteme",
                    "error",
                    f"{src.name}: letzter Lauf fehlgeschlagen",
                    (last.message or "")[:300] or None,
                    "uebersicht",
                    _aware(last.finished_at),
                )
            )
        elif good is not None and now - good > SOURCE_STALE:
            out.append(
                Check(
                    key,
                    "Quellsysteme",
                    "warn",
                    f"{src.name}: Stand veraltet",
                    f"zuletzt eingelesen {_ago(good, now)}",
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
                    f"{src.name}: eingelesen {_ago(good, now)}",
                    view="uebersicht",
                    since=good,
                )  # fmt: skip
            )
    return out


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
                    f"NetBox-Sync #{plan.id} fehlgeschlagen",
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
        return Check("backup", "Sicherung", "warn", "Keine Sicherung bekannt",
                     "deploy/backup/backup.sh täglich ausführen", "system")  # fmt: skip
    at = _aware(at)
    if now - at > BACKUP_MAX_AGE:
        return Check("backup", "Sicherung", "warn", "Letzte Sicherung zu alt",
                     f"zuletzt {_ago(at, now)}", "system", at)  # fmt: skip
    return Check("backup", "Sicherung", "ok", f"Sicherung {_ago(at, now)}", since=at)
