"""Aufbewahrung (0.48.0, R14: „alte Laeufe/Zwischenstaende mit Frist loeschen“).

Weniger speichern heisst auch: nicht ewig. Je Art eine Frist in Tagen (0 = nie loeschen):

- **Laeufe** (`retention_runs_days`, 90): beendete Collector-Laeufe (fertig bzw. fehlgeschlagen)
  samt liegengebliebenen Teil-Uploads der Referenzquellen. Nie geloescht: offene Laeufe, je
  Quellsystem der neuste und der neuste erfolgreiche Lauf und jeder Lauf, auf den der aktuelle
  Stand zeigt (Regeln, Objekte, NAT, VMs, Referenzstand der Quelle).
- **NetBox-Syncs** (`retention_sync_days`, 90): Vorschauen mit ihren Aktionen; nie die neuste je
  NetBox und keine, die gerade rechnet bzw. uebernimmt. Was uebernommen wurde, steht weiter im
  Audit (`netbox_sync.applied` mit den Zahlen).
- **Audit** (`retention_audit_days`, 365): Eintraege aelter als die Frist.

`purge` loescht und schreibt selbst einen Audit-Eintrag `retention.purged` mit den Zahlen - der
neuste ist zugleich „zuletzt aufgeraeumt“. Der Zeitplan ruft `maybe_purge` auf (hoechstens einmal
in `INTERVAL`), von Hand `POST /system/retention/purge` bzw. `automato purge`.
"""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app import audit
from app.models import (
    AuditLog,
    CollectorRun,
    DeviceObject,
    NatRule,
    ReferenceRecordRow,
    Rule,
    RunStatus,
    SourceSystem,
    SyncPlan,
    SyncStatus,
    Vm,
)

ACTION = "retention.purged"
ACTOR = "system:retention"
INTERVAL = timedelta(hours=24)
FINISHED = (RunStatus.COMPLETED, RunStatus.FAILED)
BUSY_SYNC = (SyncStatus.PLANNING.value, SyncStatus.APPLYING.value)
CHUNK = 500


@dataclass(frozen=True)
class Policy:
    runs_days: int
    sync_days: int
    audit_days: int

    @classmethod
    def of(cls, settings) -> "Policy":
        return cls(
            max(settings.retention_runs_days, 0),
            max(settings.retention_sync_days, 0),
            max(settings.retention_audit_days, 0),
        )

    @property
    def active(self) -> bool:
        return any((self.runs_days, self.sync_days, self.audit_days))


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def _cutoff(days: int, now: datetime) -> datetime | None:
    return now - timedelta(days=days) if days > 0 else None


def _latest_runs(completed: bool):
    """Je Quellsystem der neuste (bzw. neuste erfolgreiche) Lauf."""
    latest = select(
        CollectorRun.source_system_id, func.max(CollectorRun.queued_at).label("at")
    ).where(CollectorRun.source_system_id.is_not(None))
    if completed:
        latest = latest.where(CollectorRun.status == RunStatus.COMPLETED)
    sub = latest.group_by(CollectorRun.source_system_id).subquery()
    stmt = select(CollectorRun.id).join(
        sub,
        and_(
            CollectorRun.source_system_id == sub.c.source_system_id,
            CollectorRun.queued_at == sub.c.at,
        ),
    )
    return stmt.where(CollectorRun.status == RunStatus.COMPLETED) if completed else stmt


def _in_use():
    """Laeufe, auf die der aktuelle Stand zeigt."""
    used = [
        select(model.collector_run_id).where(model.collector_run_id.is_not(None))
        for model in (Rule, DeviceObject, NatRule, Vm)
    ]
    used.append(
        select(SourceSystem.reference_run_id).where(SourceSystem.reference_run_id.is_not(None))
    )
    return used


def _old_runs(session: Session, cutoff: datetime) -> list[str]:
    when = func.coalesce(CollectorRun.finished_at, CollectorRun.queued_at)
    stmt = select(CollectorRun.id).where(CollectorRun.status.in_(FINISHED), when < cutoff)
    for keep in (*_in_use(), _latest_runs(False), _latest_runs(True)):
        stmt = stmt.where(CollectorRun.id.not_in(keep))
    return list(session.scalars(stmt))


def _old_plans(session: Session, cutoff: datetime) -> list[int]:
    newest = select(func.max(SyncPlan.id)).group_by(SyncPlan.source_system_id)
    stmt = select(SyncPlan.id).where(
        SyncPlan.created_at < cutoff,
        SyncPlan.status.not_in(BUSY_SYNC),
        SyncPlan.id.not_in(newest),
    )
    return list(session.scalars(stmt))


def _old_audit(session: Session, cutoff: datetime) -> int:
    stmt = select(func.count()).select_from(AuditLog).where(AuditLog.timestamp < cutoff)
    return session.scalar(stmt) or 0


def _chunks(ids: list, size: int = CHUNK):
    for i in range(0, len(ids), size):
        yield ids[i : i + size]


def due(session: Session, policy: Policy, now: datetime | None = None) -> dict[str, int]:
    """Was jetzt geloescht wuerde (je Art die Anzahl)."""
    now = now or datetime.now(UTC)
    runs = _cutoff(policy.runs_days, now)
    plans = _cutoff(policy.sync_days, now)
    entries = _cutoff(policy.audit_days, now)
    return {
        "runs": len(_old_runs(session, runs)) if runs else 0,
        "sync_plans": len(_old_plans(session, plans)) if plans else 0,
        "audit": _old_audit(session, entries) if entries else 0,
    }


def purge(
    session: Session, policy: Policy, actor: str = ACTOR, now: datetime | None = None
) -> dict[str, int]:
    """Loeschen, was ueber der Frist ist; Rueckgabe = Anzahl je Art (Aufrufer committet)."""
    now = now or datetime.now(UTC)
    counts = {"runs": 0, "sync_plans": 0, "audit": 0}
    if cutoff := _cutoff(policy.runs_days, now):
        ids = _old_runs(session, cutoff)
        for chunk in _chunks(ids):
            # Teil-Uploads loeschen (in Postgres auch per ON DELETE CASCADE)
            session.execute(
                delete(ReferenceRecordRow).where(ReferenceRecordRow.collector_run_id.in_(chunk))
            )
            session.execute(delete(CollectorRun).where(CollectorRun.id.in_(chunk)))
        counts["runs"] = len(ids)
    if cutoff := _cutoff(policy.sync_days, now):
        ids = _old_plans(session, cutoff)
        for chunk in _chunks(ids):
            session.execute(delete(SyncPlan).where(SyncPlan.id.in_(chunk)))
        counts["sync_plans"] = len(ids)
    if cutoff := _cutoff(policy.audit_days, now):
        counts["audit"] = _old_audit(session, cutoff)
        session.execute(delete(AuditLog).where(AuditLog.timestamp < cutoff))
    audit.record(
        session,
        actor=actor,
        action=ACTION,
        subject="retention",
        detail={
            **counts,
            "runs_days": policy.runs_days,
            "sync_days": policy.sync_days,
            "audit_days": policy.audit_days,
        },
    )
    return counts


def last_purge(session: Session) -> dict | None:
    """Zuletzt aufgeraeumt: Zeitpunkt, wer und die Zahlen (aus dem Audit)."""
    row = session.scalars(
        select(AuditLog).where(AuditLog.action == ACTION).order_by(AuditLog.timestamp.desc())
    ).first()
    if row is None:
        return None
    detail = audit.decode_detail(row) or {}
    counts = {k: detail.get(k, 0) for k in ("runs", "sync_plans", "audit")}
    return {"at": _aware(row.timestamp), "actor": row.actor, "counts": counts}


def overdue(session: Session, policy: Policy, now: datetime | None = None) -> bool:
    """Gibt es beendete Laeufe ueber der Frist? (guenstig, fuer den Betriebszustand)"""
    now = now or datetime.now(UTC)
    cutoff = _cutoff(policy.runs_days, now)
    if cutoff is None:
        return False
    when = func.coalesce(CollectorRun.finished_at, CollectorRun.queued_at)
    stmt = select(CollectorRun.id).where(CollectorRun.status.in_(FINISHED), when < cutoff)
    return session.scalars(stmt.limit(1)).first() is not None


def maybe_purge(factory: sessionmaker, settings, now: datetime | None = None) -> dict | None:
    """Vom Zeitplan: aufraeumen, wenn zuletzt vor mehr als `INTERVAL` (bzw. nie)."""
    policy = Policy.of(settings)
    if not policy.active:
        return None
    now = now or datetime.now(UTC)
    with factory() as session:
        last = last_purge(session)
        if last is not None and now - last["at"] < INTERVAL:
            return None
        counts = purge(session, policy, now=now)
        session.commit()
        return counts


def status(session: Session, settings, now: datetime | None = None) -> dict:
    """Fristen, was faellig ist und wann zuletzt aufgeraeumt wurde (System-Seite)."""
    policy = Policy.of(settings)
    return {
        "policy": asdict(policy),
        "active": policy.active,
        "scheduled": bool(settings.scheduler_enabled),
        "due": due(session, policy, now),
        "last": last_purge(session),
    }
