"""AS-Nummern im Routing einzeln (1.19.0, ADR 0066) - nur aus den schon gelesenen BGP-Daten.

Anwender 2026-10-30 (Roadmap Version 2 Nr. 10): „AS-Nummern im Routing einzeln darstellen (je AS
die Nachbarn, Firewalls und Router)“. Neue Ansicht „AS-Nummern“ neben dem Routing; gelesen wird
nichts Neues (BGP in der Doku seit ADR 0041).

Je AS:

- **Geraete mit diesem AS** (lokal): FortiGates aus ihrem BGP-Prozess (`topo_bgp_object`, `as`)
  als Firewall bzw. VDOM, Router und L3-Switches aus dem lokalen AS ihrer Nachbarn
  (`topo_peer.attrs.local_as`). Das lokale AS des ACI-Fabrics liest Automato nicht - die
  Border-Leafs stehen nur als Ende ihrer Nachbarn da.
- **Nachbarn mit diesem AS**: die BGP-Nachbarn (`topo_peer`: Monitor der FortiGate, Tabelle der
  Router, Sitzungen der Border-Leafs), dazu konfigurierte FortiGate-Nachbarn ohne Sitzung und
  die Peers der ACI-L3Outs - je mit dem Geraet, an dem die Sitzung steht, Zustand, Art
  (eBGP/iBGP), den Namen aus dem IPAM und dem gelesenen Geraet dahinter.
- **eigen** (AS der Firma wie die Firmengrenze, `boundary.own_as`, bzw. ein gelesenes Geraet hat
  es lokal) oder fremd, **privat** (64512-65534, 4200000000-4294967294).

Nur lesend; die Reservierungen (`store/reservations`) nutzen die Liste als „in Gebrauch“.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SourceSystem, TopoBgpObject, TopoL3Out, TopoPeer
from app.store import boundary, paths

MAX_AS = 4294967295
# Art des lokalen Endes je Knoten der Topologie (`paths.Node.kind`)
_KIND = {"firewall": "firewall", "vdom": "firewall", paths.L3: "router", "vrf": "fabric"}


def asn(value) -> int | None:
    """AS-Nummer aus `65001`, `"65001"` bzw. asdot `1.10` (= 65546); sonst None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 < value <= MAX_AS else None
    text = str(value if value is not None else "").strip()
    if re.fullmatch(r"\d+", text):
        n = int(text)
    else:
        m = re.fullmatch(r"(\d+)\.(\d+)", text)
        n = int(m.group(1)) * 65536 + int(m.group(2)) if m else 0
    return n if 0 < n <= MAX_AS else None


def _bare(ip) -> str:
    return str(ip or "").split("%")[0].split("/")[0]


@dataclass
class Device:
    """Gelesenes Geraet (Kontext) als lokales Ende: Firewall, Router bzw. Fabric (Border-Leaf)."""

    kind: str  # firewall | router | fabric
    label: str
    source: str
    source_system_id: int
    node: str
    context: str
    router_id: str | None = None
    l3out: str | None = None

    @property
    def key(self) -> tuple:
        return (self.source_system_id, self.node, self.context, self.l3out)

    def out(self) -> dict:
        return {k: v for k, v in (
            ("kind", self.kind), ("label", self.label), ("source", self.source),
            ("source_system_id", self.source_system_id), ("node", self.node),
            ("context", self.context), ("router_id", self.router_id), ("l3out", self.l3out),
        ) if v not in (None, "")}  # fmt: skip


@dataclass
class Neighbor:
    peer_ip: str
    at: Device  # Geraet, an dem die Sitzung steht
    local_as: int | None = None
    state: str | None = None
    session: str | None = None  # ebgp | ibgp
    configured_only: bool = False  # nur konfiguriert (keine Sitzung gelesen)
    shutdown: bool = False
    description: str | None = None

    @property
    def established(self) -> bool:
        return (self.state or "").lower() == "established"


@dataclass
class AsEntry:
    asn: int
    devices: list[Device] = field(default_factory=list)
    neighbors: list[Neighbor] = field(default_factory=list)
    own: bool = False
    private: bool = False

    def add_device(self, d: Device) -> None:
        if all(x.key != d.key for x in self.devices):
            self.devices.append(d)


class Index:
    """AS-Nummern eines Mandanten aus den gelesenen BGP-Daten - einmal gebaut."""

    def __init__(self, session: Session, tenant_id: int, graph: paths.Graph | None = None):
        self.session, self.tenant_id = session, tenant_id
        self.graph = graph or paths.Graph(session, tenant_id)
        self.names = dict(session.execute(
            select(SourceSystem.id, SourceSystem.name).where(SourceSystem.tenant_id == tenant_id)
        ).all())  # fmt: skip
        self.entries: dict[int, AsEntry] = {}
        self._build()

    def _entry(self, number: int) -> AsEntry:
        e = self.entries.get(number)
        if e is None:
            e = self.entries[number] = AsEntry(number, private=boundary.private_as(number))
        return e

    def _device(self, sid: int, node: str, context: str, **extra) -> Device:
        found = self.graph.nodes.get((sid, context))
        kind, label = self.graph.label(sid, node, context)
        kind = kind or (found.kind if found is not None else "")
        return Device(_KIND.get(kind, "router"), label, self.names.get(sid, f"#{sid}"), sid, node,
                      context, **extra)  # fmt: skip

    def _build(self) -> None:
        tid = self.tenant_id
        configs: dict[tuple[int, str, str], dict] = {}
        for o in self.session.scalars(
            select(TopoBgpObject)
            .where(TopoBgpObject.tenant_id == tid, TopoBgpObject.kind == "bgp")
            .order_by(TopoBgpObject.id)
        ):  # fmt: skip
            data = o.data or {}
            key = (o.source_system_id, o.node, o.context)
            configs[key] = data
            local = asn(data.get("as"))
            if local is not None:
                self._entry(local).add_device(
                    self._device(*key, router_id=data.get("router_id") or None)
                )
        seen: set[tuple] = set()
        for p in self.session.scalars(
            select(TopoPeer).where(TopoPeer.tenant_id == tid).order_by(TopoPeer.id)
        ):
            key = (p.source_system_id, p.node, p.context)
            attrs = p.attrs or {}
            conf = attrs.get("config") or {}
            dev = self._device(*key)
            local = asn(attrs.get("local_as")) or asn((configs.get(key) or {}).get("as"))
            if local is not None and dev.kind == "router":
                self._entry(local).add_device(dev)
            remote = asn(conf.get("remote_as")) or asn(p.remote_as)
            ip = _bare(p.peer_ip)
            if remote is None or not ip:
                continue
            seen.add((*key, ip))
            kind = attrs.get("type") if attrs.get("type") in ("ebgp", "ibgp") else None
            if kind is None and local is not None:
                kind = "ibgp" if local == remote else "ebgp"
            self._entry(remote).neighbors.append(Neighbor(
                ip, dev, local, p.state, kind, shutdown=bool(conf.get("shutdown")),
                description=conf.get("description") or None))  # fmt: skip
        for key, data in sorted(configs.items()):  # konfiguriert, ohne Sitzung im Monitor
            local = asn(data.get("as"))
            for ip, conf in sorted((data.get("neighbors") or {}).items()):
                remote = asn((conf or {}).get("remote_as"))
                if remote is None or (*key, _bare(ip)) in seen:
                    continue
                kind = None if local is None else ("ibgp" if local == remote else "ebgp")
                self._entry(remote).neighbors.append(Neighbor(
                    _bare(ip), self._device(*key), local, None, kind, configured_only=True,
                    shutdown=bool((conf or {}).get("shutdown")),
                    description=(conf or {}).get("description") or None))  # fmt: skip
        for o in self.session.scalars(
            select(TopoL3Out).where(TopoL3Out.tenant_id == tid).order_by(TopoL3Out.id)
        ):
            for peer in o.peers or []:
                remote, ip = asn((peer or {}).get("asn")), _bare((peer or {}).get("ip"))
                if remote is None or not ip:
                    continue
                entry = self._entry(remote)
                sid = o.source_system_id
                same = [n for n in entry.neighbors
                        if n.at.source_system_id == sid and n.peer_ip == ip]  # fmt: skip
                if same:  # Sitzung am Border-Leaf schon gelesen: L3Out dazuschreiben
                    for n in same:
                        n.at.l3out = n.at.l3out or o.name
                    continue
                source = self.names.get(o.source_system_id, f"#{o.source_system_id}")
                vrf = f" · VRF {o.vrf}" if o.vrf else ""
                dev = Device("fabric", f"{source} · L3Out {o.name}{vrf}", source,
                             o.source_system_id, "", o.vrf or "", l3out=o.name)  # fmt: skip
                entry.neighbors.append(Neighbor(ip, dev, None, None, None, configured_only=True))
        own = self.graph.boundary().own_as()
        for e in self.entries.values():
            e.own = e.asn in own or bool(e.devices)

    def used(self) -> dict[int, list[str]]:
        """AS -> wo in Gebrauch (Texte, deutsch - fuer Befunde und Reservierungen)."""
        out: dict[int, list[str]] = {}
        for number, e in sorted(self.entries.items()):
            where = [f"{d.label} (lokal)" for d in e.devices]
            where += [f"{n.at.label} → Nachbar {n.peer_ip}" for n in e.neighbors]
            out[number] = where
        return out

    def neighbor_out(self, n: Neighbor) -> dict:
        row = {
            "peer_ip": n.peer_ip,
            **self.graph.resolve(n.peer_ip),
            "peer_node": self.graph.owner_label(n.peer_ip),
            "at": n.at.out(),
            "local_as": n.local_as,
            "state": n.state,
            "established": n.established,
            "session": n.session,
            "configured_only": n.configured_only,
            "shutdown": n.shutdown,
            "description": n.description,
        }
        return {k: v for k, v in row.items() if v not in (None, "", False) or k == "established"}
