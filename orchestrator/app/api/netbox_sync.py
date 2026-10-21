"""NetBox-Sync: Vorschau und Uebernahme per Klick (ADR 0010) - nur `admin`.

- `GET  /netbox-sync/targets`           NetBox-Quellsysteme des Mandanten (Schreib-Token?)
- `POST /netbox-sync/plans`             Vorschau berechnen (liest NetBox nur), im Hintergrund
- `GET  /netbox-sync/plans`             letzte Vorschauen des Mandanten
- `GET  /netbox-sync/plans/{id}`        Status, Kennzahlen, Hinweise, Ergebnis
- `GET  /netbox-sync/plans/{id}/items`  Aktionen (Filter Art/Aktion/Suche, blaetterbar)
- `POST /netbox-sync/plans/{id}/apply`  Uebernehmen, im Hintergrund; danach liest Automato
                                        NetBox neu ein
- `POST /netbox-sync/plans/{id}/cancel` laufende Vorschau/Uebernahme beenden

Vorschau und Uebernahme laufen als Hintergrundaufgabe im Orchestrator-Prozess. Startet er neu
(z. B. `deploy/update.sh`), stirbt die Aufgabe - bis 0.11.3 blieb der Plan dann fuer immer
"wird uebernommen" und sperrte jeden weiteren Sync (Dev-Befund). Jetzt: beim Start werden
solche Plaene beendet (`abort_interrupted`), ohne Lebenszeichen nach
`netbox_sync_timeout_minutes` ebenfalls (`recover`), und von Hand mit `cancel` - die
Uebernahme prueft vor jeder Objektart, ob sie abgebrochen wurde.

Die Uebernahme berechnet den Plan neu und schreibt nur, wenn der Fingerabdruck zur Vorschau
passt - sonst hat sich NetBox oder Automato geaendert (409 bzw. Lauf `failed`, neue Vorschau).
Geschrieben wird ausschliesslich ueber `sot_gateway.adapters.netbox_writer` mit dem
Schreib-Token des NetBox-Quellsystems; Agenten sind nicht beteiligt.
"""

import logging
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sot_gateway.adapters.netbox_writer import NetBoxWriteError, NetBoxWriter, WriteNotAllowed
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import audit, credentials
from app.auth.deps import require_admin, tenant_scope
from app.auth.principal import Principal
from app.config import get_settings
from app.db import get_session, get_session_factory
from app.jobs import celery_app
from app.models import CollectorRun, Platform, SourceSystem, SyncPlan, SyncStatus, Tenant
from app.store import netbox_sync

log = logging.getLogger(__name__)
router = APIRouter(prefix="/netbox-sync", tags=["netbox-sync"])

MAX_AGE = timedelta(hours=1)  # aeltere Vorschauen muessen neu berechnet werden
KEEP_PLANS = 10  # je Ziel-NetBox aufbewahrte Vorschauen


def make_writer(target: SourceSystem) -> NetBoxWriter:
    """Schreiber mit dem Schreib-Token der Ziel-NetBox (nur Orchestrator, nie Agent)."""
    token = (credentials.decrypt(target.credentials) if target.credentials else {}).get(
        "write_token"
    )
    if not token:
        raise HTTPException(status_code=422, detail="netbox source has no write token")
    return NetBoxWriter(target.base_url, token, verify_tls=target.verify_tls)


class TargetOut(BaseModel):
    id: int
    name: str
    base_url: str
    ref_role: str | None
    has_write_token: bool


class PlanIn(BaseModel):
    tenant_id: int
    source_system_id: int
    sections: list[str] = Field(default_factory=lambda: list(netbox_sync.SECTIONS))
    allow_delete: bool = False


class PlanOut(BaseModel):
    id: int
    tenant_id: int
    source_system_id: int
    target: str
    target_url: str
    status: str
    options: dict
    summary: dict | None
    totals: dict[str, int]
    warnings: list[str]
    message: str | None
    result: dict | None
    created_by: str
    created_at: datetime
    applied_by: str | None
    applied_at: datetime | None
    can_apply: bool


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _totals(summary: dict | None) -> dict[str, int]:
    out = {a: 0 for a in netbox_sync.ACTIONS}
    for counts in (summary or {}).values():
        for action, n in counts.items():
            out[action] = out.get(action, 0) + n
    return out


def _can_apply(plan: SyncPlan) -> bool:
    totals = _totals(plan.summary)
    return (
        plan.status == SyncStatus.READY.value
        and datetime.now(UTC) - _aware(plan.created_at) <= MAX_AGE
        and any(totals[a] for a in netbox_sync.WRITE_ACTIONS)
    )


def _out(plan: SyncPlan, target: SourceSystem) -> PlanOut:
    return PlanOut(
        id=plan.id,
        tenant_id=plan.tenant_id,
        source_system_id=plan.source_system_id,
        target=target.name,
        target_url=target.base_url,
        status=plan.status,
        options=plan.options or {},
        summary=plan.summary,
        totals=_totals(plan.summary),
        warnings=plan.warnings or [],
        message=plan.message,
        result=plan.result,
        created_by=plan.created_by,
        created_at=plan.created_at,
        applied_by=plan.applied_by,
        applied_at=plan.applied_at,
        can_apply=_can_apply(plan),
    )


def _target(session: Session, principal: Principal, tenant_id: int, sid: int) -> SourceSystem:
    tenant_scope(principal, tenant_id)
    target = session.get(SourceSystem, sid)
    if target is None or target.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="netbox source not found")
    if target.platform is not Platform.NETBOX:
        raise HTTPException(status_code=422, detail="target must be a netbox source")
    return target


def _plan(session: Session, principal: Principal, plan_id: int) -> tuple[SyncPlan, SourceSystem]:
    plan = session.get(SyncPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")
    tenant_scope(principal, plan.tenant_id)
    return plan, session.get(SourceSystem, plan.source_system_id)


RUNNING = (SyncStatus.PLANNING.value, SyncStatus.APPLYING.value)
PARTIAL = " - NetBox kann einen Teilstand haben; neue Vorschau erstellen, sie setzt dort an"


def _stop(plan: SyncPlan, message: str) -> None:
    applying = plan.status == SyncStatus.APPLYING.value
    plan.status = SyncStatus.FAILED.value
    plan.message = message + (PARTIAL if applying else "")


def recover(session: Session, now: datetime | None = None) -> int:
    """Vorschau/Uebernahme ohne Lebenszeichen nach der Frist beenden (Aufgabe verloren)."""
    minutes = get_settings().netbox_sync_timeout_minutes
    limit = (now or datetime.now(UTC)) - timedelta(minutes=minutes)
    stale = 0
    for plan in session.scalars(select(SyncPlan).where(SyncPlan.status.in_(RUNNING))):
        alive = plan.heartbeat_at or plan.started_at or plan.created_at
        if alive is not None and _aware(alive) < limit:
            what = "Übernahme" if plan.status == SyncStatus.APPLYING.value else "Vorschau"
            _stop(plan, f"{what} abgebrochen: kein Lebenszeichen seit {minutes} min")
            stale += 1
    if stale:
        session.flush()
    return stale


def abort_interrupted(factory: sessionmaker) -> int:
    """Beim Start des Orchestrators: Plaene, deren Hintergrundaufgabe mit dem alten Prozess
    gestorben ist, beenden (ein Orchestrator-Prozess je Datenbank, siehe Dockerfile)."""
    session = factory()
    try:
        plans = list(session.scalars(select(SyncPlan).where(SyncPlan.status.in_(RUNNING))))
        for plan in plans:
            what = "Übernahme" if plan.status == SyncStatus.APPLYING.value else "Vorschau"
            _stop(plan, f"{what} abgebrochen: Orchestrator wurde neu gestartet")
        session.commit()
        return len(plans)
    finally:
        session.close()


def _busy(session: Session, target_id: int) -> bool:
    recover(session)
    return (
        session.scalar(
            select(SyncPlan.id).where(
                SyncPlan.source_system_id == target_id,
                SyncPlan.status.in_([SyncStatus.PLANNING.value, SyncStatus.APPLYING.value]),
            )
        )
        is not None
    )


def _has_write_token(target: SourceSystem) -> bool:
    try:
        return bool(
            (credentials.decrypt(target.credentials) if target.credentials else {}).get(
                "write_token"
            )
        )
    except credentials.CredentialsUnavailable:
        return False


@router.get("/targets", response_model=list[TargetOut])
def list_targets(
    tenant_id: int,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
):
    tenant_scope(principal, tenant_id)
    rows = session.scalars(
        select(SourceSystem)
        .where(SourceSystem.tenant_id == tenant_id, SourceSystem.platform == Platform.NETBOX)
        .order_by(SourceSystem.name)
    )
    return [
        TargetOut(
            id=s.id,
            name=s.name,
            base_url=s.base_url,
            ref_role=s.ref_role,
            has_write_token=_has_write_token(s),
        )
        for s in rows
    ]


@router.post("/plans", response_model=PlanOut, status_code=202)
def create_plan(
    body: PlanIn,
    background: BackgroundTasks,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
    factory: sessionmaker = Depends(get_session_factory),
):
    target = _target(session, principal, body.tenant_id, body.source_system_id)
    sections = [s for s in body.sections if s in netbox_sync.SECTIONS]
    if not sections:
        raise HTTPException(status_code=422, detail=f"sections: {', '.join(netbox_sync.SECTIONS)}")
    if not _has_write_token(target):
        raise HTTPException(status_code=422, detail="netbox source has no write token")
    if not target.base_url:
        raise HTTPException(status_code=422, detail="netbox source has no address")
    if _busy(session, target.id):
        raise HTTPException(status_code=409, detail="sync for this netbox is already running")
    plan = SyncPlan(
        tenant_id=body.tenant_id,
        source_system_id=target.id,
        status=SyncStatus.PLANNING.value,
        options={"sections": sections, "allow_delete": body.allow_delete},
        created_by=principal.subject,
        created_at=datetime.now(UTC),
    )
    session.add(plan)
    session.flush()
    session.commit()  # vor der Hintergrund-Aufgabe sichtbar machen
    background.add_task(compute_job, factory, plan.id)
    return _out(plan, target)


@router.get("/plans", response_model=list[PlanOut])
def list_plans(
    tenant_id: int,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
):
    tenant_scope(principal, tenant_id)
    recover(session)
    plans = session.scalars(
        select(SyncPlan)
        .where(SyncPlan.tenant_id == tenant_id)
        .order_by(SyncPlan.id.desc())
        .limit(20)
    ).all()
    targets = {p.source_system_id: session.get(SourceSystem, p.source_system_id) for p in plans}
    return [_out(p, targets[p.source_system_id]) for p in plans]


@router.get("/plans/{plan_id}", response_model=PlanOut)
def get_plan(
    plan_id: int,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
):
    recover(session)
    plan, target = _plan(session, principal, plan_id)
    return _out(plan, target)


@router.post("/plans/{plan_id}/cancel", response_model=PlanOut)
def cancel_plan(
    plan_id: int,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
):
    """Laufende Vorschau/Uebernahme beenden; die Uebernahme hoert vor der naechsten Objektart
    auf (bereits Geschriebenes bleibt)."""
    plan, target = _plan(session, principal, plan_id)
    if plan.status not in RUNNING:
        raise HTTPException(status_code=409, detail=f"plan is {plan.status}")
    _stop(plan, f"abgebrochen von {principal.subject}")
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="netbox_sync.cancelled",
        subject=str(plan.id),
        detail={"plan_id": plan.id, "tenant_id": plan.tenant_id},
    )
    return _out(plan, target)


@router.get("/plans/{plan_id}/items")
def plan_items(
    plan_id: int,
    action: str | None = None,
    kind: str | None = None,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    plan, _ = _plan(session, principal, plan_id)
    rows = plan.items or []
    if action:
        rows = [i for i in rows if i["action"] in action.split(",")]
    if kind:
        rows = [i for i in rows if i["kind"] == kind]
    if q:
        ql = q.strip().lower()
        rows = [i for i in rows if ql in i["label"].lower() or ql in i["key"].lower()]
    return {"total": len(rows), "rows": rows[max(offset, 0) : max(offset, 0) + min(limit, 2000)]}


@router.post("/plans/{plan_id}/apply", response_model=PlanOut, status_code=202)
def apply_plan(
    plan_id: int,
    background: BackgroundTasks,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
    factory: sessionmaker = Depends(get_session_factory),
):
    plan, target = _plan(session, principal, plan_id)
    if plan.status != SyncStatus.READY.value:
        raise HTTPException(status_code=409, detail=f"plan is {plan.status}")
    if datetime.now(UTC) - _aware(plan.created_at) > MAX_AGE:
        raise HTTPException(status_code=409, detail="preview is older than 60 minutes")
    if _busy(session, target.id):
        raise HTTPException(status_code=409, detail="sync for this netbox is already running")
    if not _can_apply(plan):
        raise HTTPException(status_code=409, detail="nothing to apply")
    plan.status = SyncStatus.APPLYING.value
    plan.applied_by = principal.subject
    plan.started_at = plan.heartbeat_at = datetime.now(UTC)
    session.commit()
    background.add_task(apply_job, factory, plan.id)
    return _out(plan, target)


# -- Hintergrund-Aufgaben -----------------------------------------------------------------


def _error_text(exc: Exception) -> str:
    """Fehlermeldung ohne Secrets (keine Header/Tokens, nur Ziel und Grund)."""
    if isinstance(exc, NetBoxWriteError | WriteNotAllowed):
        return str(exc)
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    if isinstance(exc, httpx.HTTPError):
        url = getattr(getattr(exc, "request", None), "url", None)
        where = f" ({url.host})" if url is not None else ""
        return f"NetBox nicht erreichbar{where}: {type(exc).__name__}"
    if isinstance(exc, credentials.CredentialsUnavailable):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"[:500]


def _load(session: Session, plan_id: int):
    plan = session.get(SyncPlan, plan_id)
    target = session.get(SourceSystem, plan.source_system_id) if plan else None
    tenant = session.get(Tenant, plan.tenant_id) if plan else None
    return plan, target, tenant


def compute_job(factory: sessionmaker, plan_id: int) -> None:
    """Vorschau berechnen: Soll aus Automato, Ist aus NetBox (nur lesen)."""
    session = factory()
    try:
        plan, target, tenant = _load(session, plan_id)
        if plan is None or target is None or tenant is None:
            return
        writer = make_writer(target)
        try:
            result = netbox_sync.compute(
                session,
                writer,
                tenant,
                target,
                plan.options.get("sections", []),
                bool(plan.options.get("allow_delete")),
            )
        finally:
            writer.close()
        session.refresh(plan)
        if plan.status != SyncStatus.PLANNING.value:
            return  # waehrenddessen abgebrochen bzw. als haengend beendet
        plan.items = [i for i in result.items if i["action"] != "unchanged"]
        plan.summary = netbox_sync.summarize(result.items)
        plan.warnings = result.wants.warnings
        plan.fingerprint = result.fingerprint
        plan.status = SyncStatus.READY.value
        for older in session.scalars(
            select(SyncPlan).where(
                SyncPlan.source_system_id == plan.source_system_id,
                SyncPlan.id != plan.id,
                SyncPlan.status == SyncStatus.READY.value,
            )
        ):
            older.status = SyncStatus.SUPERSEDED.value
        _prune(session, plan.source_system_id)
        totals = _totals(plan.summary)
        audit.record(
            session,
            actor=f"user:{plan.created_by}",
            action="netbox_sync.planned",
            subject=str(plan.id),
            detail={
                "plan_id": plan.id,
                "tenant_id": plan.tenant_id,
                "source_system_id": plan.source_system_id,
                **{a: totals[a] for a in ("create", "update", "delete", "conflict")},
            },
        )
        session.commit()
    except Exception as exc:  # noqa: BLE001 - Fehler landen am Plan, nicht als 500
        session.rollback()
        log.exception("NetBox-Sync-Vorschau %s fehlgeschlagen", plan_id)
        _fail(factory, plan_id, f"Vorschau fehlgeschlagen: {_error_text(exc)}")
    finally:
        session.close()


def apply_job(factory: sessionmaker, plan_id: int) -> None:
    """Uebernehmen: Plan neu berechnen, Fingerabdruck pruefen, schreiben, NetBox neu lesen."""
    session = factory()
    done: dict | None = None
    try:
        plan, target, tenant = _load(session, plan_id)
        if plan is None or target is None or tenant is None:
            return
        writer = make_writer(target)
        try:
            fresh = netbox_sync.compute(
                session,
                writer,
                tenant,
                target,
                plan.options.get("sections", []),
                bool(plan.options.get("allow_delete")),
            )
            if fresh.fingerprint != plan.fingerprint:
                raise _Changed()
            done = netbox_sync.apply(writer, fresh, progress=_progress(factory, plan_id))
        finally:
            writer.close()
        session.refresh(plan)
        if plan.status != SyncStatus.APPLYING.value:
            return  # inzwischen abgebrochen bzw. beendet - Meldung steht am Plan
        plan.status = SyncStatus.APPLIED.value
        plan.result = done
        plan.applied_at = datetime.now(UTC)
        plan.message = "Übernommen – NetBox wird neu eingelesen."
        _audit_applied(session, plan, done, "applied")
        _queue_reread(session, target)
        session.commit()
    except _Cancelled:
        session.rollback()
        log.info("NetBox-Sync-Uebernahme %s abgebrochen", plan_id)
    except _Changed:
        session.rollback()
        _fail(
            factory,
            plan_id,
            "Seit der Vorschau hat sich NetBox oder Automato geändert – nichts geschrieben, bitte"
            " neue Vorschau erstellen.",
        )
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        log.exception("NetBox-Sync-Uebernahme %s fehlgeschlagen", plan_id)
        _fail(factory, plan_id, f"Übernahme abgebrochen: {_error_text(exc)}", applying=True)
    finally:
        session.close()


class _Changed(Exception):
    pass


class _Cancelled(Exception):
    pass


def _progress(factory: sessionmaker, plan_id: int):
    """Lebenszeichen je Objektart; bricht ab, wenn der Plan nicht mehr uebernommen wird."""

    def tick(kind: str) -> None:
        s = factory()
        try:
            plan = s.get(SyncPlan, plan_id)
            if plan is None or plan.status != SyncStatus.APPLYING.value:
                raise _Cancelled()
            plan.heartbeat_at = datetime.now(UTC)
            plan.message = f"schreibt: {netbox_sync.KIND[kind].label}"
            s.commit()
        finally:
            s.close()

    return tick


def _fail(factory: sessionmaker, plan_id: int, message: str, applying: bool = False) -> None:
    session = factory()
    try:
        plan = session.get(SyncPlan, plan_id)
        if plan is None:
            return
        plan.status = SyncStatus.FAILED.value
        plan.message = message
        if applying:  # Teilstand moeglich -> neu einlesen, damit Automato den Ist-Stand kennt
            _audit_applied(session, plan, None, "failed")
            target = session.get(SourceSystem, plan.source_system_id)
            if target is not None:
                _queue_reread(session, target)
        session.commit()
    finally:
        session.close()


def _audit_applied(session: Session, plan: SyncPlan, done: dict | None, status: str) -> None:
    totals = {"create": 0, "update": 0, "delete": 0}
    for counts in (done or {}).values():
        for k in totals:
            totals[k] += counts.get(k, 0)
    audit.record(
        session,
        actor=f"user:{plan.applied_by}",
        action="netbox_sync.applied",
        subject=str(plan.id),
        detail={
            "plan_id": plan.id,
            "tenant_id": plan.tenant_id,
            "source_system_id": plan.source_system_id,
            "status": status,
            **totals,
        },
    )


def _queue_reread(session: Session, target: SourceSystem) -> None:
    """NetBox nach der Uebernahme neu einlesen (zentraler Worker bzw. zugewiesener Agent)."""
    execution = "external" if target.agent_id is not None else "inprocess"
    run = CollectorRun(
        platform=Platform.NETBOX.value,
        tenant_id=target.tenant_id,
        source_system_id=target.id,
        execution=execution,
    )
    session.add(run)
    session.flush()
    audit.record(
        session,
        actor="system:netbox-sync",
        action="collector_run.queued",
        subject=run.id,
        detail={
            "platform": Platform.NETBOX.value,
            "tenant_id": target.tenant_id,
            "source_system_id": target.id,
            "execution": execution,
        },
    )
    if execution == "inprocess":
        celery_app.dispatch_collect(Platform.NETBOX.value, run.id)


def _prune(session: Session, target_id: int) -> None:
    old = session.scalars(
        select(SyncPlan)
        .where(SyncPlan.source_system_id == target_id)
        .order_by(SyncPlan.id.desc())
        .offset(KEEP_PLANS)
    ).all()
    for p in old:
        session.delete(p)
