"""Collector-Lauf anlegen - gemeinsam fuer "Jetzt einlesen" (API/CLI) und den Zeitplan.

Prueft Quellsystem, Mandant, Plattform und (bei Referenzquellen) die Lese-Zugangsdaten;
ein Geraet mit Agent wird immer extern gelesen (ADR 0009). Auditiert `collector_run.queued`.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, credentials
from app.models import REFERENCE_PLATFORMS, CollectorRun, RunStatus, SourceSystem


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


def busy(session: Session, source_system_id: int) -> bool:
    """Laeuft oder wartet schon ein Lauf fuer dieses Quellsystem?"""
    stmt = select(CollectorRun.id).where(
        CollectorRun.source_system_id == source_system_id,
        CollectorRun.status.in_((RunStatus.QUEUED, RunStatus.RUNNING)),
    )
    return session.scalars(stmt.limit(1)).first() is not None
