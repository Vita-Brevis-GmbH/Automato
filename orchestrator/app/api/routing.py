"""Routing, BGP, HA und L3Out (ADR 0019 Stufe 2) - nur lesend, mandantsgescoped (viewer).

- `GET /routing/summary`         je Quellsystem die gelesenen Kontexte (VDOM bzw. VRF auf dem
                                 Border-Leaf) mit Anzahl Routen und dem, was nicht lesbar war
- `GET /routing/routes`          Routen seitenweise (RIB, BGP-Pfade, statische); `ip` = Routen,
                                 die die IP enthalten, laengstes Praefix zuerst
- `GET /routing/lookup?ip=`      je Kontext die wirksame Route und vorgehende Policy-Routen
- `GET /routing/peers`, `/ha`, `/policy-routes`, `/l3outs`
"""

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.audit import reads
from app.auth.deps import require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import (
    SourceSystem,
    TopoContext,
    TopoHa,
    TopoL3Out,
    TopoPeer,
    TopoPolicyRoute,
    TopoRoute,
)
from app.store import topology

router = APIRouter(prefix="/routing", tags=["routing"])


def _tenant(session: Session, principal: Principal, tenant_id: int | None) -> int:
    tid = tenant_scope(principal, tenant_id)
    if tid is None:
        raise HTTPException(status_code=422, detail="tenant_id required")
    reads.maybe_audit_read(session, principal, tid, "routing")
    return tid


def _names(session: Session, tid: int) -> dict[int, str]:
    return dict(
        session.execute(
            select(SourceSystem.id, SourceSystem.name).where(SourceSystem.tenant_id == tid)
        ).all()
    )


def _row(obj, names: dict[int, str], **extra) -> dict:
    row = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    for key in ("lo", "hi", "tenant_id"):
        row.pop(key, None)
    return {**row, "source": names.get(obj.source_system_id), **extra}


@router.get("/summary")
def summary(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Gelesene Kontexte je Quellsystem mit Anzahl Routen (je Tabelle), Nachbarn und Regeln."""
    tid = _tenant(session, principal, tenant_id)
    names = _names(session, tid)
    counts: dict[tuple, dict[str, int]] = defaultdict(dict)
    for sid, node, ctx, table, n in session.execute(
        select(
            TopoRoute.source_system_id,
            TopoRoute.node,
            TopoRoute.context,
            TopoRoute.table,
            func.count(TopoRoute.id),
        )
        .where(TopoRoute.tenant_id == tid)
        .group_by(TopoRoute.source_system_id, TopoRoute.node, TopoRoute.context, TopoRoute.table)
    ):
        counts[(sid, node, ctx)][table] = n
    for model, key in ((TopoPeer, "peers"), (TopoPolicyRoute, "policy_routes")):
        for sid, node, ctx, n in session.execute(
            select(model.source_system_id, model.node, model.context, func.count(model.id))
            .where(model.tenant_id == tid)
            .group_by(model.source_system_id, model.node, model.context)
        ):
            counts[(sid, node, ctx)][key] = n
    contexts = session.scalars(
        select(TopoContext)
        .where(TopoContext.tenant_id == tid)
        .order_by(TopoContext.source_system_id, TopoContext.node, TopoContext.context)
    )
    rows = [
        _row(c, names, counts=counts.get((c.source_system_id, c.node, c.context), {}))
        for c in contexts
    ]
    ha = session.scalar(select(func.count(TopoHa.id)).where(TopoHa.tenant_id == tid)) or 0
    l3 = session.scalar(select(func.count(TopoL3Out.id)).where(TopoL3Out.tenant_id == tid)) or 0
    return {"contexts": rows, "ha": ha, "l3outs": l3}


@router.get("/routes")
def routes(
    response: Response,
    tenant_id: int | None = None,
    source_system_id: int | None = None,
    node: str | None = None,
    context: str | None = None,
    table: str | None = Query(None, pattern="^(rib|bgp|static)$"),
    protocol: str | None = None,
    ip: str | None = Query(None, description="Routen, die diese IP enthalten"),
    q: str | None = Query(None, description="Praefix, Next-Hop, Interface, Protokoll"),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """Routen seitenweise (Gesamtzahl im Header `X-Total-Count`)."""
    tid = _tenant(session, principal, tenant_id)
    stmt = select(TopoRoute).where(TopoRoute.tenant_id == tid)
    for column, value in (
        (TopoRoute.source_system_id, source_system_id),
        (TopoRoute.node, node),
        (TopoRoute.context, context),
        (TopoRoute.table, table),
        (TopoRoute.protocol, protocol),
    ):
        if value not in (None, ""):
            stmt = stmt.where(column == value)
    order = [
        TopoRoute.source_system_id,
        TopoRoute.node,
        TopoRoute.context,
        TopoRoute.family,
        TopoRoute.lo,
        TopoRoute.prefix_len,
        TopoRoute.table,
        TopoRoute.id,
    ]
    if ip:
        rng = topology.route_range(ip.split("/")[0])
        if rng is None:
            raise HTTPException(status_code=422, detail="ip: keine gueltige IP")
        family, _, lo, _, _ = rng
        stmt = stmt.where(TopoRoute.family == family, TopoRoute.lo <= lo, TopoRoute.hi >= lo)
        order = [TopoRoute.prefix_len.desc(), *order]
    for token in (q or "").lower().split():
        like = f"%{token}%"
        stmt = stmt.where(
            or_(
                func.lower(TopoRoute.prefix).like(like),
                func.lower(TopoRoute.protocol).like(like),
                func.lower(TopoRoute.context).like(like),
                func.lower(TopoRoute.node).like(like),
                func.lower(cast(TopoRoute.next_hops, String)).like(like),
            )
        )
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    response.headers["X-Total-Count"] = str(total)
    page = list(session.scalars(stmt.order_by(*order).offset(offset).limit(limit)))
    names = _names(session, tid)
    ifaces = topology.Interfaces(session, {r.source_system_id for r in page})
    return [_row(r, names, next_hops=ifaces.hops(r.source_system_id, r.next_hops)) for r in page]


@router.get("/lookup")
def lookup(
    ip: str,
    tenant_id: int | None = None,
    source_system_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """Je Kontext die wirksame Route fuer `ip` und Policy-Routen, die vorgehen (koennen)."""
    tid = _tenant(session, principal, tenant_id)
    if topology.route_range(ip) is None:
        raise HTTPException(status_code=422, detail="ip: keine gueltige IP")
    names = _names(session, tid)
    return [
        {**row, "source": names.get(row["source_system_id"])}
        for row in topology.lookup(session, tid, ip, source_system_id)
    ]


def _list(session: Session, tid: int, model, source_system_id: int | None, order) -> list[dict]:
    stmt = select(model).where(model.tenant_id == tid)
    if source_system_id is not None:
        stmt = stmt.where(model.source_system_id == source_system_id)
    names = _names(session, tid)
    return [_row(o, names) for o in session.scalars(stmt.order_by(*order))]


@router.get("/peers")
def peers(
    tenant_id: int | None = None,
    source_system_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    tid = _tenant(session, principal, tenant_id)
    order = (TopoPeer.source_system_id, TopoPeer.node, TopoPeer.context, TopoPeer.peer_ip)
    return _list(session, tid, TopoPeer, source_system_id, order)


@router.get("/ha")
def ha(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    tid = _tenant(session, principal, tenant_id)
    return _list(session, tid, TopoHa, None, (TopoHa.source_system_id, TopoHa.node))


@router.get("/policy-routes")
def policy_routes(
    tenant_id: int | None = None,
    source_system_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    tid = _tenant(session, principal, tenant_id)
    order = (
        TopoPolicyRoute.source_system_id,
        TopoPolicyRoute.node,
        TopoPolicyRoute.context,
        TopoPolicyRoute.kind,
        TopoPolicyRoute.seq,
    )
    return _list(session, tid, TopoPolicyRoute, source_system_id, order)


@router.get("/l3outs")
def l3outs(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    tid = _tenant(session, principal, tenant_id)
    order = (TopoL3Out.source_system_id, TopoL3Out.aci_tenant, TopoL3Out.name)
    return _list(session, tid, TopoL3Out, None, order)
