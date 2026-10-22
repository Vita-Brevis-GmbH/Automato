"""Erkannte Anbindungen nach aussen (0.20.0, ADR 0019): die Gegenstellen der gelesenen Knoten
- Next-Hops und BGP-Nachbarn, die keinem gelesenen Geraet gehoeren (Aussenrouter) - mit ihrer
Redundanz.

- **Erkennen:** je Knoten (VDOM bzw. ACI-VRF) die Next-Hops der wirksamen Routen (`rib`), der
  BGP-Pfade und der eingeschalteten statischen Routen sowie die BGP-Nachbarn. Nicht dabei:
  die Management-Tabelle und Adressen gelesener Geraete (dort geht der Weg weiter).
- **Je Router:** aktiv (Praefixe in der RIB, ECMP = mehrere aktive Router), Backup (nur als
  BGP-Pfad bzw. statische Route, z. B. schwebende Route) oder nur BGP-Nachbar; AS und Zustand
  der BGP-Sitzung, Default-Route, Praefixe; je Firewall Interface und Zone.
- **Zusammenfassen zu Vorschlaegen:** Router, die sich Firewalls teilen; Router derselben
  aktiven Route (ECMP); Router am selben Interface bzw. an derselben Zone und BGP-Nachbarn mit
  derselben AS - je Firewall bzw. je
  Verbund paralleler Firewalls und Session-Sync-Partner (FGSP, ADR 0020). Vier WAN-Router mit
  BGP an beiden Firewalls eines Verbunds sind ein Vorschlag mit vier Routern und zwei Firewalls.
- **Zustand einer Anbindung:** die erkannten Router, die sie abdeckt (ueber Next-Hop bzw.
  Interface/Zone, wie im Weg) - auch die, die sie noch nicht nennt.

Nur lesend: alles stammt aus den gelesenen Routing-Daten, nichts geht an ein Geraet.
"""

import ipaddress
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TopoExit, TopoHa, TopoPeer, TopoRoute
from app.store.paths import _CONNECTED, Graph, Key
from app.store.topology import MGMT, Table, table_of

SHOWN = 5  # Praefixe je Router in der Anzeige
_KIND_WORDS = (
    ("mpls", ("mpls",)),
    ("internet", ("internet", "inet", "isp")),
    ("wan", ("wan",)),
    ("partner", ("partner", "extern")),
)


@dataclass
class Use:
    """Ein Router an einem Knoten: ueber welches Interface, mit welchen Praefixen."""

    key: Key
    table: Table = 0
    interface: str | None = None
    zone: str | None = None
    active: set[str] = field(default_factory=set)  # Praefixe in der RIB
    backup: set[str] = field(default_factory=set)  # nur als BGP-Pfad bzw. statische Route
    ecmp: set[str] = field(default_factory=set)  # andere Router derselben aktiven Route
    peer: bool = False
    remote_as: int | None = None
    state: str | None = None
    exit: TopoExit | None = None

    @property
    def status(self) -> str:
        if self.active:
            return "active"
        return "backup" if self.backup else "neighbor"


class _Union:
    def __init__(self) -> None:
        self.parent: dict = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def join(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _ip(value) -> str | None:
    try:
        ip = ipaddress.ip_address(str(value or "").split("%")[0].strip())
    except ValueError:
        return None
    return None if ip.is_unspecified else str(ip)


def detect(g: Graph) -> dict[str, dict[Key, Use]]:
    """Router (IP) -> Knoten -> Nutzung: Next-Hops und BGP-Nachbarn ohne gelesenes Geraet."""
    owned = g.owners()
    found: dict[str, dict[Key, Use]] = defaultdict(dict)

    def use(ip: str, key: Key, table: Table, iface: str | None) -> Use:
        u = found[ip].get(key)
        if u is None:
            u = found[ip][key] = Use(key, table)
        if iface and not u.interface:
            u.interface = iface
        return u

    rows = g.session.execute(
        select(
            TopoRoute.source_system_id,
            TopoRoute.context,
            TopoRoute.table,
            TopoRoute.best,
            TopoRoute.protocol,
            TopoRoute.prefix,
            TopoRoute.next_hops,
            TopoRoute.attrs,
        ).where(TopoRoute.tenant_id == g.tenant_id)
    )
    for r in rows:
        key = (r.source_system_id, r.context)
        table = table_of(r)
        if key not in g.nodes or table == MGMT or r.protocol in _CONNECTED:
            continue
        if r.table == "static" and not r.best:
            continue  # abgeschaltete statische Route
        hops = [(_ip(h.get("ip")), h.get("interface")) for h in r.next_hops or []]
        hops = [(ip, iface) for ip, iface in hops if ip is not None and ip not in owned]
        active = r.table == "rib" and r.best
        for ip, iface in hops:
            u = use(ip, key, table, iface)
            (u.active if active else u.backup).add(r.prefix)
            if active and len(hops) > 1:
                u.ecmp.update(other for other, _i in hops if other != ip)
    for p in g.session.scalars(select(TopoPeer).where(TopoPeer.tenant_id == g.tenant_id)):
        key = (p.source_system_id, p.context)
        ip = _ip(p.peer_ip)
        if key not in g.nodes or ip is None or ip in owned:
            continue
        u = use(ip, key, 0, None)
        u.peer, u.remote_as, u.state = True, p.remote_as, p.state
    for ip, uses in found.items():
        for key, u in uses.items():
            u.backup -= u.active
            if not u.interface:  # BGP-Pfade und Nachbarn: das Netz des Routers
                u.interface = g.interface_to(key, ip, u.table)
            u.zone = g.zone(key[0], u.interface)
            u.exit = g.exit_for(key, u.interface, u.zone, ip)
    return found


def _scopes(g: Graph):
    """Quellsystem -> Vertreter: derselbe Verbund (ADR 0020) bzw. Session-Sync (FGSP)."""
    u = _Union()
    for n in g.nodes.values():
        if n.group:
            u.join(("grp", n.group), ("sys", n.source_system_id))
    owners = g.owners()
    for h in g.session.scalars(select(TopoHa).where(TopoHa.tenant_id == g.tenant_id)):
        for peer in h.sync_peers or []:
            for (sid, _ctx), _iface, _zone in owners.get(str(peer.get("peerip") or ""), []):
                u.join(("sys", h.source_system_id), ("sys", sid))
    return lambda sid: u.find(("sys", sid))


def _groups(g: Graph, found: dict[str, dict[Key, Use]]) -> list[list[str]]:
    """Router zu Anbindungen: gemeinsam genutzt, ECMP derselben Route, gleiches Interface bzw.
    gleiche Zone, gleiche BGP-AS - je Firewall bzw. Verbund/Session-Sync-Partner."""
    scope = _scopes(g)
    u = _Union()
    for ip, uses in found.items():
        u.find(("ip", ip))
        for key, x in uses.items():
            s = scope(key[0])
            port = x.zone or x.interface
            if port:
                u.join(("ip", ip), ("port", s, port))
            if x.remote_as:
                u.join(("ip", ip), ("as", s, x.remote_as))
            for other in x.ecmp:
                u.join(("ip", ip), ("ip", other))
    groups: dict = defaultdict(list)
    for ip in found:
        groups[u.find(("ip", ip))].append(ip)
    return [sorted(ips, key=_ip_sort) for ips in groups.values()]


def _ip_sort(ip: str) -> tuple:
    a = ipaddress.ip_address(ip)
    return (a.version, int(a))


def _net_sort(prefix: str) -> tuple:
    try:
        n = ipaddress.ip_network(prefix, strict=False)
    except ValueError:
        return (9, 0, 0)
    return (n.version, n.prefixlen, int(n.network_address))


def _ref(e: TopoExit | None) -> dict | None:
    return {"id": e.id, "name": e.name, "kind": e.kind} if e is not None else None


def _status(uses: dict[Key, Use]) -> str:
    xs = uses.values()
    if any(x.active for x in xs):
        return "active"
    return "backup" if any(x.backup for x in xs) else "neighbor"


def _redundancy(routers: dict[str, dict[Key, Use]]) -> dict:
    """Redundanz einer Anbindung: Router je Zustand, ECMP (meiste aktive Router fuer dasselbe
    Praefix an einem Knoten) und Firewalls."""
    count = Counter(_status(uses) for uses in routers.values())
    ecmp: dict[tuple, set[str]] = defaultdict(set)
    for ip, uses in routers.items():
        for key, x in uses.items():
            for prefix in x.active:
                ecmp[(key, prefix)].add(ip)
    return {
        "routers": len(routers),
        "active": count["active"],
        "backup": count["backup"],
        "neighbor": count["neighbor"],
        "ecmp": max((len(v) for v in ecmp.values()), default=0),
        "firewalls": len({key[0] for uses in routers.values() for key in uses}),
    }


def _router(g: Graph, ip: str, uses: dict[Key, Use]) -> dict:
    """Ein Router mit Zustand (aktiv, Backup, nur Nachbar) und seinen Firewalls."""
    xs = list(uses.values())
    prefixes = sorted(set().union(*(x.active | x.backup for x in xs)), key=_net_sort)
    peer = next((x for x in xs if x.peer), None)
    covered = next((x.exit for x in xs if x.exit is not None), None)
    return {
        "ip": ip,
        **g.resolve(ip),
        "status": _status(uses),
        "remote_as": peer.remote_as if peer else None,
        "state": peer.state if peer else None,
        "default": any(p.endswith("/0") for p in prefixes),
        "prefixes": prefixes[:SHOWN],
        "prefix_count": len(prefixes),
        "exit": _ref(covered),
        "covered": all(x.exit is not None for x in xs),
        "uses": [
            {
                "key": g.nodes[x.key].key,
                "source_system_id": x.key[0],
                "source": g.nodes[x.key].source,
                "label": g.nodes[x.key].label,
                "interface": x.interface,
                "zone": x.zone,
                "status": x.status,
                "active": len(x.active),
                "backup": len(x.backup),
                "remote_as": x.remote_as,
                "state": x.state,
                "exit": _ref(x.exit),
            }
            for x in sorted(xs, key=lambda x: (g.nodes[x.key].label, x.key))
        ],
    }


def _kind(routers: list[dict], ports: list[str]) -> str:
    """Art raten: Woerter in Zone/Interface bzw. IPAM-Namen, sonst Default-Route = Internet,
    BGP = WAN."""
    words = [*ports, *(n for r in routers for n in r.get("names", [])),
             *(r.get("network") or "" for r in routers)]  # fmt: skip
    text = " ".join(words).lower()
    for kind, keys in _KIND_WORDS:
        if any(k in text for k in keys):
            return kind
    if any(r["default"] for r in routers):
        return "internet"
    return "wan" if any(r["remote_as"] for r in routers) else "other"


def _name(routers: list[dict], ports: list[str]) -> str:
    """Name: das Netz aus dem IPAM (ohne Praefix), wenn alle Router darin liegen, sonst
    Zone/Interface, sonst AS bzw. IP."""
    nets = {str(r.get("network") or "").rsplit(" (", 1)[0] for r in routers}
    if len(nets) == 1 and "" not in nets:
        return nets.pop()
    if ports:
        return ", ".join(ports[:2])
    asn = next((r["remote_as"] for r in routers if r["remote_as"]), None)
    return f"AS {asn}" if asn else routers[0]["ip"]


def suggestions(session: Session, tenant_id: int, include_covered: bool = False) -> list[dict]:
    """Erkannte Anbindungen mit allen Routern und Firewalls; ohne die schon ganz abgedeckten
    (`include_covered` = alle). `exits` = Anbindungen, die einen Teil schon abdecken."""
    g = Graph(session, tenant_id)
    found = detect(g)
    out = []
    for ips in _groups(g, found):
        routers = [_router(g, ip, found[ip]) for ip in ips]
        covered = all(r["covered"] for r in routers)
        if covered and not include_covered:
            continue
        uses = [x for ip in ips for x in found[ip].values()]
        ports = sorted({p for x in uses if (p := x.zone or x.interface)})
        exits: dict[int, TopoExit] = {x.exit.id: x.exit for x in uses if x.exit is not None}
        sids = sorted({x.key[0] for x in uses})
        sources = {x.key[0]: g.nodes[x.key].source for x in uses}
        out.append(
            {
                "key": ips[0],
                "name": _name(routers, ports),
                "kind": _kind(routers, ports),
                "source_system_ids": sids,
                "sources": [sources[s] for s in sids],
                "interfaces": ports,
                "next_hops": routers,
                "exits": [_ref(e) for e in exits.values()],
                "covered": covered,
                "redundancy": _redundancy({ip: found[ip] for ip in ips}),
            }
        )
    out.sort(key=lambda s: (s["sources"], s["interfaces"], _ip_sort(s["key"])))
    return out


def status(session: Session, tenant_id: int) -> dict[int, dict]:
    """Je Anbindung die erkannten Router, die sie abdeckt (wie im Weg), mit Redundanz; dazu
    die Router, die sie noch nicht nennt (`missing`), und genannte, die nicht erkannt sind."""
    g = Graph(session, tenant_id)
    found = detect(g)
    per: dict[int, dict[str, dict[Key, Use]]] = defaultdict(lambda: defaultdict(dict))
    for ip, uses in found.items():
        for key, x in uses.items():
            if x.exit is not None:
                per[x.exit.id][ip][key] = x
    out = {}
    for e in g.exits:
        mine = per.get(e.id, {})
        routers = [_router(g, ip, mine[ip]) for ip in sorted(mine, key=_ip_sort)]
        listed = set(e.next_hops or [])
        sids = sorted({u["source_system_id"] for r in routers for u in r["uses"]})
        out[e.id] = {
            "routers": [{**r, "listed": r["ip"] in listed} for r in routers],
            "missing": [r["ip"] for r in routers if r["ip"] not in listed],
            "unseen": [ip for ip in e.next_hops or [] if ip not in mine],
            "source_system_ids": sids,
            "redundancy": _redundancy(mine),
        }
    return out
