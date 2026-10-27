"""Job-Endpunkte: Collector-Laeufe anstossen und ihren Status abfragen."""

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import require_operator, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.i18n import L
from app.jobs import celery_app
from app.models import REFERENCE_PLATFORMS, CollectorRun, RunStatus, SourceSystem
from app.schemas.jobs import (
    CollectAllItem,
    CollectAllRequest,
    CollectAllResponse,
    CollectJobRequest,
    CollectJobResponse,
    CollectorRunOut,
)
from app.store import runs, tenant_lock

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
    runs.expire_stale(session)  # haengende Laeufe blockieren nicht mehr (Dev-Befund 0.11.3)
    for src in sources:
        item = CollectAllItem(source_system_id=src.id, name=src.name, platform=src.platform.value)
        items.append(item)
        open_run = runs.blocking(session, src.id)
        if open_run is not None:
            item.skipped = runs.describe(session, open_run)
            item.blocking_run_id = open_run.id
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


def _message(run: CollectorRun) -> str | None:
    """Meldung des Laufs; ist sein Ergebnis angekommen, aber noch nicht eingespielt (0.80.1:
    eines nach dem anderen), steht das statt „läuft“ da. Nur Antwort (folgt der Sprache); die
    gespeicherte Meldung `run.message` bleibt deutsch und wird beim Abruf uebersetzt (0.88.0)."""
    state = tenant_lock.state(run.id) if run.status in runs.OPEN else None
    if state == "active":
        return L("Ergebnis angekommen – wird eingespielt", "Result arrived – being ingested")
    if state == "waiting":
        ahead = tenant_lock.waiting_before(run.id)
        return L(
            f"Ergebnis angekommen – wartet aufs Einspielen ({ahead} davor)",
            f"Result arrived – waiting to be ingested ({ahead} ahead)",
        )
    return runs.show_message(run.message)


def _run_out(run: CollectorRun) -> CollectorRunOut:
    return CollectorRunOut(
        id=run.id,
        platform=run.platform,
        status=run.status,
        tenant_id=run.tenant_id,
        source_system_id=run.source_system_id,
        rule_count=run.rule_count,
        message=_message(run),
        execution=run.execution,
        trigger=run.trigger,
        queued_at=run.queued_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


@router.get("", response_model=list[CollectorRunOut])
def list_runs(
    response: Response,
    tenant_id: int | None = None,
    status: str | None = Query(
        None, description="kommagetrennt: queued, running, completed, failed; open = wartet+laeuft"
    ),
    source_system_id: int | None = None,
    platform: str | None = None,
    trigger: str | None = Query(None, pattern="^(manual|schedule)$"),
    limit: int = Query(default=20, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    """Laeufe seitenweise (neueste zuerst), Gesamtzahl im Header `X-Total-Count`. Nicht-Admin
    sieht nur Laeufe seiner Mandanten. Filter Status (auch `open`), Quellsystem, Plattform,
    Ausloeser (0.13.0: alle Laeufe, nicht nur die letzten)."""
    runs.expire_quietly(session)  # haengende Laeufe nicht als "laeuft" anzeigen
    conds = []
    if tenant_id is not None:
        tenant_scope(principal, tenant_id)  # fremder Mandant -> 403
        conds.append(CollectorRun.tenant_id == tenant_id)
    elif not principal.is_admin and principal.tenant_ids is not None:
        conds.append(CollectorRun.tenant_id.in_(principal.tenant_ids))
    if status:
        wanted = set()
        for s in status.split(","):
            s = s.strip()
            wanted |= {"queued", "running"} if s == "open" else {s}
        conds.append(CollectorRun.status.in_([RunStatus(s) for s in wanted if s in _STATES]))
    if source_system_id is not None:
        conds.append(CollectorRun.source_system_id == source_system_id)
    if platform:
        conds.append(CollectorRun.platform == platform)
    if trigger:
        conds.append(CollectorRun.trigger == trigger)
    total = session.scalar(select(func.count(CollectorRun.id)).where(*conds)) or 0
    response.headers["X-Total-Count"] = str(total)
    query = (
        select(CollectorRun)
        .where(*conds)
        .order_by(CollectorRun.queued_at.desc(), CollectorRun.id)
        .offset(offset)
        .limit(limit)
    )
    return [_run_out(r) for r in session.scalars(query)]


_STATES = {s.value for s in RunStatus}


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


@router.post("/{run_id}/cancel", response_model=CollectorRunOut)
def cancel_run(
    run_id: str,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
):
    """Offenen Lauf beenden (haengt z. B. nach einem Neustart des Agenten). Liefert der Agent
    danach doch noch ab, wird das Ergebnis trotzdem eingespielt."""
    run = session.get(CollectorRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    tenant_scope(principal, run.tenant_id)
    if run.status not in runs.OPEN:
        raise HTTPException(status_code=409, detail="run already finished")
    runs.cancel(session, run, actor=f"user:{principal.subject}")
    return _run_out(run)
