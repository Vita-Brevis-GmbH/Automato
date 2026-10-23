"""Routing, BGP, HA und L3Out speichern und abfragen (ADR 0019 Stufe 2).

`replace` ersetzt je Lauf den Stand des Quellsystems. `lookup` sucht je Routing-Kontext die
wirksame Route fuer eine IP (laengstes Praefix der RIB) und die Policy-Routen bzw. SD-WAN-Regeln,
die fuer sie vorgehen koennen - die Grundlage fuer die Pfade (Stufe 3).
"""

import ipaddress
from collections import defaultdict

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.models import (
    TOPOLOGY_MODELS,
    InvInterface,
    TopoContext,
    TopoHa,
    TopoL3Out,
    TopoPeer,
    TopoPolicyRoute,
    TopoRoute,
)
from app.schemas.collector import RoutingInventory

MGMT = "mgmt"  # Tabelle der dedizierten Management-Interfaces (FortiGate)
Table = int | str  # VRF innerhalb des Kontexts (FortiGate 0-251) bzw. MGMT


def table_of(route: TopoRoute) -> Table:
    """VRF einer Route (fehlt = 0, 0.17.1); Routen ueber Management-Interfaces eigene Tabelle."""
    attrs = route.attrs or {}
    return MGMT if attrs.get("mgmt") else int(attrs.get("vrf") or 0)


def ip_hex(ip) -> str:
    """Wie im Regelindex: IP als Hex fester Breite (Textvergleich = numerischer Vergleich)."""
    return f"{int(ip):032x}"


# -- IP bzw. Netz (0.39.0): ein Netz als Ganzes in Routen, Policies und Contracts ----------

Net = ipaddress.IPv4Network | ipaddress.IPv6Network


def endpoint(text: str):
    """IP bzw. Netz (`10.10.0.0/23`) als Adresse bzw. Netz; ein /32 bzw. /128 ist die IP.
    Ungueltig -> ValueError."""
    text = text.strip()
    if "/" in text:
        net = ipaddress.ip_network(text, strict=False)
        return net.network_address if net.num_addresses == 1 else net
    return ipaddress.ip_address(text)


def is_net(x) -> bool:
    return isinstance(x, ipaddress.IPv4Network | ipaddress.IPv6Network)


def span(x) -> tuple[int, int]:
    """Erste und letzte Adresse einer IP bzw. eines Netzes als Zahl."""
    if is_net(x):
        return int(x.network_address), int(x.broadcast_address)
    return int(x), int(x)


def within(x, net) -> bool:
    """IP bzw. Netz liegt ganz im Netz `net` (`10.10.0.0/23` in `10.10.0.0/16`)."""
    if x.version != net.version:
        return False
    lo, hi = span(x)
    return int(net.network_address) <= lo and hi <= int(net.broadcast_address)


def overlaps(x, net) -> bool:
    """IP bzw. Netz und `net` haben Adressen gemeinsam."""
    if x.version != net.version:
        return False
    lo, hi = span(x)
    return int(net.network_address) <= hi and lo <= int(net.broadcast_address)


def route_range(prefix: str) -> tuple[int, int, str, str, str] | None:
    """(Familie, Laenge, von, bis, CIDR) eines Praefixes; ungueltig -> None."""
    try:
        net = ipaddress.ip_network(prefix.strip(), strict=False)
    except ValueError:
        return None
    lo, hi = ip_hex(net.network_address), ip_hex(net.broadcast_address)
    return net.version, net.prefixlen, lo, hi, str(net)


def replace(session: Session, run, inv: RoutingInventory) -> None:
    """Routing-Stand des Quellsystems ersetzen (wie das Netzinventar)."""
    if run.source_system_id is None or run.tenant_id is None:
        return
    for model in TOPOLOGY_MODELS:
        session.execute(delete(model).where(model.source_system_id == run.source_system_id))
    base = {"tenant_id": run.tenant_id, "source_system_id": run.source_system_id}
    contexts = [
        {**base, "node": c.node, "context": c.context, "kind": c.kind, "missing": c.missing or None}
        for c in inv.contexts
    ]
    routes = []
    for r in inv.routes:
        rng = route_range(r.prefix)
        if rng is None:
            continue
        family, length, lo, hi, cidr = rng
        routes.append(
            {
                **base,
                "node": r.node,
                "context": r.context,
                "family": family,
                "prefix": cidr,
                "prefix_len": length,
                "lo": lo,
                "hi": hi,
                "table": r.table,
                "protocol": r.protocol,
                "best": r.best,
                "distance": r.distance,
                "metric": r.metric,
                "next_hops": [h.model_dump(exclude_none=True) for h in r.next_hops] or None,
                "attrs": r.attrs or None,
            }
        )
    peers = [
        {**base, **p.model_dump(exclude={"attrs"}), "attrs": p.attrs or None} for p in inv.peers
    ]
    ha = [
        {
            **base,
            **h.model_dump(exclude={"members", "sync_peers", "attrs"}),
            "members": h.members or None,
            "sync_peers": h.sync_peers or None,
            "attrs": h.attrs or None,
        }
        for h in inv.ha
    ]
    policy = [
        {
            **base,
            **p.model_dump(exclude={"match", "output_device", "attrs"}),
            "match": p.match or None,
            "output_device": p.output_device or None,
            "attrs": p.attrs or None,
        }
        for p in inv.policy_routes
    ]
    l3outs = [
        {
            **base,
            "aci_tenant": o.tenant,
            "name": o.name,
            "dn": o.dn,
            "vrf": o.vrf,
            "nodes": o.nodes or None,
            "paths": o.paths or None,
            "peers": o.peers or None,
            "ext_epgs": o.ext_epgs or None,
        }
        for o in inv.l3outs
    ]
    for model, rows in (
        (TopoContext, contexts),
        (TopoRoute, routes),
        (TopoPeer, peers),
        (TopoHa, ha),
        (TopoPolicyRoute, policy),
        (TopoL3Out, l3outs),
    ):
        if rows:
            session.execute(insert(model), rows)
    session.flush()


class Interfaces:
    """Interface -> Zone und VDOM (FortiGate-Inventar) zum Anreichern der Next-Hops."""

    def __init__(self, session: Session, source_ids: set[int]):
        self._by: dict[tuple[int, str], InvInterface] = {}
        if source_ids:
            for i in session.scalars(
                select(InvInterface).where(InvInterface.source_system_id.in_(source_ids))
            ):
                self._by.setdefault((i.source_system_id, i.name), i)

    def hops(self, source_id: int, hops: list | None) -> list[dict]:
        out = []
        for h in hops or []:
            iface = self._by.get((source_id, h.get("interface") or ""))
            out.append({**h, **({"zone": iface.zone} if iface is not None and iface.zone else {})})
        return out


def matches(kind: str, match: dict | None, side: str, ip) -> bool | None:
    """Passt Quelle (`side="src"`) bzw. Ziel (`"dst"`) einer Policy-Route bzw. SD-WAN-Regel
    zur IP bzw. zum ganzen Netz? True/False; None = nur ueber Objekte (Namen) bestimmt bzw. nur
    ein Teil des Netzes - dann „moeglich“.

    Policy-Routen tragen Subnetze (`src`/`dst`) und Adressobjekte (`srcaddr`/`dstaddr`),
    SD-WAN-Regeln nur Adressobjekte (`src`/`dst`); beim Ziel zaehlen Internet-Services mit.
    """
    match = match or {}
    nets = [] if kind == "sdwan" else list(match.get(side) or [])
    names = list(match.get(f"{side}addr") or [])
    if kind == "sdwan":
        names += match.get(side) or []
    if side == "dst":
        names += match.get("service") or []
    if ip is None:  # unbekannt (z. B. Quelle nicht angegeben): sicher nur ohne Einschraenkung
        return None if nets or names else True
    partial = False  # Netz (0.39.0): nur ein Teil davon passt
    for net in nets:
        try:
            net = ipaddress.ip_network(net, strict=False)
        except (ValueError, TypeError):
            continue
        if within(ip, net):
            return True
        partial = partial or overlaps(ip, net)
    if names or partial:
        return None
    return False if nets else True


def lookup(
    session: Session, tenant_id: int, ip_text: str, source_system_id: int | None = None
) -> list[dict]:
    """Je Kontext: wirksame Route (laengstes Praefix der RIB, ECMP = alle Next-Hops) und die
    Policy-Routen bzw. SD-WAN-Regeln, deren Ziel passt bzw. passen kann. Weitere VRFs des
    Kontexts (FortiGate, Management) mit einer passenden Route stehen als eigene Zeile da
    (`vrf`); die Policy-Routen gehoeren zur Standard-VRF."""
    try:
        ip = endpoint(ip_text)  # IP bzw. Netz: Routen, die das ganze Netz enthalten
    except ValueError:
        return []
    lo, hi = (ip_hex(v) for v in span(ip))
    stmt = select(TopoRoute).where(
        TopoRoute.tenant_id == tenant_id,
        TopoRoute.family == ip.version,
        TopoRoute.table == "rib",
        TopoRoute.best.is_(True),
        TopoRoute.lo <= lo,
        TopoRoute.hi >= hi,
    )
    ctx_stmt = select(TopoContext).where(TopoContext.tenant_id == tenant_id)
    pol_stmt = select(TopoPolicyRoute).where(
        TopoPolicyRoute.tenant_id == tenant_id, TopoPolicyRoute.enabled.is_(True)
    )
    if source_system_id is not None:
        stmt = stmt.where(TopoRoute.source_system_id == source_system_id)
        ctx_stmt = ctx_stmt.where(TopoContext.source_system_id == source_system_id)
        pol_stmt = pol_stmt.where(TopoPolicyRoute.source_system_id == source_system_id)
    best: dict[tuple, TopoRoute] = {}
    for r in session.scalars(stmt):
        key = (r.source_system_id, r.node, r.context, table_of(r))
        cur = best.get(key)
        if cur is None or (r.prefix_len, -(r.distance or 0)) > (
            cur.prefix_len,
            -(cur.distance or 0),
        ):
            best[key] = r
    policies: dict[tuple, list[dict]] = defaultdict(list)
    # FortiOS prueft Policy-Routen vor SD-WAN-Regeln, beide vor der Routing-Tabelle
    for p in session.scalars(pol_stmt.order_by(TopoPolicyRoute.kind, TopoPolicyRoute.seq)):
        fits = matches(p.kind, p.match, "dst", ip)
        if fits is False:
            continue
        policies[(p.source_system_id, p.node, p.context)].append(
            {
                "kind": p.kind,
                "seq": p.seq,
                "name": p.name,
                "match": p.match or {},
                "gateway": p.gateway,
                "output_device": p.output_device or [],
                "certain": fits is True,
            }
        )
    contexts = list(
        session.scalars(
            ctx_stmt.order_by(TopoContext.source_system_id, TopoContext.node, TopoContext.context)
        )
    )
    ifaces = Interfaces(session, {c.source_system_id for c in contexts})
    others: dict[tuple, list[Table]] = defaultdict(list)
    for sid, node, context, table in best:
        if table != 0:
            others[(sid, node, context)].append(table)
    out = []
    for c in contexts:
        key = (c.source_system_id, c.node, c.context)
        tables: list[Table] = [0, *sorted(others.get(key, []), key=str)]
        for table in tables:
            r = best.get((*key, table))
            out.append(
                {
                    "source_system_id": c.source_system_id,
                    "node": c.node,
                    "context": c.context,
                    "kind": c.kind,
                    "vrf": None if table == 0 else table,
                    "missing": c.missing or [],
                    "route": None
                    if r is None
                    else {
                        "id": r.id,
                        "prefix": r.prefix,
                        "protocol": r.protocol,
                        "distance": r.distance,
                        "metric": r.metric,
                        "next_hops": ifaces.hops(r.source_system_id, r.next_hops),
                    },
                    "policy_routes": policies.get(key, []) if table == 0 else [],
                }
            )
    return out
