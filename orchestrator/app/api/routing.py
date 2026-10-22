"""Routing, BGP, HA und L3Out (ADR 0019) - nur lesend, mandantsgescoped (viewer).

- `GET /routing/summary`         gelesene Kontexte (Firewall, VDOM bzw. VRF auf dem Border-Leaf)
                                 mit Anzahl Routen und dem, was nicht lesbar war; dazu die
                                 Knoten fuer den Startpunkt der Weg-Suche
- `GET /routing/routes`          Routen seitenweise (RIB, BGP-Pfade, statische); `ip` = Routen,
                                 die die IP enthalten, laengstes Praefix zuerst
- `GET /routing/lookup?ip=`      je Kontext die wirksame Route und vorgehende Policy-Routen
- `GET /routing/trace?dst=&src=&start=`  Weg Hop fuer Hop von der Quelle bzw. dem Startpunkt
- `GET /routing/peers`, `/ha`, `/policy-routes`, `/l3outs` - Nachbarn mit Namen aus dem IPAM
- `GET/POST/PUT/DELETE /routing/exits` - Anbindungen nach aussen (Internet, MPLS, WAN),
                                 von Hand erfasst (operator), an denen ein Weg endet bzw.
                                 nach manuellen Routen weitergeht (0.18.0)

Bezeichnung eines Kontexts: eine FortiGate nur mit `root` heisst wie die Firewall, sonst
„fw · VDOM x“; ACI „leaf · VRF Tenant:VRF“ (`label`, `display`).
"""

import ipaddress
from collections import defaultdict
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app import audit
from app.audit import reads
from app.auth.deps import require_operator, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import (
    SourceSystem,
    TopoContext,
    TopoExit,
    TopoHa,
    TopoL3Out,
    TopoPeer,
    TopoPolicyRoute,
    TopoRoute,
)
from app.store import paths, topology

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


def _row(obj, names: dict[int, str], graph: paths.Graph | None = None, **extra) -> dict:
    row = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    for key in ("lo", "hi", "tenant_id"):
        row.pop(key, None)
    row["source"] = names.get(obj.source_system_id)
    if graph is not None and hasattr(obj, "context"):
        row["display"], row["label"] = graph.label(obj.source_system_id, obj.node, obj.context)
    return {**row, **extra}


def _hops(graph: paths.Graph, ifaces: topology.Interfaces, sid: int, hops) -> list[dict]:
    """Next-Hops mit Zone (Inventar) und Namen aus dem IPAM."""
    return [{**h, **graph.resolve(h.get("ip"))} for h in ifaces.hops(sid, hops)]


def _ip(value: str, what: str):
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{what}: keine gueltige IP") from None


@router.get("/summary")
def summary(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Gelesene Kontexte je Quellsystem mit Anzahl Routen (je Tabelle), Nachbarn und Regeln."""
    tid = _tenant(session, principal, tenant_id)
    names = _names(session, tid)
    graph = paths.Graph(session, tid)
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
        _row(c, names, graph, counts=counts.get((c.source_system_id, c.node, c.context), {}))
        for c in contexts
    ]
    ha = session.scalar(select(func.count(TopoHa.id)).where(TopoHa.tenant_id == tid)) or 0
    l3 = session.scalar(select(func.count(TopoL3Out.id)).where(TopoL3Out.tenant_id == tid)) or 0
    nodes = sorted((n.out() for n in graph.nodes.values()), key=lambda n: n["label"].lower())
    return {"contexts": rows, "nodes": nodes, "ha": ha, "l3outs": l3}


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
    graph = paths.Graph(session, tid)
    ifaces = topology.Interfaces(session, {r.source_system_id for r in page})
    return [
        _row(r, names, graph, next_hops=_hops(graph, ifaces, r.source_system_id, r.next_hops))
        for r in page
    ]


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
    _ip(ip, "ip")
    names = _names(session, tid)
    graph = paths.Graph(session, tid)
    out = []
    for row in topology.lookup(session, tid, ip, source_system_id):
        display, label = graph.label(row["source_system_id"], row["node"], row["context"])
        if row["route"]:
            row["route"]["next_hops"] = [
                {**h, **graph.resolve(h.get("ip"))} for h in row["route"]["next_hops"]
            ]
        out.append(
            {**row, "source": names.get(row["source_system_id"]), "display": display,
             "label": label}
        )  # fmt: skip
    return out


@router.get("/trace")
def trace(
    dst: str,
    src: str | None = None,
    start: str | None = Query(None, description="Startpunkt: <Quellsystem>|<Kontext>"),
    slack: int = Query(1, ge=0, le=3, description="Backup-Wege mit hoechstens so vielen Hops mehr"),
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Weg zum Ziel Hop fuer Hop - ab der Quelle (dort angeschlossen) bzw. dem Startpunkt."""
    tid = _tenant(session, principal, tenant_id)
    _ip(dst, "dst")
    if src:
        _ip(src, "src")
    if not src and not start:
        raise HTTPException(status_code=422, detail="src oder start angeben")
    return paths.trace(session, tid, dst, src, start, slack)


def _list(session: Session, tid: int, model, source_system_id: int | None, order) -> list:
    stmt = select(model).where(model.tenant_id == tid)
    if source_system_id is not None:
        stmt = stmt.where(model.source_system_id == source_system_id)
    return list(session.scalars(stmt.order_by(*order)))


@router.get("/peers")
def peers(
    tenant_id: int | None = None,
    source_system_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """BGP-Nachbarn mit Namen aus dem IPAM und - falls gelesen - dem Knoten dahinter."""
    tid = _tenant(session, principal, tenant_id)
    order = (TopoPeer.source_system_id, TopoPeer.node, TopoPeer.context, TopoPeer.peer_ip)
    names = _names(session, tid)
    graph = paths.Graph(session, tid)
    return [
        _row(
            p,
            names,
            graph,
            peer=graph.resolve(p.peer_ip),
            peer_node=graph.owner_label(p.peer_ip),
        )
        for p in _list(session, tid, TopoPeer, source_system_id, order)
    ]


@router.get("/ha")
def ha(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    tid = _tenant(session, principal, tenant_id)
    names = _names(session, tid)
    graph = paths.Graph(session, tid)
    rows = _list(session, tid, TopoHa, None, (TopoHa.source_system_id, TopoHa.node))
    out = []
    for h in rows:
        row = _row(h, names)
        row["sync_peers"] = [
            {**p, **graph.resolve(p.get("peerip")), "node": graph.owner_label(p.get("peerip"))}
            for p in h.sync_peers or []
        ]
        out.append(row)
    return out


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
    names = _names(session, tid)
    graph = paths.Graph(session, tid)
    return [
        _row(p, names, graph, gateway_resolved=graph.resolve(p.gateway))
        for p in _list(session, tid, TopoPolicyRoute, source_system_id, order)
    ]


@router.get("/l3outs")
def l3outs(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """L3Outs mit Border-Leafs (Namen aus dem Inventar), Peers und Next-Hops mit IPAM-Namen."""
    tid = _tenant(session, principal, tenant_id)
    order = (TopoL3Out.source_system_id, TopoL3Out.aci_tenant, TopoL3Out.name)
    names = _names(session, tid)
    graph = paths.Graph(session, tid)
    rows = _list(session, tid, TopoL3Out, None, order)
    leafs = paths.device_names(session, {o.source_system_id for o in rows})
    out = []
    for o in rows:
        row = _row(o, names)
        sid = o.source_system_id
        row["nodes"] = [
            {
                **n,
                "name": leafs.get((sid, str(n.get("node")))),
                "static_routes": [
                    {**r, "resolved": {h: graph.resolve(h) for h in r.get("next_hops") or []}}
                    for r in n.get("static_routes") or []
                ],
            }
            for n in o.nodes or []
        ]
        row["paths"] = [
            {
                **p,
                "node_names": [leafs.get((sid, str(x)), f"node-{x}") for x in p.get("nodes") or []],
            }
            for p in o.paths or []
        ]
        row["peers"] = [
            {**p, **graph.resolve(p.get("ip")), "node": graph.owner_label(p.get("ip"))}
            for p in o.peers or []
        ]
        out.append(row)
    return out


# -- Anbindungen nach aussen (0.18.0, von Hand erfasst) ------------------------------------

EXIT_KINDS = ("internet", "mpls", "wan", "partner", "other")


class ExitRoute(BaseModel):
    prefix: str = Field(max_length=64)
    target: str = Field(max_length=300)  # Knoten <Quellsystem>|<Kontext>


class ExitIn(BaseModel):
    tenant_id: int
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["internet", "mpls", "wan", "partner", "other"] = "other"
    source_system_id: int | None = None  # nur fuer diese Firewall (Interfaces/Zonen)
    interfaces: list[str] = []  # Interfaces bzw. Zonen
    next_hops: list[str] = []  # IPs der Gegenstelle
    prefixes: list[str] = []  # Netze dahinter (leer = alles, was dorthin geroutet wird)
    routes: list[ExitRoute] = []  # manuell: Praefix -> weiter beim gelesenen Knoten
    description: str | None = Field(default=None, max_length=2000)


def _net(value: str, what: str) -> str:
    try:
        return str(ipaddress.ip_network(value.strip(), strict=False))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{what}: kein gueltiges Netz") from None


def _clean(session: Session, body: ExitIn) -> dict:
    """Eingaben pruefen und vereinheitlichen (Netze in CIDR, Next-Hops als IP)."""
    if body.source_system_id is not None:
        src = session.get(SourceSystem, body.source_system_id)
        if src is None or src.tenant_id != body.tenant_id:
            raise HTTPException(status_code=422, detail="source system not in tenant")
    hops = [str(_ip(h, "next_hops")) for h in body.next_hops if h.strip()]
    ifaces = [i.strip() for i in body.interfaces if i.strip()]
    if not hops and not ifaces:
        raise HTTPException(status_code=422, detail="Next-Hop oder Interface/Zone angeben")
    routes = []
    for r in body.routes:
        if paths.parse_key(r.target) is None:
            raise HTTPException(status_code=422, detail="routes.target: <Quellsystem>|<Kontext>")
        routes.append({"prefix": _net(r.prefix, "routes.prefix"), "target": r.target})
    return {
        "name": body.name.strip(),
        "kind": body.kind,
        "source_system_id": body.source_system_id,
        "interfaces": ifaces,
        "next_hops": hops,
        "prefixes": [_net(p, "prefixes") for p in body.prefixes if p.strip()],
        "routes": routes,
        "description": (body.description or "").strip() or None,
    }


def _exit_out(e: TopoExit, names: dict[int, str]) -> dict:
    return {
        "id": e.id,
        "tenant_id": e.tenant_id,
        "name": e.name,
        "kind": e.kind,
        "source_system_id": e.source_system_id,
        "source": names.get(e.source_system_id) if e.source_system_id else None,
        "interfaces": e.interfaces or [],
        "next_hops": e.next_hops or [],
        "prefixes": e.prefixes or [],
        "routes": e.routes or [],
        "description": e.description,
        "updated_at": e.updated_at,
    }


def _audit_exit(session: Session, principal: Principal, action: str, e: TopoExit) -> None:
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action=f"routing.exit.{action}",
        subject=str(e.id),
        detail={"tenant_id": e.tenant_id, "exit_id": e.id, "name": e.name, "kind": e.kind},
    )


@router.get("/exits")
def list_exits(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    tid = _tenant(session, principal, tenant_id)
    names = _names(session, tid)
    rows = session.scalars(
        select(TopoExit).where(TopoExit.tenant_id == tid).order_by(TopoExit.name, TopoExit.id)
    )
    return [_exit_out(e, names) for e in rows]


@router.post("/exits", status_code=201)
def create_exit(
    body: ExitIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Anbindung erfassen - Operator. Nur Daten der Plattform, kein Zugriff auf Geraete."""
    tenant_scope(principal, body.tenant_id)
    e = TopoExit(tenant_id=body.tenant_id, **_clean(session, body))
    session.add(e)
    session.flush()
    _audit_exit(session, principal, "created", e)
    return _exit_out(e, _names(session, body.tenant_id))


def _get_exit(session: Session, principal: Principal, exit_id: int) -> TopoExit:
    e = session.get(TopoExit, exit_id)
    if e is None:
        raise HTTPException(status_code=404, detail="exit not found")
    tenant_scope(principal, e.tenant_id)
    return e


@router.put("/exits/{exit_id}")
def update_exit(
    exit_id: int,
    body: ExitIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    e = _get_exit(session, principal, exit_id)
    if body.tenant_id != e.tenant_id:
        raise HTTPException(status_code=422, detail="tenant_id must not change")
    for key, value in _clean(session, body).items():
        setattr(e, key, value)
    e.updated_at = datetime.now(UTC)
    session.flush()
    _audit_exit(session, principal, "updated", e)
    return _exit_out(e, _names(session, e.tenant_id))


@router.delete("/exits/{exit_id}", status_code=204)
def delete_exit(
    exit_id: int,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> None:
    e = _get_exit(session, principal, exit_id)
    _audit_exit(session, principal, "deleted", e)
    session.delete(e)
