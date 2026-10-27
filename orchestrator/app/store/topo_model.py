"""Topologie-Modell (0.50.0, ADR 0019): Knoten und Kanten der Umgebung als ein Modell -
abgeleitet aus den gelesenen Daten, ergaenzt um das von Hand Erfasste (`GET /topology`).
Grundlage fuer Zonen und Zonenplan (0.51.0, `store/zones.py`), Export (0.52.0) und den Designer.

Knoten (`kind`):
- gelesene Routing-Instanzen wie im Weg (`paths.Graph`): `firewall` (FortiGate nur mit root),
  `vdom`, `vrf` (Rand einer ACI-VRF, alle Border-Leafs) - mit Mitgliedern, HA, Verbund und dem,
  was nicht lesbar war;
- `segment`: Netz, an dem mehr als zwei gelesene Knoten haengen;
- `exit`: Anbindung nach aussen (von Hand, `topo_exit`);
- `router`: Gegenstelle ohne gelesenes Geraet und ohne Anbindung (Next-Hop bzw. BGP-Nachbar,
  `exits.detect`) - beim ACI nur an den L3Outs, nicht die Adressen im Fabric (TEP).

Kanten (`kind`, je mit Herkunft `origin`: derived | manual | confirmed):
- `transfer`: gemeinsames Netz zweier Knoten (Interface-IPs bzw. L3Out-SVIs, ohne Management),
  `member`: Knoten an einem Segment;
- `vdom_link`: Inter-VDOM-Link derselben FortiGate;
- `route`: Route ueber die Adresse eines anderen gelesenen Knotens ohne gemeinsames Netz;
- `bgp`: BGP-Sitzung zwischen zwei gelesenen Knoten;
- `uplink`: Knoten -> Anbindung bzw. Router (aktiv, Backup, nur Nachbar; AS, Zustand), von Hand
  ueber Interface bzw. Zone der Anbindung;
- `exit_route`: manuelle Route einer Anbindung zu einem gelesenen Knoten;
- `parallel`: Verbund paralleler Firewalls (bestaetigt), `sync`: Session-Sync-Partner (FGSP).

Je Kante die Routen darueber (aktiv bzw. Backup je Richtung) und die Adressen.

**Plausibilitaet** (`checks`, Befunde im Abgleich): doppelte IP im selben Netz, Netzmaske
passt nicht zur Gegenstelle, Routing-Schleife zwischen zwei gelesenen Knoten, statische Route
ohne Wirkung (nicht in der RIB, auch keine andere Route fuer das Praefix), BGP-Sitzung nicht
aufgebaut.

Nur lesend; Korrekturen im Designer folgen (ADR 0019 Nr. 7).
"""

import ipaddress
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import InvInterface, TopoHa, TopoL3Out, TopoPeer, TopoRoute
from app.store import exits as exit_store
from app.store.paths import _CONNECTED, Graph, Key, _vdom_links, exit_kind
from app.store.topology import MGMT, table_of

MAX_LOOP_CHECKS = 3000  # Routen ueber gelesene Nachbarn, die auf Schleifen geprueft werden
SAMPLES = 5  # Praefixe je Kante in der Anzeige
SUPERNET_FLOOR = {4: 16, 6: 64}  # abweichende Masken nur bis zu dieser Praefixlaenge


def node_id(key: Key) -> str:
    return f"{key[0]}|{key[1]}"


@dataclass
class Addr:
    key: Key
    interface: str | None
    zone: str | None
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address
    net: ipaddress.IPv4Network | ipaddress.IPv6Network

    def out(self, g: Graph) -> dict:
        return {"node": node_id(self.key), "label": g.nodes[self.key].label,
                "interface": self.interface, "zone": self.zone,
                "address": f"{self.ip}/{self.net.prefixlen}"}  # fmt: skip


@dataclass
class Edge:
    kind: str
    a: str
    b: str
    origin: str = "derived"
    network: str | None = None
    via: list[dict] = field(default_factory=list)
    active: Counter = field(default_factory=Counter)  # Richtung "a>b"/"b>a" -> Routen
    backup: Counter = field(default_factory=Counter)
    samples: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    attrs: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.a}~{self.b}" + (f"~{self.network}" if self.network else "")

    def count(self, src: str, prefix: str, active: bool) -> None:
        way = "a>b" if src == self.a else "b>a"
        (self.active if active else self.backup)[way] += 1
        if active and len(self.samples[way]) < SAMPLES and prefix not in self.samples[way]:
            self.samples[way].append(prefix)

    def out(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "a": self.a,
            "b": self.b,
            "origin": self.origin,
            "network": self.network,
            "via": self.via,
            "routes": {
                "active": dict(self.active),
                "backup": dict(self.backup),
                "samples": dict(self.samples),
            },  # fmt: skip
            **self.attrs,
        }


class _Union:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def join(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


class Model:
    """Topologie eines Mandanten: `build()` fuellt Knoten, Kanten und Befunde."""

    def __init__(self, session: Session, tenant_id: int, graph: Graph | None = None):
        self.session = session
        self.tenant_id = tenant_id
        self.g = graph or Graph(session, tenant_id)
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple, Edge] = {}
        self.adjacent: dict[frozenset, Edge] = {}  # gelesene Knoten mit gemeinsamem Netz
        self.segment_of: dict[Key, dict[str, str]] = defaultdict(dict)  # Knoten -> Netz -> Segment
        self.addrs: list[Addr] = []
        self.issues: list[dict] = []
        self.ports: dict[Key, set[str]] = defaultdict(set)  # Interfaces und Zonen je Knoten
        self.l3out_nets: dict[Key, list] = defaultdict(list)
        self.l3out_peers: dict[Key, set[str]] = defaultdict(set)
        # alle Netze (auch mit nur einem Knoten): Netz -> Adressen (Zonenstruktur, 0.56.0)
        self.components: list[tuple[str, list[Addr]]] = []

    # -- Aufbau ---------------------------------------------------------------------------

    def build(self) -> "Model":
        self._read_nodes()
        self._addresses()
        self._segments()
        self._routes()
        self._vdom_links()
        self._bgp()
        self._uplinks()
        self._manual_exits()
        self._redundancy()
        return self

    def _edge(self, kind: str, a: str, b: str, network: str | None = None, **kw) -> Edge:
        if b < a and kind not in ("uplink", "exit_route", "member"):
            a, b = b, a
        key = (kind, a, b, network)
        if key not in self.edges:
            self.edges[key] = Edge(kind, a, b, network=network, **kw)
        return self.edges[key]

    def _read_nodes(self) -> None:
        ha = {h.source_system_id: h for h in self.session.scalars(
            select(TopoHa).where(TopoHa.tenant_id == self.tenant_id))}  # fmt: skip
        for key, n in self.g.nodes.items():
            h = ha.get(key[0])
            self.nodes[node_id(key)] = {
                **n.out(),
                "id": node_id(key),
                "origin": "derived",
                "ha": {"mode": h.mode, "group": h.group_name,
                       "members": [m.get("hostname") for m in h.members or [] if m.get("hostname")]}
                if h else None,
            }  # fmt: skip

    def _addresses(self) -> None:
        single: dict[int, list[str]] = defaultdict(list)
        for key in self.g.nodes:
            single[key[0]].append(key[1])
        sources = set(single)
        if not sources:
            return
        for i in self.session.scalars(
            select(InvInterface).where(InvInterface.source_system_id.in_(sources))
        ):
            ctxs = single[i.source_system_id]
            key = (i.source_system_id, i.vdom or (ctxs[0] if len(ctxs) == 1 else ""))
            if key not in self.g.nodes:
                continue
            self.ports[key].update(p for p in (i.name, i.zone) if p)
            if self.g.table(key, i.name) == MGMT:
                continue  # Management-Interface: kein Weg fuer Daten
            for text in i.ips or []:
                self._add(key, i.name, i.zone, text)
        for o in self.session.scalars(
            select(TopoL3Out).where(TopoL3Out.source_system_id.in_(sources))
        ):
            key = (o.source_system_id, o.vrf or "")
            if key not in self.g.nodes:
                continue
            for path in o.paths or []:
                for entry in path.get("ips") or []:
                    addr = self._add(key, path.get("interface"), None, str(entry.get("ip") or ""))
                    if addr is not None:
                        self.l3out_nets[key].append(addr.net)
            for peer in o.peers or []:
                if peer.get("ip"):
                    self.l3out_peers[key].add(str(peer["ip"]).split("/")[0])

    def _add(self, key: Key, interface, zone, text: str) -> Addr | None:
        if "/" not in text:
            return None
        try:
            iface = ipaddress.ip_interface(text)
        except ValueError:
            return None
        net = iface.network
        if net.prefixlen == net.max_prefixlen or iface.ip.is_link_local or iface.ip.is_loopback:
            return None
        addr = Addr(key, interface, zone, iface.ip, net)
        self.addrs.append(addr)
        return addr

    def _segments(self) -> None:
        """Adressen im selben Netz (auch bei abweichender Maske) gehoeren zusammen; je Netz mit
        zwei gelesenen Knoten eine Kante, mit mehr ein Segment."""
        by_net: dict = defaultdict(list)
        for idx, a in enumerate(self.addrs):
            by_net[a.net].append(idx)
        groups = {k: n.group for k, n in self.g.nodes.items()}
        uf = _Union(len(self.addrs))
        for idx, a in enumerate(self.addrs):
            # andere Maske derselben Gegenstelle nur in kleinen Netzen (sonst verbindet ein
            # /8 alles darin)
            floor = min(a.net.prefixlen, SUPERNET_FLOOR[a.ip.version])
            for plen in range(a.net.prefixlen, floor - 1, -1):
                net = ipaddress.ip_network((a.ip, plen), strict=False)
                for other in by_net.get(net, []):
                    b = self.addrs[other]
                    if (
                        b.key != a.key
                        and a.ip == b.ip
                        and groups[a.key]
                        and (groups[a.key] == groups[b.key])
                    ):
                        continue  # parallele Firewalls mit gleicher Konfiguration: Kopie
                    uf.join(idx, other)
        comps: dict[int, list[int]] = defaultdict(list)
        for idx in range(len(self.addrs)):
            comps[uf.find(idx)].append(idx)
        for members in comps.values():
            addrs = [self.addrs[i] for i in members]
            keys = sorted({a.key for a in addrs})
            net = min((a.net for a in addrs), key=lambda n: n.prefixlen)
            text = str(net)
            self.components.append((text, addrs))
            if len(keys) < 2:
                continue
            self._plausible_segment(text, addrs)
            via = [a.out(self.g) for a in sorted(addrs, key=lambda a: (a.key, int(a.ip)))]
            if len(keys) == 2:
                edge = self._edge("transfer", node_id(keys[0]), node_id(keys[1]), text, via=via)
                self.adjacent[frozenset(keys)] = edge
                continue
            seg = f"net:{text}"
            name = self.g.network(str(net.network_address + 1)) if net.num_addresses > 2 else None
            self.nodes[seg] = {"id": seg, "kind": "segment", "label": name or text,
                               "network": text, "origin": "derived",
                               "members": [self.g.nodes[k].label for k in keys]}  # fmt: skip
            for k in keys:
                mine = [v for v in via if v["node"] == node_id(k)]
                edge = self._edge("member", node_id(k), seg, text, via=mine)
                self.segment_of[k][text] = seg
                for other in keys:
                    if other != k:
                        self.adjacent.setdefault(frozenset((k, other)), edge)

    def _plausible_segment(self, text: str, addrs: list[Addr]) -> None:
        by_ip: dict = defaultdict(list)
        for a in addrs:
            by_ip[a.ip].append(a)
        for ip, same in by_ip.items():
            keys = {a.key for a in same}
            if len(keys) > 1:
                self.issues.append({
                    "type": "topo_duplicate_ip", "subject": str(ip), "network": text,
                    "nodes": [a.out(self.g) for a in same],
                })  # fmt: skip
        masks = {a.net.prefixlen for a in addrs}
        if len(masks) > 1:
            self.issues.append({
                "type": "topo_mask_mismatch", "subject": text, "network": text,
                "nodes": [a.out(self.g) for a in sorted(addrs, key=lambda a: a.key)],
            })  # fmt: skip

    def _routes(self) -> None:
        """Routen ueber Adressen anderer gelesener Knoten: Zaehler an der Kante (aktiv bzw.
        Backup), ohne gemeinsames Netz eine eigene Kante; dazu Schleifen und statische Routen
        ohne Wirkung."""
        owners = self.g.owners()
        rib: set[tuple[Key, object, str]] = set()
        statics = []
        loops: list[tuple[Key, Key, TopoRoute, str]] = []
        for r in self.session.scalars(
            select(TopoRoute).where(TopoRoute.tenant_id == self.tenant_id)
        ):
            key = (r.source_system_id, r.context)
            table = table_of(r)
            if key not in self.g.nodes or table == MGMT:
                continue
            if r.table == "rib" and r.best:
                rib.add((key, table, r.prefix))
            if r.table == "static" and r.best:
                statics.append((key, table, r))
            if r.protocol in _CONNECTED or (r.table == "static" and not r.best):
                continue
            active = r.table == "rib" and r.best
            for h in r.next_hops or []:
                ip = str(h.get("ip") or "").split("%")[0]
                for other, iface, _zone in owners.get(ip, []):
                    if other == key:
                        continue
                    edge = self.adjacent.get(frozenset((key, other)))
                    if edge is None:
                        edge = self._edge("route", node_id(key), node_id(other))
                        edge.attrs.setdefault("next_hops", [])
                        if ip not in edge.attrs["next_hops"]:
                            edge.attrs["next_hops"].append(ip)
                    if edge.kind == "member":  # ueber ein Segment: Zaehler an der eigenen Kante
                        edge = self._edge("member", node_id(key), edge.b, edge.network)
                        edge.attrs.setdefault("to", Counter())[self.g.nodes[other].label] += 1
                        edge.count(node_id(key), r.prefix, active)
                    else:
                        edge.count(node_id(key), r.prefix, active)
                    if active and len(loops) < MAX_LOOP_CHECKS:
                        loops.append((key, other, r, iface))
        for edge in self.edges.values():
            if isinstance(edge.attrs.get("to"), Counter):
                edge.attrs["to"] = dict(edge.attrs["to"])
        self._loops(loops, owners)
        for key, table, r in statics:
            if (key, table, r.prefix) not in rib:
                label = self.g.nodes[key].label
                self.issues.append({
                    "type": "topo_static_unused", "subject": f"{label}: {r.prefix}",
                    "node": node_id(key), "label": label, "prefix": r.prefix,
                    "next_hops": [h.get("ip") or h.get("interface") for h in r.next_hops or []],
                    "distance": r.distance,
                })  # fmt: skip

    def _loops(self, candidates, owners) -> None:
        """A leitet P ueber B, B das Netz von P zurueck ueber A: Schleife."""
        seen: set = set()
        cache: dict = {}
        for key, other, r, iface in candidates:
            try:
                net = ipaddress.ip_network(r.prefix, strict=False)
            except ValueError:
                continue
            ip = net.network_address
            table = self.g.table(other, iface)
            if (other, table, ip) not in cache:
                cache[(other, table, ip)] = self.g.route(other, ip, table)
            back = cache[(other, table, ip)]
            if back is None or back.get("protocol") in _CONNECTED:
                continue
            via = {k for h in back["next_hops"] for k, _i, _z in owners.get(str(h.get("ip")), [])}
            pair = tuple(sorted((key, other)))
            if key in via and (pair, r.prefix) not in seen:
                seen.add((pair, r.prefix))
                a, b = (self.g.nodes[k].label for k in pair)
                self.issues.append({
                    "type": "topo_route_loop", "subject": f"{a} ↔ {b}: {r.prefix}",
                    "nodes": [node_id(k) for k in pair], "labels": [a, b], "prefix": r.prefix,
                    "back": back["prefix"],
                })  # fmt: skip

    def _vdom_links(self) -> None:
        for sid in sorted({k[0] for k in self.g.nodes}):
            for (vdom_a, if_a), (vdom_b, if_b) in _vdom_links(self.session, sid).items():
                a, b = (sid, vdom_a), (sid, vdom_b)
                if a not in self.g.nodes or b not in self.g.nodes or a > b:
                    continue
                edge = self._edge("vdom_link", node_id(a), node_id(b))
                pair = {"a": if_a, "b": if_b}
                edge.attrs.setdefault("links", [])
                if pair not in edge.attrs["links"]:
                    edge.attrs["links"].append(pair)

    def _bgp(self) -> None:
        owners = self.g.owners()
        for p in self.session.scalars(select(TopoPeer).where(TopoPeer.tenant_id == self.tenant_id)):
            key = (p.source_system_id, p.context)
            if key not in self.g.nodes:
                continue
            state = (p.state or "").lower()
            if state and state != "established":
                label = self.g.nodes[key].label
                self.issues.append({
                    "type": "topo_bgp_down", "subject": f"{label} → {p.peer_ip}",
                    "node": node_id(key), "label": label, "peer": p.peer_ip,
                    "remote_as": p.remote_as, "state": p.state,
                    "peer_names": self.g.names(p.peer_ip),
                })  # fmt: skip
            for other, _iface, _zone in owners.get(str(p.peer_ip), []):
                if other == key:
                    continue
                edge = self._edge("bgp", node_id(key), node_id(other))
                edge.attrs.setdefault("sessions", []).append(
                    {
                        "from": node_id(key),
                        "peer": p.peer_ip,
                        "remote_as": p.remote_as,
                        "state": p.state,
                    }  # fmt: skip
                )

    def _allowed(self, key: Key, ip: str) -> bool:
        """ACI: Gegenstellen nur an den L3Outs (SVI-/Interface-Netz, Peer), nicht das Fabric."""
        if self.g.nodes[key].kind != "vrf":
            return True
        if ip in self.l3out_peers[key]:
            return True
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in net for net in self.l3out_nets[key])

    def _uplinks(self) -> None:
        dismissed = exit_store.dismissed(self.session, self.tenant_id)
        for ip, uses in exit_store.detect(self.g).items():
            for key, u in uses.items():
                if not self._allowed(key, ip):
                    continue
                if u.exit is not None:
                    target = f"exit:{u.exit.id}"
                else:
                    target = f"router:{ip}"
                    if target not in self.nodes:
                        names = self.g.names(ip)
                        self.nodes[target] = {
                            "id": target, "kind": "router", "label": names[0] if names else ip,
                            "ip": ip, "origin": "derived", "dismissed": ip in dismissed,
                            **self.g.resolve(ip),
                        }  # fmt: skip
                    node = self.nodes[target]
                    if u.remote_as and not node.get("remote_as"):
                        node["remote_as"] = u.remote_as
                edge = self._edge("uplink", node_id(key), target)
                edge.attrs.setdefault("routers", []).append({
                    "ip": ip, "status": u.status, "interface": u.interface, "zone": u.zone,
                    "remote_as": u.remote_as, "state": u.state, "active": len(u.active),
                    "backup": len(u.backup), "default": any(p.endswith("/0") for p in u.active),
                })  # fmt: skip
                edge.attrs["status"] = _best_status(edge.attrs.get("status"), u.status)

    def _manual_exits(self) -> None:
        for e in self.g.exits:
            nid = f"exit:{e.id}"
            self.nodes[nid] = {
                "id": nid, "kind": "exit", "exit_kind": e.kind,
                "label": e.name or exit_kind(e.kind, e.kind), "origin": "manual",
                "prefixes": e.prefixes or [], "next_hops": e.next_hops or [],
                "description": e.description,
            }  # fmt: skip
            ports = set(e.interfaces or [])
            for key in self.g.nodes:
                if self.g.nodes[key].kind == "vrf":
                    continue
                if e.source_system_ids and key[0] not in e.source_system_ids:
                    continue
                hit = sorted(ports & self.ports[key])
                if hit and ("uplink", node_id(key), nid, None) not in self.edges:
                    edge = self._edge("uplink", node_id(key), nid, origin="manual")
                    edge.attrs["ports"] = hit
            for r in e.routes or []:
                target = str(r.get("target") or "")
                if target in self.nodes:
                    edge = self._edge("exit_route", nid, target, origin="manual")
                    edge.attrs.setdefault("prefixes", []).append(r.get("prefix"))

    def _redundancy(self) -> None:
        by_group: dict[tuple[str, str], list[Key]] = defaultdict(list)
        for key, n in self.g.nodes.items():
            if n.group:
                by_group[(n.group, key[1])].append(key)
        for (group, _ctx), keys in by_group.items():
            for i, a in enumerate(sorted(keys)):
                for b in sorted(keys)[i + 1 :]:
                    if a[0] != b[0]:
                        edge = self._edge("parallel", node_id(a), node_id(b), origin="confirmed")
                        edge.attrs["group"] = group
        owners = self.g.owners()
        for h in self.session.scalars(select(TopoHa).where(TopoHa.tenant_id == self.tenant_id)):
            for peer in h.sync_peers or []:
                for (sid, _ctx), _i, _z in owners.get(str(peer.get("peerip") or ""), []):
                    if sid == h.source_system_id:
                        continue
                    for a in [k for k in self.g.nodes if k[0] == h.source_system_id]:
                        b = (sid, a[1])
                        if b in self.g.nodes:
                            edge = self._edge("sync", node_id(a), node_id(b))
                            edge.attrs["peer"] = peer.get("peerip")

    # -- Ausgabe --------------------------------------------------------------------------

    def out(self) -> dict:
        edges = [e.out() for e in self.edges.values()]
        degree = Counter()
        for e in edges:
            degree[e["a"]] += 1
            degree[e["b"]] += 1
        issues_by_node: Counter = Counter()
        for i in self.issues:
            for n in _issue_nodes(i):
                issues_by_node[n] += 1
        nodes = [
            {**n, "degree": degree[n["id"]], "issues": issues_by_node[n["id"]]}
            for n in sorted(self.nodes.values(), key=_node_order)
        ]
        return {
            "nodes": nodes,
            "edges": sorted(edges, key=lambda e: (e["kind"], e["a"], e["b"])),
            "issues": self.issues,
            "summary": {
                "nodes": dict(Counter(n["kind"] for n in nodes)),
                "edges": dict(Counter(e["kind"] for e in edges)),
                "issues": dict(Counter(i["type"] for i in self.issues)),
            },
        }


_STATUS = {"active": 0, "backup": 1, "neighbor": 2}
_KIND_ORDER = {"firewall": 0, "vdom": 0, "vrf": 1, "segment": 2, "exit": 3, "router": 4}


def _best_status(old: str | None, new: str) -> str:
    return new if old is None or _STATUS[new] < _STATUS[old] else old


def _node_order(n: dict) -> tuple:
    return (_KIND_ORDER.get(n["kind"], 9), n.get("label") or "")


def _issue_nodes(issue: dict) -> list[str]:
    out = [issue["node"]] if issue.get("node") else []
    out += issue.get("nodes", []) if isinstance(issue.get("nodes"), list) else []
    return [n if isinstance(n, str) else n.get("node") for n in out]


def addresses(
    session: Session, tenant_id: int, graph: Graph | None = None
) -> tuple[list[Addr], dict[Key, set[str]]]:
    """Adressen der gelesenen Knoten (Interfaces ohne Management, L3Out-SVIs) und die
    Interfaces bzw. Zonen je Knoten - fuer den Zonenplan (0.51.0)."""
    m = Model(session, tenant_id, graph)
    m._addresses()
    return m.addrs, m.ports


def transfer_nets(
    session: Session, tenant_id: int, graph: Graph | None = None
) -> dict[str, list[str]]:
    """Netze zwischen gelesenen Knoten (Transfer bzw. Segment) mit den Knoten daran - fuer die
    Zonen-Vorschlaege (0.54.0)."""
    m = Model(session, tenant_id, graph)
    m._addresses()
    m._segments()
    out: dict[str, set[str]] = defaultdict(set)
    for e in m.edges.values():
        if e.kind in ("transfer", "member") and e.network:
            out[e.network].update(v["label"] for v in e.via)
    return {net: sorted(labels) for net, labels in out.items()}


def build(session: Session, tenant_id: int, graph: Graph | None = None) -> dict:
    return Model(session, tenant_id, graph).build().out()


def checks(session: Session, tenant_id: int) -> list[dict]:
    """Plausibilitaets-Befunde fuer den Abgleich (`reconcile`)."""
    return Model(session, tenant_id).build().issues
