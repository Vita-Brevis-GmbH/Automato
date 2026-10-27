"""Zonenstruktur (0.56.0, ADR 0024 Nr. 12): Zonen an den Knoten, Netze in der Zone, Uebergaenge.

Das Zonenmodell des Anwenders: eine Zone umfasst **Knoten** (Firewall bzw. VDOM, ACI-VRF) - die
Tenants des ACI sind das Datacenter, eine Internet-Firewall „deklariert“ die Zone Edge Internet.
Zwischen den Zonen liegen **Uebergaenge**: im ACI die L3Outs, bei den Firewalls die Interfaces,
meist mit Border-Routern. Firewalls derselben Zone untereinander sind kein Uebergang, ihr
Transfernetz gehoert zur Zone.

- **Knoten einer Zone** ueber ihre Selektoren: `fw_vdom` (VDOM bzw. `root` einer Firewall, je
  Quellsystem), `aci_tenant` (alle VRFs der EPGs des Tenants), `aci_vrf`. Gilt die erste Zone in
  der Reihenfolge; ein Knoten in mehreren Zonen ist ein Hinweis (`conflicts`).
- **Netze ausdruecklich in einer Zone** ueber die uebrigen Selektoren (Netz, IPAM, Interface,
  Firewall-Zone, EPG, Anbindung) - laengstes Praefix wie bisher. Die Netze hinter einer
  Anbindung (oft 0.0.0.0/0) ordnen kein Netz an den Knoten zu; dort zaehlen Next-Hop bzw.
  Interface der Anbindung.
- Je gelesenes Netz an den Knoten (Interface bzw. L3Out, auch mit abweichender Maske
  zusammengefasst wie im Topologie-Modell) die Seiten: Zonen der Knoten daran und - wenn
  angegeben - die Zone des Netzes bzw. der Anbindung dahinter.
  - Eine Seite: das Netz liegt **in der Zone** (Transfernetz zweier Firewalls derselben Zone,
    LAN, L3Out ohne Gegenstelle).
  - Ist das Netz ausdruecklich einer Zone zugeordnet, sind die Knoten anderer Zonen daran ein
    **Uebergang** zu ihr (z. B. Bein einer Edge-Firewall in die Zone Public). Ein Transfernetz
    (Router dahinter, Knoten der Zone daran bzw. Anbindung) gehoert dann keiner Zone; ein LAN
    direkt am Interface (Server-, Office-Netz) bleibt Netz der Zone, das Interface ist der
    Uebergang.
  - Sonst: Knoten verschiedener Zonen = Uebergang zwischen ihnen; Knoten ohne Zone = Uebergang zu
    „ohne Zone“; ein Router ohne gelesenes Geraet (Next-Hop, BGP-Nachbar) = Uebergang nach
    „ausserhalb“, solange keine Zone das Netz dahinter nennt.
  - Ohne jede Zone: Netz ohne Zone.
  - Knoten ohne Zone an einem Netz einer Zone sind kein Uebergang (das Netz bleibt in der Zone),
    der Plan zeigt sie als Kontakt (`contacts`), damit sichtbar ist, wo sie hingehoeren.
  - **Zone nur hinter einem Router** (0.81.0): liegt ein Netz einer Zone an keinem Knoten, aber
    ein Knoten routet es (nicht ueber die Default-Route) ueber einen Router ohne gelesenes
    Geraet, ist das Netz dieses Routers der Uebergang zur Zone - wie bei einer Anbindung.
    Der Uebergang nennt die Routen (`routes`).
- Netze eines Uebergangs gehoeren keiner Zone („Netze in der Zone sind nur die, die keine
  Uebergaenge sind“). Zonen ohne eigenes Netz sind normal (z. B. Edge Internet: ein Bein im DC,
  eines in Public).

Nur lesend.
"""

import bisect
import ipaddress
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LogicalZone, TopoExit
from app.store import customers, topo_model
from app.store import exits as exit_store
from app.store.customers import Net, Selector
from app.store.paths import Graph, Key

NODE_SELECTORS = ("fw_vdom", "aci_tenant", "aci_vrf")
NONE = "none"  # Knoten ohne Zone
OUTSIDE = "outside"  # Router ohne gelesenes Geraet, Zone dahinter unbekannt
Side = int | str
SAMPLES = 12
ROUTED_NETS = 200  # Netze je Zone, deren Routen ueber Router ohne Geraet gesucht werden


def node_id(key: Key) -> str:
    return topo_model.node_id(key)


@dataclass
class Transition:
    a: Side
    b: Side
    networks: list[dict] = field(default_factory=list)

    @property
    def key(self) -> tuple:
        return (self.a, self.b)


@dataclass
class Structure:
    graph: Graph
    node_zone: dict[Key, int] = field(default_factory=dict)
    conflicts: list[dict] = field(default_factory=list)
    explicit: dict[int, tuple[dict[int, str], dict[Net, str]]] = field(default_factory=dict)
    internal: dict[int, dict[Net, str]] = field(default_factory=lambda: defaultdict(dict))
    transitions: dict[tuple, Transition] = field(default_factory=dict)
    transition_nets: set[Net] = field(default_factory=set)
    unzoned: list[dict] = field(default_factory=list)
    # Knoten ohne Zone an Netzen einer Zone (kein Uebergang, Hinweis fuer den Plan)
    contacts: dict[int, list[dict]] = field(default_factory=lambda: defaultdict(list))
    # je Netz an den Knoten: die Knoten daran und die Zonen, die es beruehrt (Plan je Kunde)
    segments: list[tuple[set[str], set]] = field(default_factory=list)

    def zone_nodes(self) -> dict[int, list[Key]]:
        out: dict[int, list[Key]] = defaultdict(list)
        for key, zid in sorted(self.node_zone.items(), key=lambda x: self.graph.nodes[x[0]].label):
            out[zid].append(key)
        return out

    def unassigned(self) -> list[Key]:
        return sorted((k for k in self.graph.nodes if k not in self.node_zone),
                      key=lambda k: self.graph.nodes[k].label)  # fmt: skip

    def of_zone(self, zid: int) -> list[Transition]:
        return [t for t in self.transitions.values() if zid in (t.a, t.b)]

    def nets(self, zid: int) -> tuple[dict[int, str], dict[Net, str]]:
        """EPGs und Netze der Zone: ausdruecklich zugeordnete und die Netze ihrer Knoten in der
        Zone, ohne Netze von Uebergaengen."""
        epgs, nets = self.explicit.get(zid, ({}, {}))
        out = {n: w for n, w in nets.items() if n not in self.transition_nets}
        for n, w in self.internal.get(zid, {}).items():
            out.setdefault(n, w)
        return epgs, out


def selectors_of(z: LogicalZone) -> list[Selector]:
    return [Selector(s.kind, s.value, s.source_system_id, s.exclude) for s in z.selectors]


def has_nodes(items: list[LogicalZone]) -> bool:
    return any(s.kind in NODE_SELECTORS and not s.exclude for z in items for s in z.selectors)


def _node_matches(g: Graph, data: customers._Data, s: Selector) -> set[Key]:
    v = s.value.strip().casefold()
    out: set[Key] = set()
    if s.kind == "fw_vdom":
        for key, n in g.nodes.items():
            if (
                n.kind in ("firewall", "vdom")
                and key[1].casefold() == v
                and (s.source_system_id is None or s.source_system_id == key[0])
            ):
                out.add(key)
    elif s.kind == "aci_vrf":
        for key, n in g.nodes.items():
            ctx = key[1].casefold()
            if n.kind == "vrf" and (ctx == v or (":" not in v and ctx.split(":")[-1] == v)):
                out.add(key)
    elif s.kind == "aci_tenant":
        vrfs = {(data.vrf.get(e.id) or "").casefold() for e in data.epgs
                if (e.aci_tenant or "").casefold() == v}  # fmt: skip
        for key, n in g.nodes.items():
            ctx = key[1].casefold()
            if n.kind == "vrf" and (ctx in vrfs or ctx.split(":")[0] == v):
                out.add(key)
    return out


def _exit_zone(items: list[LogicalZone], exits: list[TopoExit]) -> dict[int, int]:
    """Anbindung -> Zone, die sie als Selektor nennt (erste in der Reihenfolge)."""
    out: dict[int, int] = {}
    for z in items:
        names = {s.value.strip().casefold() for s in z.selectors
                 if s.kind == "exit" and not s.exclude}  # fmt: skip
        for e in exits:
            if e.id not in out and ((e.name or "").casefold() in names or f"#{e.id}" in names):
                out[e.id] = z.id
    return out


def build(session: Session, tenant_id: int, items: list[LogicalZone],
          data: "customers._Data | None" = None,
          graph: Graph | None = None) -> Structure:  # fmt: skip
    from app.store import zones as zone_store

    data = data or customers._Data(session, tenant_id)
    g = graph or Graph(session, tenant_id)
    st = Structure(g)

    # Knoten der Zonen (erste Zone in der Reihenfolge gilt)
    seen: dict[Key, list[str]] = defaultdict(list)
    placed: dict[int, dict[Net, str]] = {}  # Netze fuer die Netze an den Knoten, ohne Anbindung
    for z in items:
        sel = selectors_of(z)
        mine = set().union(*(_node_matches(g, data, s) for s in sel
                             if s.kind in NODE_SELECTORS and not s.exclude))  # fmt: skip
        mine -= set().union(*(_node_matches(g, data, s) for s in sel
                              if s.kind in NODE_SELECTORS and s.exclude))  # fmt: skip
        for key in mine:
            seen[key].append(z.name)
            st.node_zone.setdefault(key, z.id)
        # Netze ausdruecklich in der Zone (ohne die Knoten-Selektoren der Firewall)
        explicit = [s for s in sel if s.kind != "fw_vdom"]
        st.explicit[z.id] = customers.nets_of(data, explicit)
        # was hinter einer Anbindung liegt (oft 0.0.0.0/0), ordnet kein Transfernetz zu - dafuer
        # zaehlen Next-Hop bzw. Interface der Anbindung (`behind`)
        if any(s.kind == "exit" and not s.exclude for s in sel):
            placed[z.id] = customers.nets_of(data, [s for s in explicit if s.kind != "exit"])[1]
        else:
            placed[z.id] = st.explicit[z.id][1]
    st.conflicts = [{"node": node_id(k), "label": g.nodes[k].label, "zones": names}
                    for k, names in seen.items() if len(names) > 1]  # fmt: skip

    index = zone_store.ZoneIndex([zone_store.ZoneNets(z.id, z.name, placed[z.id]) for z in items])

    m = topo_model.Model(session, tenant_id, g)
    m._addresses()
    m._segments()
    # Router ohne gelesenes Geraet und Anbindungen je Netz
    comp_of: dict[Key, list[tuple[Net, int]]] = defaultdict(list)
    for i, (_text, addrs) in enumerate(m.components):
        for a in addrs:
            comp_of[a.key].append((a.net, i))

    def comp_at(key: Key, ip: str) -> int | None:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return None
        for net, i in comp_of.get(key, []):
            if addr.version == net.version and addr in net:
                return i
        return None

    routers: dict[int, dict[str, list[str]]] = defaultdict(dict)
    for ip, uses in exit_store.detect(g).items():
        for key in uses:
            i = comp_at(key, ip)
            if i is not None:
                routers[i].setdefault(ip, g.names(ip))
    exits = list(session.scalars(select(TopoExit).where(TopoExit.tenant_id == tenant_id)))
    exit_zone = _exit_zone(items, exits)
    behind: dict[int, set[int]] = defaultdict(set)
    for e in exits:
        zid = exit_zone.get(e.id)
        if zid is None:
            continue
        ports = {p.casefold() for p in e.interfaces or []}
        for i, (_text, addrs) in enumerate(m.components):
            for a in addrs:
                if e.source_system_ids and a.key[0] not in e.source_system_ids:
                    continue
                hit = any(_inside(str(h), a.net) for h in e.next_hops or [])
                port = {(a.interface or "").casefold(), (a.zone or "").casefold()} & ports
                if hit or port:
                    behind[i].add(zid)

    routed = _routed(g, items, placed, st.node_zone, m, comp_at, routers)
    for i, zones in routed.items():
        behind[i] |= set(zones)

    for i, (text, addrs) in enumerate(m.components):
        net = min((a.net for a in addrs), key=lambda n: n.prefixlen)
        keys = sorted({a.key for a in addrs})
        nodes = {k: st.node_zone.get(k) for k in keys}
        lo, hi = int(net.network_address), int(net.broadcast_address)
        explicit = set(index.covering(net.version, lo, hi)) | behind.get(i, set())
        assigned = {z for z in nodes.values() if z is not None}
        ordered_addrs = sorted(addrs, key=lambda a: (g.nodes[a.key].label, int(a.ip)))
        legs = [{"node": node_id(a.key), "label": g.nodes[a.key].label, "interface": a.interface,
                 "address": f"{a.ip}/{a.net.prefixlen}",
                 "l3out": any(a.net == n for n in m.l3out_nets.get(a.key, []))}
                for a in ordered_addrs]  # fmt: skip
        info = {"network": text, "legs": legs,
                "routers": [{"ip": ip, "names": names}
                            for ip, names in sorted(routers.get(i, {}).items())]}  # fmt: skip
        if routed.get(i):  # Zonen nur hinter dem Router: die Routen dorthin (0.81.0)
            info["routes"] = [{"zone": zid, "prefixes": sorted(p, key=_prefix_order)}
                              for zid, p in sorted(routed[i].items())]  # fmt: skip
        st.segments.append(({x["node"] for x in legs}, assigned | explicit))
        pairs: set[tuple[Side, Side]] = set()
        if explicit:
            if any(z is None for z in nodes.values()):
                for z in explicit:
                    st.contacts[z].append(info)
            pairs = {(y, p) for y in assigned - explicit for p in explicit}
            # Netz der Zone selbst: alle Knoten in ihr bzw. ein LAN am Interface (keine Knoten
            # der Zone, kein Router dahinter) - dann ist nur das Interface der Uebergang
            if not pairs or not (routers.get(i) or assigned & explicit or behind.get(i)):
                for z in explicit:
                    st.internal[z].setdefault(net, _reason(legs))
                for a, b in pairs:
                    key = tuple(sorted((a, b), key=_side_order))
                    st.transitions.setdefault(key, Transition(*key)).networks.append(info)
                continue
        else:
            sides: set[Side] = set(assigned)
            if not sides:
                ids = list(dict.fromkeys(x["node"] for x in legs))
                st.unzoned.append({"network": text, "node": legs[0]["label"],
                                   "where": _where(legs[0]), "nodes": ids})  # fmt: skip
                continue
            if any(z is None for z in nodes.values()):
                sides.add(NONE)
            if routers.get(i):
                sides.add(OUTSIDE)
            if len(sides) == 1:
                st.internal[next(iter(assigned))].setdefault(net, _reason(legs))
                continue
            ordered = sorted(sides, key=_side_order)
            pairs = {(a, b) for n, a in enumerate(ordered) for b in ordered[n + 1 :]}
        st.transition_nets.add(net)
        for a, b in pairs:
            key = tuple(sorted((a, b), key=_side_order))
            t = st.transitions.setdefault(key, Transition(*key))
            t.networks.append(info)
    return st


def _routed(
    g: Graph,
    items: list[LogicalZone],
    placed: dict[int, dict[Net, str]],
    node_zone: dict[Key, int],
    m,
    comp_at,
    routers,
) -> dict[int, dict[int, set[str]]]:
    """Zonen nur hinter einem Router (0.81.0): Netz i -> Zone -> Praefixe der Routen. Netze der
    Zone, die an keinem Knoten liegen; Route des Knotens (nicht 0.0.0.0/0) mit einem Next-Hop
    auf einem Router ohne gelesenes Geraet im Netz i."""
    attached = _Spans(a.net for _t, addrs in m.components for a in addrs)
    out: dict[int, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
    for z in items:
        nets = [n for n in placed.get(z.id, {})
                if n.prefixlen and not attached.overlaps(n)][:ROUTED_NETS]  # fmt: skip
        if not nets:
            continue
        for key in g.nodes:
            if node_zone.get(key) == z.id:
                continue
            for net in nets:
                route = g.route(key, net.network_address)
                if route is None or not route["prefix_len"]:
                    continue  # ohne Route bzw. nur ueber die Default-Route
                for hop in route["next_hops"] or []:
                    ip = hop.get("ip")
                    i = comp_at(key, ip) if ip else None
                    if i is not None and ip in routers.get(i, {}):
                        out[i][z.id].add(route["prefix"])
    return out


class _Spans:
    """Netze als sortierte Bereiche je Familie - ueberschneidet sich ein Netz mit einem davon?"""

    def __init__(self, nets):
        spans: dict[int, list[tuple[int, int]]] = {4: [], 6: []}
        for n in nets:
            spans[n.version].append((int(n.network_address), int(n.broadcast_address)))
        self.spans = {f: sorted(s) for f, s in spans.items()}
        self.starts = {f: [s[0] for s in v] for f, v in self.spans.items()}
        # groesstes Ende bis zu jeder Stelle (Bereiche koennen einander enthalten)
        self.reach = {}
        for f, v in self.spans.items():
            top, acc = -1, []
            for _lo, hi in v:
                top = max(top, hi)
                acc.append(top)
            self.reach[f] = acc

    def overlaps(self, net: Net) -> bool:
        lo, hi = int(net.network_address), int(net.broadcast_address)
        i = bisect.bisect_right(self.starts[net.version], hi)  # Bereiche, die vor hi beginnen
        return i > 0 and self.reach[net.version][i - 1] >= lo


def _prefix_order(p: str) -> tuple:
    try:
        n = ipaddress.ip_network(p, strict=False)
    except ValueError:
        return (9, 0, p)
    return (n.version, int(n.network_address), n.prefixlen)


def needed(st: Structure, own: set[int], targets: set[int]) -> tuple[set[str], set[int]]:
    """Knoten, die ein Kunde braucht (0.57.1): die Knoten seiner Zonen und die an seinen Netzen,
    dazu je Zielzone (Gegenstelle seiner Regeln) die Knoten auf den kuerzesten Wegen zu ihren
    Knoten (Zone ohne Knoten: zu den Knoten an ihren Netzen) - ueber gemeinsame Netze der Knoten,
    gleich lange Wege alle (Redundanz) - und die Partner eines Verbunds.
    -> (Knoten-IDs `<Quellsystem>|<Kontext>`, erreichte Zielzonen)."""
    adj: dict[str, set[str]] = defaultdict(set)
    touches: dict = defaultdict(set)  # Zone -> Knoten an ihren Netzen bzw. in ihr
    for nodes, zones in st.segments:
        for n in nodes:
            adj[n] |= nodes - {n}
        for z in zones:
            touches[z] |= nodes
    inside: dict = defaultdict(set)  # Zone -> ihre Knoten
    for key, zid in st.node_zone.items():
        touches[zid].add(node_id(key))
        inside[zid].add(node_id(key))
    sources = set().union(*(touches.get(z, set()) for z in own)) if own else set()
    out, reached = set(sources), set()
    if not sources:
        return out, reached
    from_src = _distances(adj, sources)
    for t in targets - own:
        goal = inside.get(t) or touches.get(t, set())  # Zone ohne Knoten: an ihren Netzen
        best = min((from_src[n] for n in goal if n in from_src), default=None)
        if best is None:
            continue
        reached.add(t)
        to_goal = _distances(adj, goal)
        out |= {n for n, d in from_src.items() if n in to_goal and d + to_goal[n] == best}
    groups = {n.group for k, n in st.graph.nodes.items() if n.group and node_id(k) in out}
    out |= {node_id(k) for k, n in st.graph.nodes.items() if n.group in groups and n.group}
    return out, reached


def _distances(adj: dict[str, set[str]], start: set[str]) -> dict[str, int]:
    dist = dict.fromkeys(start, 0)
    todo = list(start)
    while todo:
        nxt = []
        for n in todo:
            for m in adj.get(n, ()):
                if m not in dist:
                    dist[m] = dist[n] + 1
                    nxt.append(m)
        todo = nxt
    return dist


def _inside(ip: str, net: Net) -> bool:
    try:
        return ipaddress.ip_address(ip.split("/")[0]) in net
    except ValueError:
        return False


def _side_order(s: Side) -> tuple:
    return (1, 0, s) if isinstance(s, str) else (0, s, "")


def _where(leg: dict) -> str:
    return f"{'L3Out' if leg['l3out'] else 'Interface'} {leg['interface'] or '?'}"


def _reason(legs: list[dict]) -> str:
    """Grund eines Netzes in der Zone: Transfer zweier Knoten bzw. Interface/L3Out."""
    nodes = list(dict.fromkeys(leg["label"] for leg in legs))
    if len(nodes) > 1:
        return "Transfer " + " ↔ ".join(nodes)
    return f"{_where(legs[0])} · {nodes[0]}"
