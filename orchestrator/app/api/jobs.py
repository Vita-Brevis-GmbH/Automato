"""Job-Endpunkte: Collector-Laeufe anstossen und ihren Status abfragen."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.auth.deps import require_operator, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.jobs import celery_app
from app.models import CollectorRun, SourceSystem
from app.schemas.jobs import CollectJobRequest, CollectJobResponse, CollectorRunOut

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/collect", response_model=CollectJobResponse, status_code=202)
def start_collect(
    payload: CollectJobRequest,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session),
):
    tenant_id = tenant_scope(principal, payload.tenant_id)  # Mandanten-Zugriff erzwingen
    if payload.source_system_id is not None:
        src = session.get(SourceSystem, payload.source_system_id)
        if src is None:
            raise HTTPException(status_code=422, detail="source system not found")
        if src.tenant_id is None or src.tenant_id != tenant_id:
            raise HTTPException(status_code=422, detail="source system belongs to another tenant")
        if src.platform.value != payload.platform:
            raise HTTPException(status_code=422, detail="platform does not match source system")
        if src.agent_id is not None:
            payload.execution = "external"  # Geraet wird von seinem Agenten gelesen (ADR 0009)
    run = CollectorRun(
        platform=payload.platform,
        tenant_id=tenant_id,
        source_system_id=payload.source_system_id,
        execution=payload.execution,
    )
    session.add(run)
    session.flush()  # run.id verfuegbar machen

    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="collector_run.queued",
        subject=run.id,
        detail={
            "platform": payload.platform,
            "tenant_id": tenant_id,
            "source_system_id": payload.source_system_id,
            "execution": payload.execution,
        },
    )
    # inprocess: zentraler Worker. external: externer Collector holt den Lauf per RUN_ID ab
    # und liefert via /internal ab (ADR 0008) - hier wird NICHT dispatcht.
    if payload.execution == "inprocess":
        celery_app.dispatch_collect(payload.platform, run.id)
    return CollectJobResponse(run_id=run.id, status=run.status, execution=run.execution)


def _run_out(run: CollectorRun) -> CollectorRunOut:
    return CollectorRunOut(
        id=run.id,
        platform=run.platform,
        status=run.status,
        tenant_id=run.tenant_id,
        source_system_id=run.source_system_id,
        rule_count=run.rule_count,
        message=run.message,
        execution=run.execution,
        queued_at=run.queued_at,
        finished_at=run.finished_at,
    )


@router.get("", response_model=list[CollectorRunOut])
def list_runs(
    tenant_id: int | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session),
):
    """Letzte Laeufe (neueste zuerst). Nicht-Admin sieht nur Laeufe seiner Mandanten."""
    query = select(CollectorRun).order_by(CollectorRun.queued_at.desc()).limit(limit)
    if tenant_id is not None:
        tenant_scope(principal, tenant_id)  # fremder Mandant -> 403
        query = query.where(CollectorRun.tenant_id == tenant_id)
    elif not principal.is_admin and principal.tenant_ids is not None:
        query = query.where(CollectorRun.tenant_id.in_(principal.tenant_ids))
    return [_run_out(r) for r in session.scalars(query)]


@router.get("/{run_id}", response_model=CollectorRunOut)
def get_run(
    run_id: str,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session),
):
    run = session.get(CollectorRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    tenant_scope(principal, run.tenant_id)  # Mandanten-Zugriff pruefen
    return _run_out(run)
