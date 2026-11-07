"""L3Outs: die Firewall entscheidet, nicht das ACI (ADR 0034, Anwender 2026-10-27).

Hinter einem L3Out steht im Betrieb eine Firewall, die den Verkehr regelt; die Subnetze der
externen EPGs im ACI sind dagegen meist weit offen (192.168.0.0/16 am L3O_bit, 0.0.0.0/0 am
L3O_inet). Darum:

- **Zuordnung L3Out -> Firewall:** abgeleitet aus den gelesenen Daten wie im Topologie-Modell
  (`topo_model`): eine SVI bzw. ein Interface des L3Outs im selben Netz wie ein Interface der
  Firewall (Transfernetz, auch mit abweichender Maske), ein BGP-Peer bzw. Next-Hop einer
  statischen Route des L3Outs = Adresse eines Firewall-Interfaces. Von Hand pflegbar
  (`l3out_firewall`, Operator): bestaetigen, eine andere Firewall waehlen, „keine“; ohne Zeile
  gilt die Ableitung. Je L3Out also Firewalls (Quellsystem, VDOM, Interface zum L3Out) bzw.
  keine/unbekannt.
- **Ueber welchen L3Out kommt eine Gegenstelle (`decide`):** je L3Out der VRF fragt die
  Firewall dahinter ihr Routing - Route zur IP (laengstes Praefix in der VRF des Interfaces zum
  L3Out) ueber ein Interface **ausserhalb** (nicht zurueck ins ACI: weder ueber das Interface zu
  einem L3Out noch ueber einen Next-Hop des Fabrics). Es gewinnt die genaueste Route (Best
  Match); gleich genaue: mehrere L3Outs. Leitet die Firewall die IP zurueck ins ACI, kommt sie
  nicht ueber diesen L3Out. Ohne Firewall bzw. ohne Route bleibt der L3Out „unbekannt“ - dort
  gilt weiter die Zuordnung im ACI (`aci_relevance`).

Nur lesend, Stand der letzten Laeufe; die Zuordnung von Hand ist Datenhaltung der Plattform.
"""

import ipaddress
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import InvInterface, L3OutFirewall, TopoL3Out
from app.store.paths import Graph, Key
from app.store.topology import MGMT

DERIVED, CONFIRMED, MANUAL, NONE = "derived", "confirmed", "manual", "none"
FIREWALL_KINDS = ("firewall", "vdom")
SUPERNET_FLOOR = {4: 16, 6: 64}  # wie im Topologie-Modell: abweichende Masken nur bis hier


@dataclass(frozen=True)
class FwRef:
    """Firewall hinter einem L3Out: Quellsystem, Kontext (VDOM) und das Interface zum L3Out."""

    source_system_id: int
    context: str
    interface: str | None = None
    zone: str | None = None

    @property
    def key(self) -> Key:
        return (self.source_system_id, self.context)

    def same(self, other: "FwRef") -> bool:
        return (self.key, self.interface) == (other.key, other.interface)

    def out(self, g: Graph) -> dict:
        node = g.nodes.get(self.key)
        return {
            "source_system_id": self.source_system_id,
            "vdom": self.context,
            "interface": self.interface,
            "zone": self.zone,
            "node": f"{self.source_system_id}|{self.context}",
            "label": node.label if node else f"#{self.source_system_id} · {self.context}",
            "source": node.source if node else None,
            "read": node is not None,
        }


@dataclass
class L3Out:
    source_system_id: int  # APIC
    aci_tenant: str
    name: str
    dn: str
    vrf: str | None
    derived: list[FwRef] = field(default_factory=list)
    manual: list[FwRef] | None = None  # gepflegt; [] = ausdruecklich keine Firewall
    origin: str = DERIVED
    updated_by: str | None = None
    updated_at: datetime | None = None

    @property
    def firewalls(self) -> list[FwRef]:
        return self.manual if self.manual is not None else self.derived

    def out(self, g: Graph) -> dict:
        """Zuordnung fuer die Anzeige (Routing -> L3Outs): Herkunft, Firewalls, abgeleitet."""
        return {
            "origin": self.origin,
            "firewalls": [f.out(g) for f in self.firewalls],
            "derived": [f.out(g) for f in self.derived],
            "updated_by": self.updated_by,
            "updated_at": self.updated_at,
        }


@dataclass
class Decision:
    """Ueber welche L3Outs einer VRF eine IP nach dem Routing der Firewalls kommt."""

    best: list[str]  # L3Outs mit der genauesten Route nach aussen
    reasons: dict[str, str]  # L3Out -> „fw-01 routet 203.0.113.0/24 über wan1“
    back: dict[str, str]  # L3Out -> die Firewall leitet die IP zurueck ins ACI
    unknown: list[str]  # ohne Firewall bzw. ohne Route
    names: list[str] = field(default_factory=list)  # alle gelesenen L3Outs der VRF

    def reason(self, names: list[str] | None = None) -> str:
        return "; ".join(self.reasons[n] for n in (names or self.best) if n in self.reasons)


class Behind:
    """L3Outs eines Mandanten mit der Firewall dahinter; einmal je Mandant geladen."""

    def __init__(self, session: Session, tenant_id: int,
                 graph: Callable[[], Graph] | Graph | None = None):  # fmt: skip
        self.session = session
        self.tenant_id = tenant_id
        self._graph_of = graph
        self._cache: dict[tuple, Decision | None] = {}
        self._status: dict[tuple, tuple[str, int, str] | None] = {}

    @cached_property
    def graph(self) -> Graph:
        if isinstance(self._graph_of, Graph):
            return self._graph_of
        if callable(self._graph_of):
            return self._graph_of()
        return Graph(self.session, self.tenant_id)

    # -- Zuordnung ------------------------------------------------------------------------

    @cached_property
    def l3outs(self) -> list[L3Out]:
        rows = list(
            self.session.scalars(
                select(TopoL3Out)
                .where(TopoL3Out.tenant_id == self.tenant_id)
                .order_by(TopoL3Out.source_system_id, TopoL3Out.aci_tenant, TopoL3Out.name)
            )
        )
        if not rows:
            return []  # ohne gelesene L3Outs (nur Firewalls bzw. APIC ohne Routing)
        manual: dict[tuple[int, str], list[L3OutFirewall]] = defaultdict(list)
        for m in self.session.scalars(
            select(L3OutFirewall)
            .where(L3OutFirewall.tenant_id == self.tenant_id)
            .order_by(L3OutFirewall.id)
        ):
            manual[(m.aci_source_system_id, m.l3out_dn)].append(m)
        zones = self._zones()
        out = []
        for o in rows:
            item = L3Out(o.source_system_id, o.aci_tenant, o.name, o.dn, o.vrf, self._derive(o))
            kept = manual.get((o.source_system_id, o.dn))
            if kept:
                item.origin = kept[0].origin
                item.updated_by, item.updated_at = kept[-1].updated_by, kept[-1].updated_at
                item.manual = [
                    FwRef(m.firewall_id, m.vdom or "", m.interface,
                          zones.get((m.firewall_id, m.interface or "")))
                    for m in kept if m.firewall_id is not None
                ]  # fmt: skip
            out.append(item)
        return out

    def _zones(self) -> dict[tuple[int, str], str]:
        self.graph.owners()  # laedt die Zonen
        return self.graph._zones

    @cached_property
    def _firewall_addrs(self) -> list[tuple[Key, str, str | None, object, object]]:
        """Adressen der Firewall-Interfaces wie im Topologie-Modell (ohne Management, Host-,
        Link-local- und Loopback-Adressen): (Knoten, Interface, Zone, IP, Netz)."""
        g = self.graph
        single: dict[int, list[str]] = defaultdict(list)
        for key in g.nodes:
            single[key[0]].append(key[1])
        sources = [sid for sid, ctxs in single.items()
                   if any(g.nodes[(sid, c)].kind in FIREWALL_KINDS for c in ctxs)]  # fmt: skip
        out = []
        if not sources:
            return out
        for i in self.session.scalars(
            select(InvInterface).where(InvInterface.source_system_id.in_(sources))
        ):
            ctxs = single[i.source_system_id]
            key = (i.source_system_id, i.vdom or (ctxs[0] if len(ctxs) == 1 else ""))
            if key not in g.nodes or g.nodes[key].kind not in FIREWALL_KINDS:
                continue
            if g.table(key, i.name) == MGMT:
                continue  # Management-Interface: kein Weg fuer Daten
            for text in i.ips or []:
                if "/" not in str(text):
                    continue
                try:
                    iface = ipaddress.ip_interface(str(text))
                except ValueError:
                    continue
                net = iface.network
                if (
                    net.prefixlen == net.max_prefixlen
                    or iface.ip.is_link_local
                    or (iface.ip.is_loopback)
                ):
                    continue
                out.append((key, i.name, i.zone, iface.ip, net))
        return out

    def _derive(self, o: TopoL3Out) -> list[FwRef]:
        """Firewalls am L3Out: Interface im Netz einer SVI bzw. eines Interfaces des L3Outs (auch
        mit abweichender Maske bis /16 bzw. /64), BGP-Peer bzw. Next-Hop einer statischen Route
        = Interface-IP der Firewall."""
        g = self.graph
        found: dict[tuple[Key, str | None], FwRef] = {}
        svis = []
        for p in o.paths or []:
            for e in p.get("ips") or []:
                try:
                    iface = ipaddress.ip_interface(str(e.get("ip") or ""))
                except ValueError:
                    continue
                if iface.network.prefixlen < iface.network.max_prefixlen:
                    svis.append(iface)
        for key, name, zone, ip, net in self._firewall_addrs:
            for s in svis:
                if ip.version != s.ip.version or ip == s.ip:
                    continue
                floor = SUPERNET_FLOOR[ip.version]
                if (ip in s.network and s.network.prefixlen >= floor) or (
                    s.ip in net and net.prefixlen >= floor
                ):
                    found.setdefault((key, name), FwRef(*key, name, zone))
        hops = [str(p.get("ip") or "").split("/")[0] for p in o.peers or []]
        for n in o.nodes or []:
            for r in n.get("static_routes") or []:
                hops += [str(h) for h in r.get("next_hops") or []]
        owners = g.owners()
        for ip in hops:
            for key, iface, zone in owners.get(ip, []):
                if g.nodes[key].kind in FIREWALL_KINDS:
                    found.setdefault((key, iface), FwRef(*key, iface, zone))
        return sorted(found.values(), key=lambda f: (f.key, f.interface or ""))

    @cached_property
    def _by_name(self) -> dict[tuple[int, str, str], L3Out]:
        return {(o.source_system_id, o.aci_tenant, o.name): o for o in self.l3outs}

    @cached_property
    def _by_vrf(self) -> dict[tuple[int, str], list[L3Out]]:
        out: dict[tuple[int, str], list[L3Out]] = defaultdict(list)
        for o in self.l3outs:
            out[(o.source_system_id, o.vrf or "")].append(o)
        return out

    def of(self, sid: int | None, aci_tenant: str | None, name: str | None) -> L3Out | None:
        """L3Out einer externen EPG (APIC, Tenant, Name) - None, wenn er nicht gelesen ist."""
        if sid is None or not name:
            return None
        found = self._by_name.get((sid, aci_tenant or "", name))
        if found is None:  # externe EPG eines L3Outs aus einem anderen Tenant (z. B. common)
            same = [o for o in self.l3outs if (o.source_system_id, o.name) == (sid, name)]
            found = same[0] if len(same) == 1 else None
        return found

    def in_vrf(self, sid: int | None, vrf: str | None) -> list[L3Out]:
        return self._by_vrf.get((sid or 0, vrf or ""), [])

    @cached_property
    def _groups(self) -> dict[int, str | None]:
        return {n.source_system_id: n.group for n in self.graph.nodes.values()}

    def _same_firewall(self, fw: FwRef, sid: int, vdom: str | None) -> bool:
        """Firewall der Regel (Quellsystem, VDOM) ist die Firewall hinter dem L3Out - auch ein
        Partner im Verbund paralleler Firewalls (ADR 0020)."""
        if vdom and fw.context and vdom != fw.context:
            return False
        if fw.source_system_id == sid:
            return True
        group = self._groups.get(sid)
        return group is not None and self._groups.get(fw.source_system_id) == group

    def for_rule(self, sid: int, vdom: str | None, vrfs: set | None,
                 ports: list[str] | None = None) -> list[L3Out]:  # fmt: skip
        """L3Outs (in den VRFs `vrfs`), hinter denen die Firewall einer Regel steht; `ports` =
        Interfaces bzw. Zonen der Regel zum Fabric (leer bzw. any = jedes)."""
        names = {p for p in ports or [] if p and p.lower() != "any"}
        out = []
        for o in self.l3outs:
            if vrfs is not None and (o.source_system_id, o.vrf) not in vrfs:
                continue
            fws = [f for f in o.firewalls if self._same_firewall(f, sid, vdom)]
            if names:
                fws = [f for f in fws if f.interface is None or {f.interface, f.zone} & names]
            if fws:
                out.append(o)
        return out

    def _aci_ports(self, key: Key) -> set[str]:
        """Interfaces bzw. Zonen einer Firewall zu einem L3Out (abgeleitet bzw. gepflegt)."""
        out: set[str] = set()
        for o in self.l3outs:
            for f in [*o.firewalls, *o.derived]:
                if f.key == key and f.interface:
                    out.add(f.interface)
        return out

    # -- Routing der Firewall -------------------------------------------------------------

    def decide(self, sid: int | None, vrf: str | None, ip) -> Decision | None:
        """Ueber welche L3Outs der VRF kommt die IP nach dem Routing der Firewalls dahinter?
        None ohne Aussage (keine Firewall routet sie, keine leitet sie zurueck)."""
        key = (sid, vrf, str(ip))
        if key not in self._cache:
            self._cache[key] = self._decide(sid, vrf, ip)
        return self._cache[key]

    def _decide(self, sid: int | None, vrf: str | None, ip) -> Decision | None:
        outs = self.in_vrf(sid, vrf)
        if not outs:
            return None
        cands: dict[str, tuple[int, str]] = {}
        back: dict[str, str] = {}
        unknown: list[str] = []
        for o in outs:
            best: tuple[int, str] | None = None
            for fw in o.firewalls:
                st = self.status(fw, ip)
                if st is None:
                    continue
                kind, plen, text = st
                if kind == "out":
                    if best is None or plen > best[0]:
                        best = (plen, text)
                else:
                    back.setdefault(o.name, text)
            if best is not None:
                cands[o.name] = best
                back.pop(o.name, None)
            elif o.name not in back and o.name not in unknown:
                unknown.append(o.name)
        if not cands and not back:
            return None
        top = max((plen for plen, _t in cands.values()), default=None)
        best_names = sorted(n for n, (plen, _t) in cands.items() if plen == top)
        return Decision(best_names, {n: t for n, (_p, t) in cands.items()}, back, unknown,
                        [o.name for o in outs])  # fmt: skip

    def status(self, fw: FwRef, ip) -> tuple[str, int, str] | None:
        """Route der Firewall zur IP: („out“ | „back“, Praefixlaenge, Begruendung); None ohne
        gelesenes Routing bzw. ohne Route."""
        key = (fw, str(ip))
        if key not in self._status:
            self._status[key] = self._route_status(fw, ip)
        return self._status[key]

    def _route_status(self, fw: FwRef, ip) -> tuple[str, int, str] | None:
        g = self.graph
        node = g.nodes.get(fw.key)
        if node is None:
            return None
        table = g.table(fw.key, fw.interface) if fw.interface else 0
        route = g.route(fw.key, ip, table)
        if route is None or not route["next_hops"]:
            return None  # keine Route (bzw. ohne Next-Hop, z. B. Blackhole): keine Aussage
        ports = self._aci_ports(fw.key) | ({fw.interface} if fw.interface else set())
        # 0.111.0 (Anwender): die IP im Transfernetz dieses L3Outs (angeschlossen am Interface
        # der Firewall zu ihm) liegt auf der Verbindung selbst - sie gehoert zu diesem L3Out,
        # nicht „zurueck ins ACI“ (sonst gewann ein anderer L3Out mit seiner Default-Route)
        hops = route["next_hops"] or []
        if (fw.interface and route["protocol"] in ("connected", "local")
                and all(h.get("interface") == fw.interface for h in hops)):  # fmt: skip
            prefix = route["prefix"]
            return (
                "out",
                route["prefix_len"],
                L(
                    f"{node.label} hat {prefix} angeschlossen ({fw.interface}, Transfernetz zum "
                    "L3Out)",
                    f"{node.label} has {prefix} connected ({fw.interface}, transfer network to the "
                    "L3Out)",
                ),
            )
        owners = g.owners()
        ifaces: list[str] = []
        outward = False
        for h in route["next_hops"] or []:
            nh = str(h.get("ip") or "").split("%")[0]
            iface = h.get("interface") or (g.interface_to(fw.key, nh, table) if nh else None)
            if iface:
                ifaces.append(iface)
            to_fabric = (iface in ports) if iface else False
            if nh and any(g.nodes[k].kind == "vrf" for k, _i, _z in owners.get(nh, [])):
                to_fabric = True
            if not to_fabric:
                outward = True
        via = ", ".join(dict.fromkeys(ifaces)) or "?"
        prefix = route["prefix"]
        if not outward:
            return (
                "back",
                route["prefix_len"],
                L(
                    f"{node.label} leitet {ip} zurück ins ACI ({prefix} über {via})",
                    f"{node.label} routes {ip} back into the ACI ({prefix} via {via})",
                ),
            )
        return "out", route["prefix_len"], L(f"{node.label} routet {prefix} über {via}",
                                             f"{node.label} routes {prefix} via {via}")  # fmt: skip


# -- Pflege von Hand ------------------------------------------------------------------------


def find(session: Session, tenant_id: int, aci_id: int, dn: str) -> TopoL3Out | None:
    return session.scalars(
        select(TopoL3Out).where(
            TopoL3Out.tenant_id == tenant_id,
            TopoL3Out.source_system_id == aci_id,
            TopoL3Out.dn == dn,
        )
    ).first()


def assign(session: Session, tenant_id: int, l3out: TopoL3Out, firewalls: list[FwRef],
           origin: str, actor: str) -> None:  # fmt: skip
    """Zuordnung von Hand setzen (ersetzt die bisherige): Firewalls bzw. keine (`origin` none)."""
    clear(session, l3out.source_system_id, l3out.dn)
    base = {"tenant_id": tenant_id, "aci_source_system_id": l3out.source_system_id,
            "l3out_dn": l3out.dn, "l3out_name": l3out.name, "updated_by": actor}  # fmt: skip
    if origin == NONE or not firewalls:
        session.add(L3OutFirewall(**base, firewall_id=None, origin=NONE))
    for f in firewalls if origin != NONE else []:
        session.add(L3OutFirewall(**base, firewall_id=f.source_system_id, vdom=f.context,
                                  interface=f.interface, origin=origin))  # fmt: skip
    session.flush()


def clear(session: Session, aci_id: int, dn: str) -> int:
    """Zuordnung von Hand entfernen - es gilt wieder die Ableitung."""
    result = session.execute(
        delete(L3OutFirewall).where(
            L3OutFirewall.aci_source_system_id == aci_id, L3OutFirewall.l3out_dn == dn
        )
    )
    session.flush()
    return result.rowcount or 0


def choices(session: Session, tenant_id: int, graph: Graph | None = None) -> list[dict]:
    """Firewalls zur Auswahl (gelesene Kontexte) mit ihren Interfaces (ohne Management)."""
    g = graph or Graph(session, tenant_id)
    nodes = {k: n for k, n in g.nodes.items() if n.kind in FIREWALL_KINDS}
    single: dict[int, list[str]] = defaultdict(list)
    for sid, ctx in nodes:
        single[sid].append(ctx)
    ifaces: dict[Key, list[dict]] = defaultdict(list)
    if single:
        for i in session.scalars(
            select(InvInterface)
            .where(InvInterface.source_system_id.in_(list(single)))
            .order_by(InvInterface.name)
        ):
            ctxs = single[i.source_system_id]
            key = (i.source_system_id, i.vdom or (ctxs[0] if len(ctxs) == 1 else ""))
            if key in nodes and g.table(key, i.name) != MGMT:
                ifaces[key].append({"name": i.name, "zone": i.zone, "ips": list(i.ips or [])})
    return [
        {"source_system_id": key[0], "vdom": key[1], "label": n.label, "source": n.source,
         "interfaces": ifaces.get(key, [])}
        for key, n in sorted(nodes.items(), key=lambda kv: kv[1].label.lower())
    ]  # fmt: skip
