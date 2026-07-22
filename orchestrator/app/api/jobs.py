"""Job-Endpunkte: Collector-Laeufe anstossen und ihren Status abfragen."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import audit
from app.db import get_session
from app.jobs import celery_app
from app.models import CollectorRun
from app.schemas.jobs import CollectJobRequest, CollectJobResponse, CollectorRunOut

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/collect", response_model=CollectJobResponse, status_code=202)
def start_collect(payload: CollectJobRequest, session: Session = Depends(get_session)):
    run = CollectorRun(
        platform=payload.platform,
        tenant_id=payload.tenant_id,
        source_system_id=payload.source_system_id,
    )
    session.add(run)
    session.flush()  # run.id verfuegbar machen

    # TODO(M3/M4): actor aus AuthN uebernehmen, sobald Auth umgesetzt ist
    audit.record(
        session,
        actor="api",
        action="collector_run.queued",
        subject=run.id,
        detail={
            "platform": payload.platform,
            "tenant_id": payload.tenant_id,
            "source_system_id": payload.source_system_id,
        },
    )
    celery_app.dispatch_collect(payload.platform, run.id)
    return CollectJobResponse(run_id=run.id, status=run.status)


@router.get("/{run_id}", response_model=CollectorRunOut)
def get_run(run_id: str, session: Session = Depends(get_session)):
    run = session.get(CollectorRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return CollectorRunOut(
        id=run.id,
        platform=run.platform,
        status=run.status,
        tenant_id=run.tenant_id,
        source_system_id=run.source_system_id,
        rule_count=run.rule_count,
        message=run.message,
    )
