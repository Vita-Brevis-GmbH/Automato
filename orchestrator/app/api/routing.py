"""Routing, BGP, HA und L3Out (ADR 0019) - nur lesend, mandantsgescoped (viewer).

- `GET /routing/summary`         gelesene Kontexte (Firewall, VDOM bzw. VRF auf dem Border-Leaf)
                                 mit Anzahl Routen und dem, was nicht lesbar war; dazu die
                                 Knoten fuer den Startpunkt der Weg-Suche
- `GET /routing/routes`          Routen seitenweise (RIB, BGP-Pfade, statische); `ip` = Routen,
                                 die die IP enthalten, laengstes Praefix zuerst; je Route die
                                 letzte Aenderung aus dem Verlauf (`changed_at`, ADR 0030)
- `GET /routing/changes`         Verlauf des Routings (ADR 0030): Aenderungen je Lauf an Routen,
                                 BGP-Nachbarn, HA, Policy-Routen, Kontexten und L3Outs,
                                 seitenweise, neuste zuerst; nicht fuer Kunden-Benutzer
- `GET /routing/lookup?ip=`      je Kontext die wirksame Route und vorgehende Policy-Routen
                                 (auch fuer ein Netz: Route, die es ganz enthaelt)
- `GET /routing/trace?dst=&src=&start=&service=`  Weg Hop fuer Hop von der Quelle bzw. dem
                                 Startpunkt, je FortiGate-Hop die passenden Policies (0.22.0),
                                 je ACI-VRF-Hop die Contracts (0.35.0); Dienst auch als
                                 Service-Objekt bzw. ACI-Filter (`FT_https`); Quelle und Ziel
                                 auch als Netz (0.39.0)
- `GET /routing/resolve?q=&type=`  Name bzw. FQDN -> Hosts und Netze (IPAM, Objekte, VMs,
                                 Inventar) fuer die Weg-Suche, seitenweise (`limit`, `offset`)
- `GET /routing/peers`, `/ha`, `/policy-routes`, `/l3outs` - Nachbarn mit Namen aus dem IPAM;
                                 je L3Out die Firewall dahinter (`behind`, ADR 0034)
- `GET /routing/l3outs/firewall-choices`  Firewalls (VDOMs) mit ihren Interfaces zur Auswahl
- `PUT/DELETE /routing/l3outs/firewall`   Firewall hinter einem L3Out von Hand: bestaetigen,
                                 waehlen, keine bzw. wieder abgeleitet (operator, ADR 0034)
- `GET/POST/PUT/DELETE /routing/exits` - Anbindungen nach aussen (Internet, MPLS, WAN),
                                 von Hand erfasst (operator), an denen ein Weg endet bzw.
                                 nach manuellen Routen weitergeht (0.18.0); fuer eine oder
                                 mehrere Firewalls, mit den erkannten Routern (0.20.0)
- `GET /routing/exits/suggestions` erkannte Anbindungen: Next-Hops und BGP-Nachbarn ohne
                                 gelesenes Geraet, zusammengefasst mit Redundanz (0.20.0)
- `POST /routing/exits/dismiss[/undo]` erkannte Router ausblenden bzw. wieder vorschlagen
                                 (operator, 0.21.0)

Bezeichnung eines Kontexts: eine FortiGate nur mit `root` heisst wie die Firewall, sonst
„fw · VDOM x“; ACI „leaf · VRF Tenant:VRF“ (`label`, `display`).
"""

import ipaddress
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import String, and_, cast, false, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app import audit
from app.api.customer_filter import customer_view
from app.audit import reads
from app.auth.deps import require_operator, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session, get_session_factory
from app.i18n import L
from app.models import (
    CollectorRun,
    SourceSystem,
    Tenant,
    TopoChange,
    TopoContext,
    TopoExit,
    TopoHa,
    TopoL3Out,
    TopoPeer,
    TopoPolicyRoute,
    TopoRoute,
)
from app.store import (
    app_refresh,
    customer_infra,
    exits,
    l2_topo,
    l3out_firewall,
    paths,
    policy_check,
    resolve,
    topo_changes,
    topology,
    zone_path,
)
from app.store.customer_infra import OWN, SHARED

router = APIRouter(prefix="/routing", tags=["routing"])


def _tenant(session: Session, principal: Principal, tenant_id: int | None) -> int:
    tid = tenant_scope(principal, tenant_id)
    if tid is None:
        raise HTTPException(status_code=422, detail="tenant_id required")
    reads.maybe_audit_read(session, principal, tid, "routing")
    return tid


def _infra(session: Session, principal: Principal, tid: int,
           customer_id: int | None) -> customer_infra.Infra | None:  # fmt: skip
    """Kunde oben gewaehlt (0.76.0): sein Ausschnitt der Infrastruktur, sonst None."""
    return customer_infra.of(session, customer_view(session, principal, tid, customer_id))


def _contexts(infra: customer_infra.Infra, model, *marks: str):
    keys = [k for k, m in infra.contexts.items() if m in marks]
    if not keys:
        return false()
    return or_(*(and_(model.source_system_id == sid, model.context == ctx) for sid, ctx in keys))


def _route_filter(infra: customer_infra.Infra):
    """Routen im Ausschnitt: alle in eigenen Kontexten, in geteilten nur die, die ein Netz des
    Kunden enthalten oder darin liegen (auch die Default-Route)."""
    return or_(
        _contexts(infra, TopoRoute, OWN),
        and_(
            _contexts(infra, TopoRoute, SHARED),
            infra.overlap_clause(TopoRoute.family, TopoRoute.lo, TopoRoute.hi),
        ),
    )


def _change_filter(infra: customer_infra.Infra):
    """Verlauf im Ausschnitt (ADR 0030), wie die Listen: Routen wie `_route_filter`, Nachbarn,
    Policy-Routen, Kontexte und L3Outs in seinen Kontexten, HA an seinen Firewalls, Vermerke des
    ganzen Quellsystems (Ausgangsstand, Obergrenze) bei seinen Firewalls bzw. APICs."""
    systems = sorted({sid for sid, _ in infra.contexts})
    nodes = [
        and_(TopoChange.source_system_id == sid, TopoChange.node == node)
        for sid, node in infra.nodes
    ]
    routes = or_(
        _contexts(infra, TopoChange, OWN),
        and_(
            _contexts(infra, TopoChange, SHARED),
            infra.overlap_clause(TopoChange.family, TopoChange.lo, TopoChange.hi),
        ),
    )
    return or_(
        and_(TopoChange.kind == "route", routes),
        and_(
            TopoChange.kind.in_(("bgp_peer", "policy_route", "context", "l3out")),
            _contexts(infra, TopoChange, OWN, SHARED),
        ),
        and_(TopoChange.kind == "ha", or_(*nodes) if nodes else false()),
        and_(
            TopoChange.kind == "context",
            TopoChange.context.is_(None),
            TopoChange.source_system_id.in_(systems) if systems else false(),
        ),
    )


def _in_view(infra, row: dict, mark: str | None) -> bool:
    """Zeile im Ausschnitt? Markiert geteilte (`shared`)."""
    if infra is None:
        return True
    if mark is None:
        return False
    row["shared"] = mark == SHARED
    return True


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
        detail = L(f"{what}: keine gueltige IP", f"{what}: not a valid IP")
        raise HTTPException(status_code=422, detail=detail) from None


def _endpoint(value: str, what: str):
    """IP bzw. Netz (`10.10.0.0/23`, Weg-Suche 0.39.0)."""
    try:
        return topology.endpoint(value)
    except ValueError:
        detail = L(f"{what}: keine gueltige IP bzw. kein gueltiges Netz",
                   f"{what}: not a valid IP or network")  # fmt: skip
        raise HTTPException(status_code=422, detail=detail) from None


@router.get("/summary")
def summary(
    tenant_id: int | None = None,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Gelesene Kontexte je Quellsystem mit Anzahl Routen (je Tabelle), Nachbarn und Regeln;
    mit Kunde (0.76.0) nur seine Kontexte, geteilte markiert."""
    tid = _tenant(session, principal, tenant_id)
    infra = _infra(session, principal, tid, customer_id)
    names = _names(session, tid)
    graph = paths.Graph(session, tid)
    counts: dict[tuple, dict[str, int]] = defaultdict(dict)
    route_stmt = (
        select(
            TopoRoute.source_system_id,
            TopoRoute.node,
            TopoRoute.context,
            TopoRoute.table,
            func.count(TopoRoute.id),
        )
        .where(TopoRoute.tenant_id == tid)
        .group_by(TopoRoute.source_system_id, TopoRoute.node, TopoRoute.context, TopoRoute.table)
    )
    if infra is not None:
        route_stmt = route_stmt.where(_route_filter(infra))
    for sid, node, ctx, table, n in session.execute(route_stmt):
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
        row
        for c in contexts
        if _in_view(
            infra,
            row := _row(
                c, names, graph, counts=counts.get((c.source_system_id, c.node, c.context), {})
            ),
            infra.context_mark(c.source_system_id, c.context) if infra else None,
        )
    ]
    if infra is None:
        ha = session.scalar(select(func.count(TopoHa.id)).where(TopoHa.tenant_id == tid)) or 0
        l3 = session.scalar(select(func.count(TopoL3Out.id)).where(TopoL3Out.tenant_id == tid)) or 0
    else:
        ha = sum(
            1
            for sid, node in session.execute(
                select(TopoHa.source_system_id, TopoHa.node).where(TopoHa.tenant_id == tid)
            )
            if (sid, node) in infra.nodes
        )
        l3 = sum(
            1
            for sid, vrf in session.execute(
                select(TopoL3Out.source_system_id, TopoL3Out.vrf).where(TopoL3Out.tenant_id == tid)
            )
            if infra.context_mark(sid, vrf)
        )
    nodes = sorted(
        (
            n.out()
            for n in graph.nodes.values()
            if infra is None or infra.context_mark(n.source_system_id, n.context)
        ),
        key=lambda n: n["label"].lower(),
    )
    # VRFs der Routen (FortiGate 7.x: eigene Tabellen im VDOM) fuer den Filter
    vrfs = {
        v or 0
        for v in session.scalars(
            select(TopoRoute.attrs["vrf"].as_integer()).where(TopoRoute.tenant_id == tid).distinct()
        )
    }
    mgmt = session.scalar(
        select(TopoRoute.id)
        .where(TopoRoute.tenant_id == tid, TopoRoute.attrs["mgmt"].as_boolean().is_(True))
        .limit(1)
    )
    # Verlauf (ADR 0030): Aenderungen der letzten 7 Tage (ohne Vermerke)
    recent = select(func.count(TopoChange.id)).where(
        TopoChange.tenant_id == tid,
        TopoChange.at >= datetime.now(UTC) - timedelta(days=7),
        TopoChange.change.in_(topo_changes.CHANGES),
    )
    if infra is not None:
        recent = recent.where(_change_filter(infra))
    return {
        "contexts": rows,
        "nodes": nodes,
        "ha": ha,
        "l3outs": l3,
        "vrfs": sorted(vrfs),
        "mgmt_vrf": mgmt is not None,
        "changes_7d": session.scalar(recent) or 0,
    }


@router.get("/routes")
def routes(
    response: Response,
    tenant_id: int | None = None,
    source_system_id: int | None = None,
    node: str | None = None,
    context: str | None = None,
    table: str | None = Query(None, pattern="^(rib|bgp|static)$"),
    protocol: str | None = None,
    vrf: str | None = Query(
        None, pattern=r"^(\d{1,3}|mgmt)$", description="VRF im VDOM (0 = Standard) bzw. mgmt"
    ),
    ip: str | None = Query(None, description="Routen, die diese IP enthalten"),
    q: str | None = Query(None, description="Praefix, Next-Hop, Interface, Protokoll"),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """Routen seitenweise (Gesamtzahl im Header `X-Total-Count`); mit Kunde (0.76.0) nur in
    seinen Kontexten, in geteilten nur die zu seinen Netzen."""
    tid = _tenant(session, principal, tenant_id)
    infra = _infra(session, principal, tid, customer_id)
    stmt = select(TopoRoute).where(TopoRoute.tenant_id == tid)
    if infra is not None:
        stmt = stmt.where(_route_filter(infra))
    for column, value in (
        (TopoRoute.source_system_id, source_system_id),
        (TopoRoute.node, node),
        (TopoRoute.context, context),
        (TopoRoute.table, table),
        (TopoRoute.protocol, protocol),
    ):
        if value not in (None, ""):
            stmt = stmt.where(column == value)
    if vrf == "mgmt":  # dediziertes Management-Interface (eigene Tabelle)
        stmt = stmt.where(TopoRoute.attrs["mgmt"].as_boolean().is_(True))
    elif vrf is not None:
        number = TopoRoute.attrs["vrf"].as_integer()
        mgmt = TopoRoute.attrs["mgmt"].as_boolean()
        stmt = stmt.where(
            or_(number.is_(None), number == 0) if vrf == "0" else number == int(vrf),
            or_(mgmt.is_(None), ~mgmt),
        )
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
            detail = L("ip: keine gueltige IP", "ip: not a valid IP")
            raise HTTPException(status_code=422, detail=detail)
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
                func.lower(cast(TopoRoute.attrs, String)).like(like),
            )
        )
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    response.headers["X-Total-Count"] = str(total)
    page = list(session.scalars(stmt.order_by(*order).offset(offset).limit(limit)))
    names = _names(session, tid)
    graph = paths.Graph(session, tid)
    ifaces = topology.Interfaces(session, {r.source_system_id for r in page})
    changed = topo_changes.last_changed(session, page)  # geaendert seit (ADR 0030)
    out = []
    for r in page:
        row = _row(r, names, graph, next_hops=_hops(graph, ifaces, r.source_system_id, r.next_hops))
        row["changed_at"] = changed.get(r.id)
        if infra is not None:
            row["shared"] = infra.context_mark(r.source_system_id, r.context) == SHARED
        out.append(row)
    return out


# -- Verlauf des Routings (ADR 0030) -------------------------------------------------------


def _utc(value: datetime | None) -> datetime | None:
    """Zeitpunkt in UTC (ohne Zeitzone angegeben = UTC) - gespeichert ist UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _change_labels(session: Session, tid: int, names: dict[int, str]) -> dict[tuple, tuple]:
    """(Quellsystem, Knoten, Kontext) -> (Art, Bezeichnung) der gelesenen Kontexte."""
    rows = list(
        session.execute(
            select(TopoContext.source_system_id, TopoContext.node, TopoContext.context,
                   TopoContext.kind).where(TopoContext.tenant_id == tid)
        )
    )  # fmt: skip
    vdoms = Counter(sid for sid, _, _, kind in rows if kind == "vdom")
    return {
        (sid, node, ctx): paths.context_label(
            kind, names.get(sid, f"#{sid}"), node, ctx, vdoms[sid] == 1
        )
        for sid, node, ctx, kind in rows
    }


def _change_where(c: TopoChange, source: str, labels: dict) -> tuple[str, str]:
    """Bezeichnung wie im Routing; ein Kontext, der nicht mehr gelesen wird, bzw. ein L3Out mit
    seiner VRF, HA mit der Firewall, Vermerke mit dem Quellsystem."""
    if c.node and c.context:
        return labels.get((c.source_system_id, c.node, c.context), ("", f"{c.node} · {c.context}"))
    if c.context:
        return "vrf", f"{source} · VRF {c.context}"
    if c.node:
        return "firewall", c.node
    return "", source


@router.get("/changes")
def changes(
    response: Response,
    tenant_id: int | None = None,
    source_system_id: int | None = None,
    context: str | None = Query(None, max_length=200, description="VDOM bzw. Tenant:VRF"),
    kind: str | None = Query(None, pattern="^(route|bgp_peer|ha|policy_route|context|l3out)$"),
    change: str | None = Query(
        None,
        pattern="^(added|removed|changed|baseline|unreadable|truncated|note)$",
        description="note = alle Vermerke (Ausgangsstand, nicht lesbar, Obergrenze)",
    ),
    q: str | None = Query(None, max_length=200, description="Praefix, Next-Hop, Nachbar, Name"),
    since: datetime | None = None,  # ab diesem Zeitpunkt (ISO 8601, ohne Zeitzone = UTC)
    until: datetime | None = None,  # vor diesem Zeitpunkt
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """Verlauf des Routings seitenweise (Gesamtzahl im Header `X-Total-Count`), neuste zuerst
    und je Lauf Wichtiges zuerst; je Eintrag die verglichenen Felder vorher/nachher als Text
    (`fields`) und ein Kurztext (`summary`) in der Sprache der Anfrage. Mit Kunde (0.76.0) nur
    sein Ausschnitt; Kunden-Benutzer erreichen den Verlauf nicht (Freigabeliste)."""
    tid = _tenant(session, principal, tenant_id)
    infra = _infra(session, principal, tid, customer_id)
    stmt = select(TopoChange).where(TopoChange.tenant_id == tid)
    if infra is not None:
        stmt = stmt.where(_change_filter(infra))
    for column, value in (
        (TopoChange.source_system_id, source_system_id),
        (TopoChange.context, context),
        (TopoChange.kind, kind),
    ):
        if value not in (None, ""):
            stmt = stmt.where(column == value)
    if change == "note":
        stmt = stmt.where(TopoChange.change.in_(topo_changes.NOTES))
    elif change:
        stmt = stmt.where(TopoChange.change == change)
    if (start := _utc(since)) is not None:
        stmt = stmt.where(TopoChange.at >= start)
    if (end := _utc(until)) is not None:
        stmt = stmt.where(TopoChange.at < end)
    for token in (q or "").lower().split():
        like = f"%{token}%"
        stmt = stmt.where(
            or_(
                func.lower(TopoChange.key).like(like),
                func.lower(TopoChange.node).like(like),
                func.lower(TopoChange.context).like(like),
                func.lower(cast(TopoChange.before, String)).like(like),
                func.lower(cast(TopoChange.after, String)).like(like),
            )
        )
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    response.headers["X-Total-Count"] = str(total)
    page = list(
        session.scalars(
            stmt.order_by(TopoChange.at.desc(), TopoChange.id).offset(offset).limit(limit)
        )
    )
    names = _names(session, tid)
    labels = _change_labels(session, tid, names)
    run_ids = {c.collector_run_id for c in page if c.collector_run_id}
    runs = (
        set(session.scalars(select(CollectorRun.id).where(CollectorRun.id.in_(run_ids))))
        if run_ids
        else set()
    )
    out = []
    for c in page:
        source = names.get(c.source_system_id, f"#{c.source_system_id}")
        display, label = _change_where(c, source, labels)
        fields = topo_changes.fields(c)
        row = {
            "id": c.id,
            "at": topo_changes.aware(c.at),
            "source_system_id": c.source_system_id,
            "source": source,
            "collector_run_id": c.collector_run_id,
            "run_exists": c.collector_run_id in runs,  # Laeufe haben eine kuerzere Frist
            "kind": c.kind,
            "node": c.node,
            "context": c.context,
            "display": display,
            "label": label,
            "table": c.table,
            "key": c.key,
            "prefix": c.prefix,
            "change": c.change,
            "before": c.before,
            "after": c.after,
            "fields": fields,
            "summary": topo_changes.summary(c, fields),
        }
        if infra is not None:
            row["shared"] = _change_mark(infra, c) == SHARED
        out.append(row)
    return out


def _change_mark(infra: customer_infra.Infra, c: TopoChange) -> str | None:
    """Eigen bzw. geteilt: HA nach der Firewall, Vermerke des ganzen Quellsystems geteilt."""
    if c.kind == "ha":
        return infra.nodes.get((c.source_system_id, c.node or ""))
    if c.context is None:
        return SHARED
    return infra.context_mark(c.source_system_id, c.context)


@router.get("/lookup")
def lookup(
    ip: str,
    tenant_id: int | None = None,
    source_system_id: int | None = None,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """Je Kontext die wirksame Route fuer `ip` (bzw. ein ganzes Netz) und Policy-Routen, die
    vorgehen (koennen); mit Kunde (0.76.0) nur seine Kontexte, geteilte nur fuer seine Netze."""
    tid = _tenant(session, principal, tenant_id)
    _endpoint(ip, "ip")
    infra = _infra(session, principal, tid, customer_id)
    names = _names(session, tid)
    graph = paths.Graph(session, tid)
    out = []
    for row in topology.lookup(session, tid, ip, source_system_id):
        if infra is not None:
            mark = infra.context_mark(row["source_system_id"], row["context"])
            if mark is None or (mark == SHARED and not infra.overlaps(ip)):
                continue
            row["shared"] = mark == SHARED
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
    service: str | None = Query(
        None, max_length=200, description="Dienst, z. B. tcp/443, bzw. Service-Objekt FT_https"
    ),
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Weg zum Ziel Hop fuer Hop - ab der Quelle (dort angeschlossen) bzw. dem Startpunkt;
    Quelle und Ziel als IP bzw. Netz (das Netz als Ganzes, 0.39.0)."""
    tid = _tenant(session, principal, tenant_id)
    _endpoint(dst, "dst")
    if src:
        _endpoint(src, "src")
    if not src and not start:
        raise HTTPException(status_code=422, detail=L("src oder start angeben",
                                                      "specify src or start"))  # fmt: skip
    wanted, note = _service(session, tid, service)
    graph = paths.Graph(session, tid)
    result = paths.trace(session, tid, dst, src, start, slack, wanted, graph=graph)
    if note:
        result["notes"].insert(0, note)
    if service and wanted is not None and result["service"] != service.strip().lower():
        result["service_name"] = service.strip()
    # Zonen je Hop, Uebergaenge und Absicht Quell-/Zielzone (0.57.0)
    zone_path.annotate(session, tid, result, src, dst, wanted, result.get("service_name"), graph)
    # L2 zwischen den Hops, Anschluss von Quelle und Ziel (0.64.0)
    l2_topo.annotate(session, tid, result, src, dst)
    return result


def _service(session: Session, tid: int, text: str | None) -> tuple[tuple | None, str | None]:
    """Dienst als `tcp/443` bzw. als Service-Objekt/ACI-Filter (`FT_https`): geprueft wird
    der erste Dienst des Objekts; mehrere stehen im Hinweis."""
    try:
        return policy_check.parse_service(text), None
    except ValueError as exc:
        values = resolve.service_values(session, tid, text or "")
        if values is None:
            name = (text or "").strip()
            detail = L(f"{exc}; kein Service-Objekt bzw. ACI-Filter „{name}“",
                       f"{exc}; no service object or ACI filter “{name}”")  # fmt: skip
            raise HTTPException(status_code=422, detail=detail) from None
    usable = []
    for v in values:
        try:
            if (parsed := policy_check.parse_service(v)) is not None:
                usable.append((v, parsed))
        except ValueError:
            continue
    name = (text or "").strip()
    if not usable:
        shown = ", ".join(values) or L("keine Dienste", "no services")
        return None, L(
            f"{name} ({shown}) ist nicht als Port prüfbar – ohne Dienst geprüft",
            f"{name} ({shown}) cannot be checked as a port – checked without service",
        )
    if len(usable) > 1:
        rest = ", ".join(v for v, _ in usable[1:])
        first = usable[0][0]
        return usable[0][1], L(f"{name}: geprüft {first} – weitere Dienste: {rest}",
                               f"{name}: checked {first} – more services: {rest}")  # fmt: skip
    return usable[0][1], None


@router.get("/resolve")
def resolve_name(
    q: str = Query(..., min_length=1, max_length=200, description="Name, FQDN, IP bzw. Netz"),
    tenant_id: int | None = None,
    only: str | None = Query(None, alias="type", pattern="^(host|net)$"),
    limit: int = Query(resolve.LIMIT, ge=1, le=500),
    offset: int = Query(0, ge=0),
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Hosts und Netze zu einem Namen bzw. FQDN aus IPAM, Firewall-Objekten, VMs und dem
    Inventar (Weg-Suche): seitenweise mit Gesamtzahl und Anzahl je Art; mit Kunde (0.76.0)
    nur in seinen Netzen (geteilte markiert), eine eingegebene IP ohne fremden Namen."""
    tid = _tenant(session, principal, tenant_id)
    infra = _infra(session, principal, tid, customer_id)
    keep = None
    if infra is not None:

        def keep(hit: dict) -> bool:
            mark = infra.net_mark(hit.get("ip"))
            if mark is None:
                if hit.get("kind") != "ip":
                    return False
                hit.update(name=None, also=None)  # eingegebene IP bleibt, ohne fremden Namen
                return True
            hit["shared"] = mark == SHARED
            return True

    return resolve.resolve(session, tid, q, limit, offset, only, keep)


def _list(session: Session, tid: int, model, source_system_id: int | None, order) -> list:
    stmt = select(model).where(model.tenant_id == tid)
    if source_system_id is not None:
        stmt = stmt.where(model.source_system_id == source_system_id)
    return list(session.scalars(stmt.order_by(*order)))


@router.get("/peers")
def peers(
    tenant_id: int | None = None,
    source_system_id: int | None = None,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """BGP-Nachbarn mit Namen aus dem IPAM und - falls gelesen - dem Knoten dahinter."""
    tid = _tenant(session, principal, tenant_id)
    infra = _infra(session, principal, tid, customer_id)
    order = (TopoPeer.source_system_id, TopoPeer.node, TopoPeer.context, TopoPeer.peer_ip)
    names = _names(session, tid)
    graph = paths.Graph(session, tid)
    return [
        row
        for p in _list(session, tid, TopoPeer, source_system_id, order)
        if _in_view(
            infra,
            row := _row(
                p,
                names,
                graph,
                peer=graph.resolve(p.peer_ip),
                peer_node=graph.owner_label(p.peer_ip),
            ),  # fmt: skip
            infra.context_mark(p.source_system_id, p.context) if infra else None,
        )
    ]


@router.get("/ha")
def ha(
    tenant_id: int | None = None,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    tid = _tenant(session, principal, tenant_id)
    infra = _infra(session, principal, tid, customer_id)
    names = _names(session, tid)
    graph = paths.Graph(session, tid)
    rows = _list(session, tid, TopoHa, None, (TopoHa.source_system_id, TopoHa.node))
    out = []
    for h in rows:
        row = _row(h, names)
        if not _in_view(
            infra, row, infra.nodes.get((h.source_system_id, h.node)) if infra else None
        ):
            continue
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
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    tid = _tenant(session, principal, tenant_id)
    infra = _infra(session, principal, tid, customer_id)
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
        row
        for p in _list(session, tid, TopoPolicyRoute, source_system_id, order)
        if _in_view(
            infra,
            row := _row(p, names, graph, gateway_resolved=graph.resolve(p.gateway)),
            infra.context_mark(p.source_system_id, p.context) if infra else None,
        )
    ]


@router.get("/l3outs")
def l3outs(
    tenant_id: int | None = None,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """L3Outs mit Border-Leafs (Namen aus dem Inventar), Peers und Next-Hops mit IPAM-Namen."""
    tid = _tenant(session, principal, tenant_id)
    infra = _infra(session, principal, tid, customer_id)
    order = (TopoL3Out.source_system_id, TopoL3Out.aci_tenant, TopoL3Out.name)
    names = _names(session, tid)
    graph = paths.Graph(session, tid)
    rows = _list(session, tid, TopoL3Out, None, order)
    leafs = paths.device_names(session, {o.source_system_id for o in rows})
    # Firewall dahinter (ADR 0034): abgeleitet bzw. von Hand
    behind = {(o.source_system_id, o.dn): o
              for o in l3out_firewall.Behind(session, tid, graph).l3outs}  # fmt: skip
    out = []
    for o in rows:
        row = _row(o, names)
        sid = o.source_system_id
        if not _in_view(infra, row, infra.context_mark(sid, o.vrf) if infra else None):
            continue
        found = behind.get((sid, o.dn))
        row["behind"] = found.out(graph) if found else None
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


# -- Firewall hinter einem L3Out (ADR 0034) -------------------------------------------------


class FirewallRef(BaseModel):
    source_system_id: int
    vdom: str = Field(min_length=1, max_length=100)  # Kontext wie im Weg (root bei nur root)
    interface: str | None = Field(default=None, max_length=200)  # Interface zum L3Out


class L3OutFirewallIn(BaseModel):
    tenant_id: int
    source_system_id: int  # APIC
    dn: str = Field(min_length=1, max_length=400)  # DN des L3Outs
    # confirm: die abgeleitete Zuordnung bestaetigen; none: ausdruecklich keine Firewall;
    # sonst die gewaehlten Firewalls
    confirm: bool = False
    none: bool = False
    firewalls: list[FirewallRef] = Field(default_factory=list, max_length=16)


def _l3out(session: Session, principal: Principal, tenant_id: int, aci_id: int, dn: str):
    tenant_scope(principal, tenant_id)
    found = l3out_firewall.find(session, tenant_id, aci_id, dn)
    if found is None:
        raise HTTPException(status_code=404, detail="l3out not found")
    return found


def _audit_l3out(session: Session, principal: Principal, action: str, o: TopoL3Out,
                 tenant_id: int, **extra) -> None:  # fmt: skip
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action=f"routing.l3out_firewall.{action}",
        subject=o.dn,
        detail={
            "tenant_id": tenant_id,
            "source_system_id": o.source_system_id,
            "dn": o.dn,
            "l3out": o.name,
            **extra,
        },  # fmt: skip
    )


def _behind_out(session: Session, tid: int, o: TopoL3Out) -> dict | None:
    graph = paths.Graph(session, tid)
    found = l3out_firewall.Behind(session, tid, graph).of(o.source_system_id, o.aci_tenant, o.name)
    return found.out(graph) if found else None


@router.get("/l3outs/firewall-choices")
def l3out_firewall_choices(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """Gelesene Firewalls bzw. VDOMs mit ihren Interfaces (ohne Management) - zur Auswahl der
    Firewall hinter einem L3Out (ADR 0034)."""
    tid = _tenant(session, principal, tenant_id)
    return l3out_firewall.choices(session, tid)


@router.put("/l3outs/firewall")
def set_l3out_firewall(
    body: L3OutFirewallIn,
    background: BackgroundTasks,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
    factory: sessionmaker = Depends(get_session_factory),
) -> dict:
    """Firewall hinter einem L3Out von Hand (Operator, ADR 0034): die abgeleitete bestaetigen,
    eine bzw. mehrere waehlen (VDOM, Interface zum L3Out) oder ausdruecklich keine. Nur Daten
    der Plattform; Befunde der Applikationen und Zonen sowie die Kunden ziehen danach nach."""
    o = _l3out(session, principal, body.tenant_id, body.source_system_id, body.dn)
    graph = paths.Graph(session, body.tenant_id)
    behind = l3out_firewall.Behind(session, body.tenant_id, graph)
    current = behind.of(o.source_system_id, o.aci_tenant, o.name)
    derived = current.derived if current else []
    if body.confirm:
        if not derived:
            detail = L("keine abgeleitete Firewall zum Bestätigen",
                       "no derived firewall to confirm")  # fmt: skip
            raise HTTPException(status_code=422, detail=detail)
        chosen, origin = list(derived), l3out_firewall.CONFIRMED
    elif body.none:
        chosen, origin = [], l3out_firewall.NONE
    else:
        if not body.firewalls:
            detail = L("Firewall wählen, bestätigen oder „keine“",
                       "choose or confirm a firewall, or “none”")  # fmt: skip
            raise HTTPException(status_code=422, detail=detail)
        choices = {(c["source_system_id"], c["vdom"]): c
                   for c in l3out_firewall.choices(session, body.tenant_id, graph)}  # fmt: skip
        chosen = []
        for f in body.firewalls:
            c = choices.get((f.source_system_id, f.vdom))
            if c is None:
                detail = L(f"keine gelesene Firewall {f.source_system_id}|{f.vdom}",
                           f"no firewall read {f.source_system_id}|{f.vdom}")  # fmt: skip
                raise HTTPException(status_code=422, detail=detail)
            iface = (f.interface or "").strip() or None
            known = {i["name"]: i for i in c["interfaces"]}
            if iface is not None and iface not in known:
                detail = L(f"Interface {iface} gibt es auf {c['label']} nicht",
                           f"interface {iface} does not exist on {c['label']}")  # fmt: skip
                raise HTTPException(status_code=422, detail=detail)
            zone = known[iface]["zone"] if iface else None
            ref = l3out_firewall.FwRef(f.source_system_id, f.vdom, iface, zone)
            if not any(ref.same(x) for x in chosen):
                chosen.append(ref)
        same = len(chosen) == len(derived) and all(any(c.same(d) for d in derived) for c in chosen)
        origin = l3out_firewall.CONFIRMED if same else l3out_firewall.MANUAL
    l3out_firewall.assign(session, body.tenant_id, o, chosen, origin, f"user:{principal.subject}")
    _audit_l3out(session, principal, "set", o, body.tenant_id, origin=origin,
                 firewalls=[{"source_system_id": f.source_system_id, "vdom": f.context,
                             "interface": f.interface} for f in chosen])  # fmt: skip
    app_refresh.schedule_tenant(background, factory, body.tenant_id)
    return {"dn": o.dn, "behind": _behind_out(session, body.tenant_id, o)}


@router.delete("/l3outs/firewall")
def remove_l3out_firewall(
    background: BackgroundTasks,
    tenant_id: int,
    source_system_id: int,
    dn: str = Query(..., min_length=1, max_length=400),
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
    factory: sessionmaker = Depends(get_session_factory),
) -> dict:
    """Zuordnung von Hand entfernen (Operator): es gilt wieder die Ableitung."""
    o = _l3out(session, principal, tenant_id, source_system_id, dn)
    removed = l3out_firewall.clear(session, o.source_system_id, o.dn)
    if removed:
        _audit_l3out(session, principal, "removed", o, tenant_id)
        app_refresh.schedule_tenant(background, factory, tenant_id)
    return {"dn": o.dn, "removed": removed, "behind": _behind_out(session, tenant_id, o)}


# -- Anbindungen nach aussen (0.18.0, von Hand erfasst) ------------------------------------

EXIT_KINDS = ("internet", "mpls", "wan", "partner", "other")


class ExitRoute(BaseModel):
    prefix: str = Field(max_length=64)
    target: str = Field(max_length=300)  # Knoten <Quellsystem>|<Kontext>


class ExitIn(BaseModel):
    tenant_id: int
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["internet", "mpls", "wan", "partner", "other"] = "other"
    source_system_ids: list[int] = []  # nur fuer diese Firewalls (leer = jede)
    interfaces: list[str] = []  # Interfaces bzw. Zonen
    next_hops: list[str] = []  # IPs der Gegenstelle
    prefixes: list[str] = []  # Netze dahinter (leer = alles, was dorthin geroutet wird)
    routes: list[ExitRoute] = []  # manuell: Praefix -> weiter beim gelesenen Knoten
    description: str | None = Field(default=None, max_length=2000)


def _net(value: str, what: str) -> str:
    try:
        return str(ipaddress.ip_network(value.strip(), strict=False))
    except ValueError:
        detail = L(f"{what}: kein gueltiges Netz", f"{what}: not a valid network")
        raise HTTPException(status_code=422, detail=detail) from None


def _clean(session: Session, body: ExitIn) -> dict:
    """Eingaben pruefen und vereinheitlichen (Netze in CIDR, Next-Hops als IP)."""
    sids = sorted(set(body.source_system_ids))
    if sids:
        known = set(
            session.scalars(
                select(SourceSystem.id).where(
                    SourceSystem.id.in_(sids), SourceSystem.tenant_id == body.tenant_id
                )
            )
        )
        if known != set(sids):
            raise HTTPException(status_code=422, detail="source system not in tenant")
    hops = [str(_ip(h, "next_hops")) for h in body.next_hops if h.strip()]
    ifaces = [i.strip() for i in body.interfaces if i.strip()]
    if not hops and not ifaces:
        detail = L("Next-Hop oder Interface/Zone angeben", "specify a next-hop or interface/zone")
        raise HTTPException(status_code=422, detail=detail)
    routes = []
    for r in body.routes:
        if paths.parse_key(r.target) is None:
            detail = L("routes.target: <Quellsystem>|<Kontext>",
                       "routes.target: <source system>|<context>")  # fmt: skip
            raise HTTPException(status_code=422, detail=detail)
        routes.append({"prefix": _net(r.prefix, "routes.prefix"), "target": r.target})
    return {
        "name": body.name.strip(),
        "kind": body.kind,
        "source_system_ids": sids,
        "interfaces": ifaces,
        "next_hops": hops,
        "prefixes": [_net(p, "prefixes") for p in body.prefixes if p.strip()],
        "routes": routes,
        "description": (body.description or "").strip() or None,
    }


def _exit_out(e: TopoExit, names: dict[int, str], status: dict | None = None) -> dict:
    ids = e.source_system_ids or []
    return {
        "id": e.id,
        "tenant_id": e.tenant_id,
        "name": e.name,
        "kind": e.kind,
        "source_system_ids": ids,
        "sources": [names.get(i, f"#{i}") for i in ids],
        "interfaces": e.interfaces or [],
        "next_hops": e.next_hops or [],
        "prefixes": e.prefixes or [],
        "routes": e.routes or [],
        "description": e.description,
        "updated_at": e.updated_at,
        **({"status": status} if status is not None else {}),
    }


def _audit_exit(session: Session, principal: Principal, action: str, e: TopoExit) -> None:
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action=f"routing.exit.{action}",
        subject=str(e.id),
        detail={"tenant_id": e.tenant_id, "exit_id": e.id, "name": e.name, "kind": e.kind},
    )


def _exit_mark(infra: customer_infra.Infra, sids: list[int] | None, prefixes) -> str | None:
    """Anbindung im Ausschnitt (0.76.0): an einer Firewall mit Kontext des Kunden bzw. an jeder;
    eigen nur, wenn alle Netze dahinter Netze des Kunden sind - sonst geteilt."""
    fws = {sid for sid, _ in infra.contexts}
    if sids and not set(sids) & fws:
        return None
    marks = [infra.net_mark(p) for p in prefixes or []]
    return OWN if marks and all(m == OWN for m in marks) else SHARED


@router.get("/exits")
def list_exits(
    tenant_id: int | None = None,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """Anbindungen mit den erkannten Routern, die sie abdecken (`status`)."""
    tid = _tenant(session, principal, tenant_id)
    infra = _infra(session, principal, tid, customer_id)
    names = _names(session, tid)
    found = exits.status(session, tid)
    rows = session.scalars(
        select(TopoExit).where(TopoExit.tenant_id == tid).order_by(TopoExit.name, TopoExit.id)
    )
    return [
        row
        for e in rows
        if _in_view(
            infra,
            row := _exit_out(e, names, found.get(e.id)),
            _exit_mark(infra, e.source_system_ids, e.prefixes) if infra else None,
        )
    ]


@router.get("/exits/suggestions")
def exit_suggestions(
    tenant_id: int | None = None,
    covered: bool = Query(False, description="auch schon ganz abgedeckte"),
    dismissed: bool = Query(False, description="auch ausgeblendete"),
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """Erkannte Anbindungen: Router (Next-Hops, BGP-Nachbarn) ohne gelesenes Geraet, je
    Firewall bzw. Verbund zusammengefasst, mit aktiv/Backup/BGP-Zustand - nur gelesene Daten."""
    tid = _tenant(session, principal, tenant_id)
    infra = _infra(session, principal, tid, customer_id)
    rows = exits.suggestions(session, tid, include_covered=covered, include_dismissed=dismissed)
    if infra is None:
        return rows
    fws = {sid for sid, _ in infra.contexts}
    out = []
    for r in rows:
        if set(r.get("source_system_ids") or []) & fws:
            r["shared"] = True  # Router der Umgebung, nicht des Kunden allein
            out.append(r)
    return out


class DismissIn(BaseModel):
    tenant_id: int
    ips: list[str] = Field(min_length=1, max_length=500)  # Router des Vorschlags


def _dismiss(session: Session, principal: Principal, body: DismissIn, undo: bool) -> dict:
    tenant_scope(principal, body.tenant_id)
    if session.get(Tenant, body.tenant_id) is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    ips = sorted({str(_ip(ip, "ips")) for ip in body.ips})
    fn = exits.undismiss if undo else exits.dismiss
    changed = fn(session, body.tenant_id, ips)
    if changed:
        audit.record(
            session,
            actor=f"user:{principal.subject}",
            action=f"routing.exit.{'undismissed' if undo else 'dismissed'}",
            subject=str(body.tenant_id),
            detail={"tenant_id": body.tenant_id, "ips": changed},
        )
    return {"ips": changed}


@router.post("/exits/dismiss")
def dismiss_exit(
    body: DismissIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Erkannte Router nicht mehr als Anbindung vorschlagen (Operator) - nur Daten der
    Plattform, die Wege bleiben gleich."""
    return _dismiss(session, principal, body, undo=False)


@router.post("/exits/dismiss/undo")
def undismiss_exit(
    body: DismissIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Ausgeblendete Router wieder vorschlagen (Operator)."""
    return _dismiss(session, principal, body, undo=True)


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
