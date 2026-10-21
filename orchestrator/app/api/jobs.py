"""Job-Endpunkte: Collector-Laeufe anstossen und ihren Status abfragen."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import require_operator, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.jobs import celery_app
from app.models import REFERENCE_PLATFORMS, CollectorRun, SourceSystem
from app.schemas.jobs import (
    CollectAllItem,
    CollectAllRequest,
    CollectAllResponse,
    CollectJobRequest,
    CollectJobResponse,
    CollectorRunOut,
)
from app.store import runs

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/collect", response_model=CollectJobResponse, status_code=202)
def start_collect(
    payload: CollectJobRequest,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
):
    tenant_id = tenant_scope(principal, payload.tenant_id)  # Mandanten-Zugriff erzwingen
    try:
        run = runs.queue_collect(
            session,
            platform=payload.platform,
            tenant_id=tenant_id,
            source_system_id=payload.source_system_id,
            execution=payload.execution,
            actor=f"user:{principal.subject}",
        )
    except runs.RunRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # inprocess: zentraler Worker. external: externer Collector holt den Lauf per RUN_ID ab
    # und liefert via /internal ab (ADR 0008) - hier wird NICHT dispatcht.
    if run.execution == "inprocess":
        celery_app.dispatch_collect(payload.platform, run.id)
    return CollectJobResponse(run_id=run.id, status=run.status, execution=run.execution)


@router.post("/collect-all", response_model=CollectAllResponse, status_code=202)
def start_collect_all(
    payload: CollectAllRequest,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
):
    """Alle aktiven Quellsysteme des Mandanten einlesen (Referenzquellen zuerst). Systeme,
    die schon laufen/warten oder nicht lesbar sind (z. B. ohne Lese-Zugang), werden mit
    Grund uebersprungen. Rein lesend gegenueber den Geraeten - wie "Jetzt einlesen"."""
    tenant_id = tenant_scope(principal, payload.tenant_id)
    sources = sorted(
        session.scalars(
            select(SourceSystem).where(
                SourceSystem.tenant_id == tenant_id, SourceSystem.enabled.is_(True)
            )
        ),
        key=lambda s: (s.platform not in REFERENCE_PLATFORMS, s.name.lower()),
    )
    items: list[CollectAllItem] = []
    for src in sources:
        item = CollectAllItem(source_system_id=src.id, name=src.name, platform=src.platform.value)
        items.append(item)
        if runs.busy(session, src.id):
            item.skipped = "Lauf läuft bzw. wartet bereits"
            continue
        try:
            run = runs.queue_collect(
                session,
                platform=src.platform.value,
                tenant_id=tenant_id,
                source_system_id=src.id,
                execution="inprocess",  # mit Agent wird daraus "external" (ADR 0009)
                actor=f"user:{principal.subject}",
            )
        except runs.RunRejected as exc:
            item.skipped = str(exc)
            continue
        item.run_id, item.execution = run.id, run.execution
        if run.execution == "inprocess":
            celery_app.dispatch_collect(run.platform, run.id)
    return CollectAllResponse(queued=sum(1 for i in items if i.run_id), items=items)


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
        trigger=run.trigger,
        queued_at=run.queued_at,
        finished_at=run.finished_at,
    )


@router.get("", response_model=list[CollectorRunOut])
def list_runs(
    tenant_id: int | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
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
    session: Session = Depends(get_session, scope="function"),
):
    run = session.get(CollectorRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    tenant_scope(principal, run.tenant_id)  # Mandanten-Zugriff pruefen
    return _run_out(run)
