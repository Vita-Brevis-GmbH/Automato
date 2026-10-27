"""Weg einer Verbindung durchs Netz (ADR 0019 Stufe 3, erste Fassung 0.17.0).

Knoten sind Routing-Instanzen: ein FortiGate-VDOM (bzw. die Firewall, wenn sie nur `root`
hat) oder der Rand einer ACI-VRF - alle Border-Leafs der VRF zusammen, vier Anbindungen sind
ein Knoten und nicht vier Wege. Seit ADR 0032 auch Router und L3-Switches (Switches & Router,
Art `l3`): je Geraet und VRF ein Knoten; ihre Adressen kommen aus den `local`-Routen.

- **Start:** der gewaehlte Startpunkt bzw. der Knoten, an dem die Quelle angeschlossen ist
  (angeschlossenes Netz in der RIB, sonst EPG-/BD-Subnetz einer gelesenen ACI-VRF).
- **Je Hop:** eine Policy-Route bzw. SD-WAN-Regel, die sicher passt, geht vor; sonst das
  laengste Praefix der RIB (ECMP verzweigt) - in der VRF des Eingangs (FortiGate; ohne
  Eingang die Standard-VRF 0, nie die Management-Tabelle).
- **Naechster Knoten:** dem der Next-Hop gehoert (Interface-IP der FortiGate inkl.
  Sekundaer-IPs, SVI/Interface eines L3Outs). Gehoert er keinem gelesenen Geraet, endet der
  Weg dort - kein Raten ueber ein gemeinsames Transfernetz (0.17.1: das fuehrte bei mehreren
  Firewalls am selben Aussenrouter zu Schleifen).
- **Anbindung nach aussen** (0.18.0, von Hand erfasst, `TopoExit`): gehoert der Next-Hop
  keinem gelesenen Geraet, aber passt eine Anbindung (Next-Hop-IP, sonst Interface bzw. Zone
  der Firewall), endet der Weg dort („Internet“, „MPLS …“) - oder geht nach ihren manuellen
  Routen beim gelesenen Knoten weiter (z. B. ueber MPLS zur Firewall eines anderen Standorts).
  0.20.0: fuer eine oder mehrere Firewalls (z. B. beide Session-Sync-Partner); erkannte
  Anbindungen mit allen Routern in `store/exits.py`.
- **Ende:** Ziel angeschlossen, Anbindung, Default-Route ohne gelesenen Nachbarn
  („extern“), Next-Hop ohne gelesenes Geraet, keine Route, Schleife oder Hop-Grenze -
  Next-Hops mit dem Namen aus dem IPAM.
- **Firmengrenze** (ADR 0032, `store/boundary.py`): endet der Weg an einem nicht gelesenen
  Next-Hop mit eBGP zu einem fremden AS, an einer Anbindung Internet/MPLS/Partner bzw. mit der
  Default-Route Richtung Provider, steht das am Weg (`path.boundary`: Grund, AS, Knoten,
  Interface) und am letzten Hop (`hop.boundary`).
- **Redundante Wege** (0.19.0): neben den aktiven Wegen (ECMP verzweigt, parallele Firewalls
  eines Verbunds) die Backup-Wege - je Weg eine Abweichung an einem Hop ueber einen
  BGP-Pfad bzw. eine statische Route fuer dasselbe Praefix mit anderem Next-Hop (schwebende
  Route, Backup-Provider). Gezeigt werden Backup-Wege, die ein Ziel erreichen und hoechstens
  `slack` Hops laenger sind als der kuerzeste aktive Weg („gleiche oder fast gleiche Laenge“).
- **Verbund als ein Hop** (0.29.0): Wege, die sich nur in den Firewalls eines Verbunds (z. B.
  Session-Sync-Paar) unterscheiden, sind ein Weg; der Verbund ist ein Hop mit den Firewalls
  darin (`parts`, je mit Ein-/Ausgang und Regeln). ECMP ueber andere Router bleibt je Weg.
- **Regeln auf dem Weg** (0.22.0, `store/policy_check.py`): je FortiGate-Hop die Policies, die
  auf Ein-/Ausgang, Quelle, Ziel und Dienst passen, mit dem Urteil; je Weg das Gesamturteil.
- **Inter-VDOM-Links** (0.27.0): Geht eine Route ueber einen VDOM-Link ohne Next-Hop-IP (bzw.
  mit einer IP, die keinem gelesenen Interface gehoert), fuehrt der Weg zum VDOM am anderen
  Ende: `<name>0` <-> `<name>1` derselben FortiGate (vdom-link bzw. NPU-vlink), VLAN-
  Subinterfaces ueber Parent und VLAN-ID.
- **Netz als Quelle bzw. Ziel** (0.39.0): als Ganzes - es gilt die Route, die das ganze Netz
  enthaelt (laengstes solches Praefix); genauere Routen innerhalb des Netzes mit anderem
  Next-Hop stehen am Hop (`route.inner`) und als Hinweis. Policies und Contracts pruefen, ob
  sie das ganze Netz erfassen (sonst „nur teilweise“ = moeglich); kein DNAT fuer ein Netz.
- **NAT auf dem Weg** (0.23.0): Ist das Ziel eine VIP der FortiGate (externe IP bzw. Bereich,
  passendes `extintf`), geht der Weg ab diesem Hop zur internen IP weiter (Virtual Server: dem
  ersten Real Server, alle stehen am Hop). SNAT der erlaubenden Policy (IP-Pool bzw.
  Interface-IP) aendert die Quelle fuer die Firewalls danach (`policy_check`).
"""

import ipaddress
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import (
    InvDevice,
    InvInterface,
    InvSubnet,
    NatRule,
    Platform,
    SourceSystem,
    TopoContext,
    TopoExit,
    TopoL3Out,
    TopoPolicyRoute,
    TopoRoute,
)
from app.store import nat as nat_store
from app.store import references
from app.store.topology import (
    MGMT,
    Table,
    aci_vrf,
    endpoint,
    ip_hex,
    is_net,
    matches,
    span,
    table_of,
    within,
)

MAX_HOPS = 12
MAX_PATHS = 16  # aktive Wege
MAX_BACKUP = 16  # Backup-Wege (vor dem Filter nach Laenge)
INNER = 5  # genauere Routen innerhalb eines Netzes je Hop (Anzeige)
REACHED = ("connected", "fabric", "exit", "external")  # Ende = Ziel erreicht bzw. nach aussen
_CONNECTED = ("connected", "local")
_RIB_AFTER = 16  # Abfragen je Kontext, ab denen die RIB als Index im Speicher liegt
Key = tuple[int, str]  # (Quellsystem, Kontext: VDOM bzw. Tenant:VRF)
__all__ = ["MGMT", "Graph", "trace"]

EXIT_LABELS = {
    "internet": "Internet",
    "mpls": "MPLS",
    "wan": "WAN",
    "partner": "Partner",
    "other": "Anbindung",
}
_EXIT_LABELS_EN = {**EXIT_LABELS, "other": "Exit"}


def exit_kind(kind: str | None, default: str | None = None) -> str | None:
    """Art einer Anbindung in der Sprache der Anfrage („Internet“, „MPLS“ … „Anbindung“)."""
    return L(EXIT_LABELS, _EXIT_LABELS_EN).get(kind or "", default)


@dataclass
class Node:
    source_system_id: int
    context: str
    kind: str  # firewall | vdom | vrf
    label: str
    source: str
    members: list[str] = field(default_factory=list)  # Hostname bzw. Border-Leafs
    missing: list[str] = field(default_factory=list)
    group: str | None = None  # Verbund paralleler Firewalls (ADR 0020)

    @property
    def key(self) -> str:
        return f"{self.source_system_id}|{self.context}"

    def out(self) -> dict:
        return {
            "key": self.key,
            "source_system_id": self.source_system_id,
            "source": self.source,
            "context": self.context,
            "kind": self.kind,
            "label": self.label,
            "members": self.members,
            "missing": self.missing,
            "group": self.group,
        }


L3 = "l3"  # Router bzw. L3-Switch (ADR 0032), Kontext je Geraet und VRF


def router_vrf(node: str, context: str) -> str | None:
    """VRF eines Router-Kontexts: `rt-01` = Standard-VRF (None), `rt-01:KUNDE` = KUNDE."""
    return context[len(node) + 1 :] if context.startswith(f"{node}:") else None


def context_label(kind: str, source: str, node: str, context: str, single: bool) -> tuple[str, str]:
    """(Art, Bezeichnung) eines gelesenen Kontexts: FortiGate nur mit `root` = die Firewall,
    sonst „fw · VDOM x“; ACI je Leaf „leaf-101 · VRF T:V“; Router „rt-01“ bzw. „rt-01 · VRF x“."""
    if kind == "vdom":
        return ("firewall", source) if single else ("vdom", f"{source} · VDOM {context}")
    if kind == L3:
        vrf = router_vrf(node, context)
        return L3, f"{node} · VRF {vrf}" if vrf else node
    return "vrf", f"{node} · VRF {context}"


class _Rib:
    """Routen nach Familie und Praefixlaenge: die Routen, die eine IP bzw. ein ganzes Netz
    enthalten, per Nachschlagen je Laenge (hoechstens 33 bzw. 129) statt einer Bereichsabfrage.
    Der Index haelt nur Kennung und Protokoll; die Routen selbst laedt der Graph bei Bedarf."""

    def __init__(self, rows):
        self.by: dict[tuple[int, int], dict[int, list[tuple[int, str]]]] = defaultdict(dict)
        lengths: dict[int, set[int]] = defaultdict(set)
        for rid, family, prefix_len, lo, protocol in rows:
            try:
                start = int(lo, 16)
            except (TypeError, ValueError):
                continue
            self.by[(family, prefix_len)].setdefault(start, []).append((rid, protocol))
            lengths[family].add(prefix_len)
        self.lengths = {f: sorted(v) for f, v in lengths.items()}

    def containing(self, ip, connected: bool = False) -> list[int]:
        bits = 32 if ip.version == 4 else 128
        start = int(ip.network_address) if is_net(ip) else int(ip)
        longest = ip.prefixlen if is_net(ip) else bits
        out: list[int] = []
        for length in self.lengths.get(ip.version, []):
            if length > longest:
                break
            mask = ((1 << length) - 1) << (bits - length) if length else 0
            for rid, protocol in self.by[(ip.version, length)].get(start & mask, []):
                if not connected or protocol in _CONNECTED:
                    out.append(rid)
        return out


class Graph:
    """Knoten, Adressen der Knoten (Interfaces, L3Out-SVIs) und IPAM-Namen eines Mandanten."""

    def __init__(self, session: Session, tenant_id: int):
        self.session = session
        self.tenant_id = tenant_id
        names, groups = {}, {}
        for sid, name, group in session.execute(
            select(SourceSystem.id, SourceSystem.name, SourceSystem.parallel_group).where(
                SourceSystem.tenant_id == tenant_id
            )
        ):
            names[sid], groups[sid] = name, group
        contexts = list(
            session.scalars(
                select(TopoContext)
                .where(TopoContext.tenant_id == tenant_id)
                .order_by(TopoContext.source_system_id, TopoContext.context, TopoContext.node)
            )
        )
        vdoms = Counter(c.source_system_id for c in contexts if c.kind == "vdom")
        self.nodes: dict[Key, Node] = {}
        self.labels: dict[tuple[int, str, str], tuple[str, str]] = {}
        for c in contexts:
            source = names.get(c.source_system_id, f"#{c.source_system_id}")
            single = vdoms[c.source_system_id] == 1
            self.labels[(c.source_system_id, c.node, c.context)] = context_label(
                c.kind, source, c.node, c.context, single
            )
            key = (c.source_system_id, c.context)
            node = self.nodes.get(key)
            if node is None:
                kind, label = context_label(c.kind, source, c.node, c.context, single)
                if c.kind == "vrf":
                    label = f"{source} · VRF {c.context}"
                node = self.nodes[key] = Node(c.source_system_id, c.context, kind, label, source)
                node.group = groups.get(c.source_system_id)
            node.members.append(c.node)
            node.missing += [m for m in c.missing or [] if m not in node.missing]
        self.refs = references.context(session, tenant_id)
        self._owners: dict[str, list[tuple[Key, str | None, str | None]]] | None = None
        self._zones: dict[tuple[int, str], str] = {}
        self._tables: dict[Key, dict[str, Table]] = {}
        self._vips: dict[Key, list[NatRule]] = {}
        self._lbs: list[tuple[NatRule, str]] | None = None  # NSX ALB (ADR 0027), bei Bedarf
        self._vlinks: dict[int, dict[tuple[str, str], tuple[str, str]]] = {}
        self._leaks = None  # aci_shared.Leaks, erst bei Bedarf
        self._ribs: dict[Key | None, _Rib] = {}  # beste RIB-Routen je Kontext (0.74.1)
        self._boundary = None  # boundary.Boundary, erst bei Bedarf (ADR 0032)
        self._rib_asked: Counter = Counter()  # Abfragen je Kontext bis zum Index
        self._loaded: dict[int, TopoRoute] = {}  # Routen aus dem Index, je Kennung
        self.exits = list(
            session.scalars(
                select(TopoExit).where(TopoExit.tenant_id == tenant_id).order_by(TopoExit.id)
            )
        )

    # -- Bezeichnungen --------------------------------------------------------------

    def label(self, source_system_id: int, node: str, context: str) -> tuple[str, str]:
        return self.labels.get((source_system_id, node, context), ("", f"{node} · {context}"))

    def names(self, ip: str | None) -> list[str]:
        """DNS-Namen bzw. Namen der IP aus dem IPAM (fuehrende Quelle und Anreicherung)."""
        return references.dns_names(self.refs, ip) if ip else []

    def network(self, ip: str | None) -> str | None:
        """Ohne Namen der IP: das kleinste IPAM-Netz mit Namen bzw. Beschreibung."""
        if not ip:
            return None
        for idx in [self.refs.primary, *self.refs.enrichments]:
            if idx is None:
                continue
            for r in idx.containing_prefixes(ip):
                text = r.name or r.description
                if text and not r.value.endswith("/0"):
                    return f"{text} ({r.value})"
        return None

    def resolve(self, ip: str | None) -> dict:
        """Namen (IPAM) und - ohne Namen - das Netz; fuer Next-Hops und Nachbarn."""
        names = self.names(ip)
        net = None if names or not ip else self.network(ip)
        return {k: v for k, v in (("names", names), ("network", net)) if v}

    # -- Adressen der Knoten ------------------------------------------------------------

    def owners(self) -> dict[str, list[tuple[Key, str | None, str | None]]]:
        """IP -> (Knoten, Interface, Zone): FortiGate-Interfaces, L3Out-SVIs/-Interfaces und die
        eigenen Adressen der Router (`local`-Routen, ADR 0032)."""
        if self._owners is not None:
            return self._owners
        out: dict[str, list[tuple[Key, str | None, str | None]]] = defaultdict(list)
        sources = {k[0] for k in self.nodes}
        single = defaultdict(list)
        for key, n in self.nodes.items():
            if n.kind != L3:  # Router: Adressen aus den Routen (unten), nicht dem Inventar
                single[key[0]].append(key[1])
        if sources:
            for i in self.session.scalars(
                select(InvInterface).where(InvInterface.source_system_id.in_(sources))
            ):
                if i.zone:
                    self._zones[(i.source_system_id, i.name)] = i.zone
                ctxs = single[i.source_system_id]
                if not ctxs:
                    continue
                context = i.vdom or (ctxs[0] if len(ctxs) == 1 else None)
                key = (i.source_system_id, context or "")
                if key not in self.nodes:
                    continue
                for ip in i.ips or []:
                    out[str(ip).split("/")[0]].append((key, i.name, i.zone))
            for o in self.session.scalars(
                select(TopoL3Out).where(TopoL3Out.source_system_id.in_(sources))
            ):
                key = (o.source_system_id, o.vrf or "")
                if key not in self.nodes:
                    continue
                for path in o.paths or []:
                    for entry in path.get("ips") or []:
                        ip = str(entry.get("ip") or "").split("/")[0]
                        if ip:
                            out[ip].append((key, path.get("interface"), None))
            for key, ip, iface, _virtual in self.router_addresses():
                out[ip].append((key, iface, None))
        self._owners = out
        return out

    def router_addresses(self) -> list[tuple[Key, str, str | None, bool]]:
        """Eigene Adressen der Router bzw. L3-Switches (ADR 0032): Host-Routen `local` der RIB
        (bei Arista aus `show ip interface`), je Kontext mit Interface; `virtual` = Gateway-
        Adresse (HSRP/VRRP, VARP), die auch der Partner hat."""
        routers = {k[0] for k, n in self.nodes.items() if n.kind == L3}
        if not routers:
            return []
        out = []
        for sid, context, prefix, hops, attrs in self.session.execute(
            select(TopoRoute.source_system_id, TopoRoute.context, TopoRoute.prefix,
                   TopoRoute.next_hops, TopoRoute.attrs)
            .where(TopoRoute.tenant_id == self.tenant_id, TopoRoute.table == "rib",
                   TopoRoute.protocol == "local", TopoRoute.prefix_len.in_((32, 128)),
                   TopoRoute.source_system_id.in_(routers))
            .order_by(TopoRoute.id)
        ):  # fmt: skip
            key = (sid, context)
            node = self.nodes.get(key)
            if node is None or node.kind != L3:
                continue
            iface = next((h.get("interface") for h in hops or [] if h.get("interface")), None)
            out.append((key, str(prefix).split("/")[0], iface, bool((attrs or {}).get("virtual"))))
        return out

    def boundary(self):
        """Firmengrenze (ADR 0032, `store/boundary.py`) - einmal je Graph geladen."""
        if self._boundary is None:
            from app.store.boundary import Boundary

            self._boundary = Boundary(self)
        return self._boundary

    def owner_label(self, ip: str | None) -> str | None:
        found = self.owners().get(ip or "", [])
        return self.nodes[found[0][0]].label if found else None

    def zone(self, source_system_id: int, interface: str | None) -> str | None:
        self.owners()  # laedt die Zonen
        return self._zones.get((source_system_id, interface or ""))

    # -- Routen -------------------------------------------------------------------------

    def _routes(self, ip, key: Key | None = None, connected: bool = False) -> list[TopoRoute]:
        """Beste Routen der RIB, die die IP bzw. das ganze Netz enthalten. Seit 0.74.1 aus einem
        Index im Speicher je Kontext (bzw. die angeschlossenen Netze aller Kontexte) statt einer
        Datenbankabfrage je Aufruf - die Kunden-Ableitung, die Relevanz und die Wege fragen das
        Routing tausendfach (Anwender: Speichern und Doku brauchten Minuten)."""
        if key is not None or connected:
            rib = self._ribs.get(key)
            if rib is None:
                # erst ab einigen Abfragen lohnt das Laden des Index eines Kontexts
                self._rib_asked[key] += 1
                if self._rib_asked[key] > _RIB_AFTER:
                    rib = self._ribs[key] = _Rib(self._rib_rows(key))
            if rib is not None:
                return self._route_rows(rib.containing(ip, connected))
        lo, hi = (ip_hex(v) for v in span(ip))
        stmt = select(TopoRoute).where(
            TopoRoute.tenant_id == self.tenant_id,
            TopoRoute.family == ip.version,
            TopoRoute.table == "rib",
            TopoRoute.best.is_(True),
            TopoRoute.lo <= lo,
            TopoRoute.hi >= hi,
        )
        if key is not None:
            stmt = stmt.where(TopoRoute.source_system_id == key[0], TopoRoute.context == key[1])
        if connected:
            stmt = stmt.where(TopoRoute.protocol.in_(_CONNECTED))
        return list(self.session.scalars(stmt))

    def _rib_rows(self, key: Key | None):
        """Kennung, Familie, Praefix und Protokoll der besten RIB-Routen eines Kontexts bzw. ohne
        Kontext der angeschlossenen Netze aller Kontexte."""
        stmt = select(
            TopoRoute.id, TopoRoute.family, TopoRoute.prefix_len, TopoRoute.lo, TopoRoute.protocol
        ).where(
            TopoRoute.tenant_id == self.tenant_id,
            TopoRoute.table == "rib",
            TopoRoute.best.is_(True),
        )
        if key is None:
            stmt = stmt.where(TopoRoute.protocol.in_(_CONNECTED))
        else:
            stmt = stmt.where(TopoRoute.source_system_id == key[0], TopoRoute.context == key[1])
        return self.session.execute(stmt.order_by(TopoRoute.id)).all()

    def _route_rows(self, ids: list[int]) -> list[TopoRoute]:
        """Routen zu Kennungen aus dem Index; einmal geladene bleiben im Graph."""
        missing = [i for i in ids if i not in self._loaded]
        for i in range(0, len(missing), 500):
            chunk = missing[i : i + 500]
            for r in self.session.scalars(select(TopoRoute).where(TopoRoute.id.in_(chunk))):
                self._loaded[r.id] = r
        return [self._loaded[i] for i in ids if i in self._loaded]

    def route(self, key: Key, ip, table: Table = 0) -> dict | None:
        """Wirksame Route des Knotens in der VRF `table`: laengstes Praefix, kleinste Distanz;
        Next-Hops aller Mitglieder (Border-Leafs) zusammen."""
        rows = [r for r in self._routes(ip, key) if table_of(r) == table]
        if not rows:
            return None
        length = max(r.prefix_len for r in rows)
        rows = [r for r in rows if r.prefix_len == length]
        distance = min((r.distance or 0) for r in rows)
        rows = [r for r in rows if (r.distance or 0) == distance]
        hops: list[dict] = []
        for r in rows:
            for h in r.next_hops or []:
                if h not in hops:
                    hops.append(h)
        first = rows[0]
        out = {
            "prefix": first.prefix,
            "prefix_len": first.prefix_len,
            "protocol": first.protocol,
            "distance": first.distance,
            "next_hops": hops,
            "members": sorted({r.node for r in rows}),
        }
        if is_net(ip):  # Netz (0.39.0): Teile davon, die genauer anders geroutet werden
            out |= self._inner(key, ip, table, hops)
        return out

    def _inner(self, key: Key, net, table: Table, hops: list[dict]) -> dict:
        """Genauere Routen innerhalb des Netzes mit anderem Next-Hop bzw. Interface: der Teil
        geht einen anderen Weg (`inner`, hoechstens INNER, `inner_total`)."""
        lo, hi = (ip_hex(v) for v in span(net))
        mine = {(h.get("ip"), h.get("interface")) for h in hops}
        rows = self.session.scalars(
            select(TopoRoute)
            .where(
                TopoRoute.source_system_id == key[0],
                TopoRoute.context == key[1],
                TopoRoute.family == net.version,
                TopoRoute.table == "rib",
                TopoRoute.best.is_(True),
                TopoRoute.prefix_len > net.prefixlen,
                TopoRoute.lo >= lo,
                TopoRoute.hi <= hi,
            )
            .order_by(TopoRoute.lo, TopoRoute.prefix_len)
        )
        other: dict[str, dict] = {}
        for r in rows:
            if table_of(r) != table:
                continue
            nhs = r.next_hops or []
            if {(h.get("ip"), h.get("interface")) for h in nhs} <= mine:
                continue
            via = ", ".join(h.get("ip") or h.get("interface") or "?" for h in nhs) or "?"
            other.setdefault(r.prefix, {"prefix": r.prefix, "protocol": r.protocol, "via": via})
        if not other:
            return {}
        return {"inner": list(other.values())[:INNER], "inner_total": len(other)}

    def connected_nodes(self, ip) -> list[tuple[Key, str | None, Table]]:
        """Knoten mit einem angeschlossenen Netz, das die IP enthaelt (laengstes je Knoten, bei
        gleicher Laenge die Standard-VRF): Knoten, Interface, VRF."""
        best: dict[Key, TopoRoute] = {}
        for r in self._routes(ip, connected=True):
            key = (r.source_system_id, r.context)
            if key not in self.nodes:
                continue
            rank = (r.prefix_len, table_of(r) == 0)
            if key not in best or rank > (best[key].prefix_len, table_of(best[key]) == 0):
                best[key] = r
        out = []
        for key, r in best.items():
            ifaces = [h.get("interface") for h in r.next_hops or [] if h.get("interface")]
            out.append((key, ifaces[0] if ifaces else None, table_of(r)))
        return out

    def backups(self, key: Key, ip, table: Table, route: dict) -> list[dict]:
        """Backup-Next-Hops fuer das Praefix der aktiven Route: BGP-Pfade und eingeschaltete
        statische Routen derselben VRF mit einem anderen Next-Hop (z. B. schwebende Route mit
        hoeherer Distanz, zweiter Provider)."""
        lo, hi = (ip_hex(v) for v in span(ip))
        active = {h.get("ip") for h in route.get("next_hops") or []}
        out: list[dict] = []
        for r in self.session.scalars(
            select(TopoRoute)
            .where(
                TopoRoute.source_system_id == key[0],
                TopoRoute.context == key[1],
                TopoRoute.family == ip.version,
                TopoRoute.table.in_(("bgp", "static")),
                TopoRoute.prefix == route["prefix"],
                TopoRoute.lo <= lo,
                TopoRoute.hi >= hi,
            )
            .order_by(TopoRoute.table, TopoRoute.distance, TopoRoute.id)
        ):
            if table_of(r) != table or (r.table == "static" and not r.best):
                continue  # andere VRF bzw. abgeschaltete statische Route
            for h in r.next_hops or []:
                nh = h.get("ip")
                if not nh or nh in active or any(o["next_hop"]["ip"] == nh for o in out):
                    continue
                attrs = r.attrs or {}
                if r.table == "bgp":
                    path = (
                        L(f" (AS-Pfad {attrs['as_path']})", f" (AS path {attrs['as_path']})")
                        if attrs.get("as_path")
                        else ""
                    )
                    reason = L(f"BGP-Backup{path}", f"BGP backup{path}")
                else:
                    reason = L("statische Backup-Route", "static backup route") + (
                        L(f" (Distanz {r.distance})", f" (distance {r.distance})")
                        if r.distance is not None
                        else ""
                    )
                hop = dict(h)
                if not hop.get("interface"):  # BGP-Pfade ohne Interface: das Netz des Next-Hops
                    hop["interface"] = self.interface_to(key, nh, table)
                out.append({"next_hop": hop, "prefix": r.prefix, "protocol": r.protocol,
                            "distance": r.distance, "reason": reason})  # fmt: skip
        return out

    def interface_to(self, key: Key, ip_text: str, table: Table) -> str | None:
        """Interface, ueber das der Knoten eine IP direkt erreicht (angeschlossenes Netz)."""
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            return None
        rows = [r for r in self._routes(ip, key, connected=True) if table_of(r) == table]
        rows.sort(key=lambda r: -r.prefix_len)
        for r in rows:
            for h in r.next_hops or []:
                if h.get("interface"):
                    return h["interface"]
        return None

    def table(self, key: Key, interface: str | None) -> Table:
        """VRF eines Interfaces - aus seinen angeschlossenen Netzen (fehlt = 0)."""
        if key not in self._tables:
            tables: dict[str, Table] = {}
            for r in self.session.scalars(
                select(TopoRoute).where(
                    TopoRoute.source_system_id == key[0],
                    TopoRoute.context == key[1],
                    TopoRoute.table == "rib",
                    TopoRoute.protocol.in_(_CONNECTED),
                )
            ):
                for h in r.next_hops or []:
                    if h.get("interface"):
                        tables.setdefault(h["interface"], table_of(r))
            self._tables[key] = tables
        return self._tables[key].get(interface or "", 0)

    def fabric(self, ip, key: Key | None = None) -> list[tuple[Key, str]]:
        """ACI-VRF-Knoten, deren EPG- bzw. BD-Subnetz die IP bzw. das ganze Netz enthaelt, mit
        dem Ort im Fabric (`Tenant|AP|EPG` bzw. BD); laengstes Subnetz je Knoten."""
        best: dict[Key, tuple[int, str]] = {}
        stmt = select(InvSubnet).where(
            InvSubnet.tenant_id == self.tenant_id, InvSubnet.context.in_(("bd", "epg"))
        )
        if key is not None:
            stmt = stmt.where(InvSubnet.source_system_id == key[0])
        for s in self.session.scalars(stmt):
            try:
                net = ipaddress.ip_network(s.prefix, strict=False)
            except ValueError:
                continue
            nkey = (s.source_system_id, aci_vrf(s.vrf, s.source_system_id, self.nodes))
            if not within(ip, net) or nkey not in self.nodes or key not in (None, nkey):
                continue
            where = (
                f"EPG {s.aci_tenant}|{s.ap}|{s.epg}"
                if s.epg and s.ap
                else f"BD {s.aci_tenant}|{s.bd or s.context_name}"
            )
            text = f"{where} ({s.prefix})"
            if nkey not in best or net.prefixlen > best[nkey][0]:
                best[nkey] = (net.prefixlen, text)
        return [(k, v[1]) for k, v in best.items()]

    def leaked(self, key: Key, ip) -> tuple[str, str, str, str] | None:
        """Ziel in einer anderen ACI-VRF desselben APIC, die es in diese VRF leakt (Shared
        Service, Route-Leaking; 0.44.0) -> (VRF dort, Ort `EPG Tenant|AP|EPG`, Grund, Art
        `shared` | `leak`)."""
        from app.store import aci_relevance, aci_shared  # spaet: aci_relevance -> paths
        from app.store.epgs import inventory_label

        if self._leaks is None:
            cls = aci_relevance.Classifier(self.session, self.tenant_id, lambda: self)
            self._leaks = aci_shared.Leaks(self.session, self.tenant_id, cls)
        found = self._leaks.into(key[0], key[1], ip)
        if found is None:
            return None
        epg, vrf, why, kind = found
        return vrf, f"EPG {inventory_label(epg).replace('/', '|')}", why, kind

    def exit_for(
        self, key: Key, interface: str | None, zone: str | None, ip: str | None
    ) -> TopoExit | None:
        """Anbindung fuer einen Ausgang: zuerst ueber die Next-Hop-IP, dann ueber Interface
        bzw. Zone - Anbindungen dieser Firewall (auch mehrerer, z. B. beider Session-Sync-
        Partner) vor solchen fuer alle."""
        ports = {p for p in (interface, zone) if p}
        own = [e for e in self.exits if key[0] in (e.source_system_ids or [])]
        ranked = own + [e for e in self.exits if not e.source_system_ids]
        if ip:
            for e in ranked:
                if ip in (e.next_hops or []):
                    return e
        for e in ranked:
            if ports & set(e.interfaces or []):
                return e
        return None

    def vdom_peer(self, key: Key, interface: str | None) -> tuple[Key, str] | None:
        """Anderes Ende eines Inter-VDOM-Links: (Knoten, Interface) im anderen VDOM."""
        if not interface:
            return None
        if key[0] not in self._vlinks:
            self._vlinks[key[0]] = _vdom_links(self.session, key[0])
        peer = self._vlinks[key[0]].get((key[1], interface))
        if peer is None:
            return None
        peer_key = (key[0], peer[0])
        return (peer_key, peer[1]) if peer_key in self.nodes else None

    def dnat(self, key: Key, ip, ingress: dict) -> dict | None:
        """VIP der FortiGate (DNAT) fuer das Ziel an diesem Knoten: externe IP bzw. Bereich und
        `extintf` (any bzw. Eingang) passen -> interne IP(s), beim Virtual Server alle Real
        Server. Ein Netz als Ziel (0.39.0) geht ohne DNAT weiter - eine VIP ist eine Adresse."""
        if is_net(ip):
            return None
        if key not in self._vips:
            self._vips[key] = list(
                self.session.scalars(
                    select(NatRule)
                    .where(
                        NatRule.source_system_id == key[0],
                        NatRule.nat_type == "dnat_vip",
                        or_(NatRule.vdom == key[1], NatRule.vdom.is_(None)),
                    )
                    .order_by(NatRule.name, NatRule.id)
                )
            )
        ports = {v for v in (ingress.get("interface"), ingress.get("zone")) if v}
        for n in self._vips[key]:
            to = _vip_target(n, ip)
            if not to:
                continue
            extintf = str((n.raw or {}).get("extintf") or "any")
            if extintf != "any" and ports and extintf not in ports:
                continue
            out = {
                "name": n.name,
                "id": n.id,
                "ext": n.ext_ip,
                "to": to,
                "type": n.vip_type,
                "ports": [n.ext_port, n.mapped_port] if n.portforward else None,
            }
            # Ziel der VIP ist ein Virtual Service des NSX ALB (0.79.0): im Hop nennen
            port = n.mapped_port if n.portforward else None
            try:
                lb = self.balancer(ipaddress.ip_address(to[0]), ("tcp", _first_port(port)))
            except ValueError:
                lb = None
            if lb is not None:
                out["balancer"] = lb["name"]
            return out
        return None

    def balancer(self, ip, service: tuple[str, int | None] | None) -> dict | None:
        """Virtual Service eines Load Balancers (NSX ALB, ADR 0027) mit dieser VIP: Port des
        Dienstes passt (ohne Dienst der erste) -> Server des Pools (aktive zuerst)."""
        if is_net(ip):
            return None
        if self._lbs is None:
            self._lbs = list(
                self.session.execute(
                    select(NatRule, SourceSystem.name)
                    .join(SourceSystem, SourceSystem.id == NatRule.source_system_id)
                    .where(
                        NatRule.tenant_id == self.tenant_id,
                        NatRule.nat_type == "dnat_vip",
                        SourceSystem.platform == Platform.AVI,
                    )
                    .order_by(NatRule.name, NatRule.id)
                ).tuples()
            )
        port = service[1] if service else None
        for n, source in self._lbs:
            ext = _bounds(n.ext_ip)
            if ext is None or ext[0] != ext[1] or ext[0] != ip:
                continue
            ports = _port_span(n.ext_port)
            if port is not None and ports is not None and not ports[0] <= port <= ports[1]:
                continue
            attrs = n.attrs or {}
            servers = [t for t in n.targets or [] if t.get("ip")]
            ready = [t for t in servers if t.get("status", "active") == "active"] or servers
            out = {
                "name": n.name,
                "id": n.id,
                "source_system_id": n.source_system_id,
                "source": source,
                "ext": n.ext_ip,
                "port": n.ext_port,
                "to": [t["ip"] for t in ready],
                "server_port": ready[0].get("port") if ready else None,
                "servers": len(servers),
                "pool": attrs.get("pool") or attrs.get("pool_group"),
                "tenant": attrs.get("tenant"),
                # Quelle danach: Service Engine (SNAT) bzw. der Client (preserve_client_ip)
                "snat": attrs.get("snat") or "service_engine",
                "down": attrs.get("oper_status") == "OPER_DOWN" or attrs.get("enabled") is False,
            }
            return {k: v for k, v in out.items() if v not in (None, "", False)}
        return None

    def policies(self, key: Key, src, dst, ingress: str | None) -> list[dict]:
        """Policy-Routen bzw. SD-WAN-Regeln des Knotens, die fuer den Verkehr gelten (koennen):
        `certain` = Quelle, Ziel und Eingangs-Interface passen sicher."""
        out = []
        for p in self.session.scalars(
            select(TopoPolicyRoute)
            .where(
                TopoPolicyRoute.source_system_id == key[0],
                TopoPolicyRoute.context == key[1],
                TopoPolicyRoute.enabled.is_(True),
            )
            .order_by(TopoPolicyRoute.kind, TopoPolicyRoute.seq)
        ):
            m = p.match or {}
            devices = m.get("input_device") or []
            checks = [
                matches(p.kind, m, "dst", dst),
                matches(p.kind, m, "src", src),
                True if not devices else (ingress in devices if ingress else None),
            ]
            if False in checks:
                continue
            out.append(
                {
                    "kind": p.kind,
                    "seq": p.seq,
                    "name": p.name,
                    "gateway": p.gateway,
                    "output_device": p.output_device or [],
                    "certain": all(c is True for c in checks),
                }
            )
        return out


def parse_key(value: str | None) -> Key | None:
    if not value or "|" not in value:
        return None
    sid, _, context = value.partition("|")
    return (int(sid), context) if sid.isdigit() else None


def trace(
    session: Session,
    tenant_id: int,
    dst_text: str,
    src_text: str | None = None,
    start: str | None = None,
    slack: int = 1,
    service: tuple[str, int | None] | None = None,
    graph: "Graph | None" = None,
    checks=None,
    backups: bool = True,
) -> dict:
    """Weg von der Quelle (bzw. dem Startpunkt) zum Ziel: je Pfad die Hops mit Ein- und
    Ausgang (Interface, Zone), Route bzw. Policy-Route und Next-Hop, dazu das Ende - die
    aktiven Wege und die Backup-Wege mit hoechstens `slack` Hops mehr; je FortiGate-Hop die
    passenden Policies (Dienst optional, z. B. `("tcp", 443)`).

    Viele Wege eines Mandanten (Verbindungen der Applikations-Doku, 0.45.0): `graph` und
    `checks` (`policy_check.Checks`) einmal laden; `backups=False` nur die aktiven Wege."""
    from app.store import policy_check  # Regeln je Hop - spaet importiert (Regelindex)

    dst = endpoint(dst_text)  # IP bzw. Netz (0.39.0: das Netz als Ganzes)
    src = endpoint(src_text) if src_text and src_text.strip() else None
    g = graph or Graph(session, tenant_id)
    notes: list[str] = []
    starts: list[tuple[Key, str | None, str]] = []
    chosen = parse_key(start)
    if start and chosen not in g.nodes:
        notes.append(
            L(
                "Startpunkt unbekannt – neu einlesen bzw. einen anderen wählen",
                "Start point unknown – collect again or choose another one",
            )
        )
    elif chosen is not None:
        # Eingang (und damit VRF) am Startpunkt: das angeschlossene Netz der Quelle, falls bekannt
        found = g.connected_nodes(src) if src else []
        iface = next((i for k, i, _ in found if k == chosen), None)
        starts = [(chosen, iface, L("gewählt", "selected"))]
    elif src is not None:
        how = L("Quelle angeschlossen", "Source connected")
        starts = [(k, iface, how) for k, iface, _ in g.connected_nodes(src)]
        if not starts:
            starts = [(k, None, L(f"Quelle im Fabric: {text}", f"Source in fabric: {text}"))
                      for k, text in g.fabric(src)]  # fmt: skip
        if not starts:
            whole = L(" (das ganze Netz)", " (the whole network)") if is_net(src) else ""
            notes.append(
                L(
                    f"Kein gelesenes Gerät hat {src}{whole} angeschlossen – Startpunkt wählen bzw. "
                    "das Gerät mit diesem Netz einlesen.",
                    f"No device read has {src}{whole} connected – choose a start point or collect "
                    "the device with this network.",
                )
            )
    paths: list[dict] = []
    backups_found: list[dict] = []

    def behind(hops, hop, key, iface, table, dev, cur) -> bool:
        """VIP eines Load Balancers hier angeschlossen (NSX ALB, ADR 0027): der Weg geht am
        selben Knoten vom Netz der VIP zum Server des Pools weiter (einmal je Weg)."""
        if any(h.get("lb") for h in hops):
            return False
        lb = g.balancer(cur, service)
        if lb is None:
            return False
        hop["lb"] = lb
        if not lb.get("to"):
            text = L(
                f"Load Balancer {lb['name']}: kein Server mit IP im Pool",
                f"Load Balancer {lb['name']}: no server with an IP in the pool",
            )
            _record(paths if dev is None else backups_found, hops, hop, "unknown", text, dev)
            return True
        name = f"Load Balancer {lb['name']} ({lb.get('source', 'NSX ALB')})"
        via = L(f"über {name}", f"via {name}")
        server = ipaddress.ip_address(lb["to"][0])
        (active if dev is None else backup).append(
            ([*hops, hop], key, iface, via, table, dev, server)
        )
        return True

    # (Hops, Knoten, Eingang, wie erreicht, VRF, Abweichung, Ziel nach NAT) - aktive zuerst
    active = [([], key, iface, via, g.table(key, iface), None, dst) for key, iface, via in starts]
    backup: list[tuple] = []
    cut = False
    while active or backup:
        if active and len(paths) >= MAX_PATHS or not active and len(backups_found) >= MAX_BACKUP:
            cut = True
            break
        hops, key, ingress, via, table, dev, cur = (active or backup).pop(0)
        out = paths if dev is None else backups_found

        node = g.nodes[key]
        hop = {**node.out(), "via": via, "ingress": _port(g, key, ingress)}
        if table != 0:
            hop["vrf"] = table
        leg = hops[_after_balancer(hops) :]  # nach einem Load Balancer: neuer Abschnitt
        if any((h["key"], h.get("vrf", 0)) == (node.key, table) for h in leg):
            text = L(f"Schleife: {node.label} ist schon auf dem Weg",
                     f"Loop: {node.label} is already on the path")  # fmt: skip
            _record(out, hops, hop, "loop", text, dev)
            continue
        if len(hops) >= MAX_HOPS:
            text = L(f"mehr als {MAX_HOPS} Hops – abgebrochen",
                     f"more than {MAX_HOPS} hops – aborted")  # fmt: skip
            _record(out, hops, hop, "max_hops", text, dev)
            continue
        if cur != dst:
            hop["dst"] = str(cur)  # Ziel nach NAT auf einem Hop davor
        if node.kind in ("firewall", "vdom"):
            nat = g.dnat(key, cur, hop["ingress"])
            if nat is not None:  # VIP: ab hier zur internen IP
                hop["nat"] = nat
                cur = ipaddress.ip_address(nat["to"][0])
        policies = g.policies(key, src, cur, ingress)
        hop["policy_routes"] = policies
        follow = next(
            (p for p in policies if p["certain"] and (p["gateway"] or p["output_device"])), None
        )
        route = g.route(key, cur, table)
        hop["route"] = route
        inside = g.fabric(cur, key) if node.kind == "vrf" else []
        # Route ohne Next-Hop-IP ueber einen Inter-VDOM-Link: weiter im VDOM am anderen Ende
        on_link = (
            route is not None
            and route["protocol"] not in _CONNECTED
            and any(
                not h.get("ip") and g.vdom_peer(key, h.get("interface")) for h in route["next_hops"]
            )
        )
        if follow is not None:
            hop["policy"] = follow
            devices = follow["output_device"] or [None]
            next_hops = [{"ip": follow["gateway"], "interface": devices[0]}]
        elif inside:
            if behind(hops, hop, key, None, table, dev, cur):
                continue
            text = L(f"Ziel im Fabric: {inside[0][1]}", f"Destination in fabric: {inside[0][1]}")
            _record(out, hops, hop, "fabric", text, dev)
            continue
        elif node.kind == "vrf" and (leak := g.leaked(key, cur)) is not None:
            # Ziel in einer anderen VRF, ins Fabric dieser VRF geleakt (0.44.0): im Fabric,
            # ohne L3Out - der Contract zwischen den VRFs entscheidet (aci_check)
            vrf, where, why, leak_kind = leak
            # `kind` (shared | leak): die Oberflaeche unterscheidet danach, nicht am Text
            hop["leak"] = {"vrf": vrf, "where": where, "text": why, "kind": leak_kind}
            text = L(f"Ziel im Fabric der VRF {vrf}: {where} – {why}",
                     f"Destination in the fabric of VRF {vrf}: {where} – {why}")  # fmt: skip
            _record(out, hops, hop, "fabric", text, dev)
            continue
        elif route is None:
            text = L("keine Route zum Ziel", "no route to the destination") + (
                f" (VRF {table})" if table != 0 else ""
            )
            if node.missing:
                text = L(
                    "keine Route bekannt – Routing-Tabelle nicht (ganz) lesbar",
                    "no route known – routing table not (fully) readable",
                )
            _record(out, hops, hop, "no_route", text, dev)
            continue
        elif not on_link and (
            route["protocol"] in _CONNECTED or not any(h.get("ip") for h in route["next_hops"])
        ):
            iface = next((h.get("interface") for h in route["next_hops"]), None)
            hop["egress"] = _port(g, key, iface)
            if behind(hops, hop, key, iface, table, dev, cur):
                continue
            where = f" ({iface})" if iface else ""
            text = L(f"Ziel angeschlossen an {node.label}{where}",
                     f"Destination connected to {node.label}{where}")  # fmt: skip
            _record(out, hops, hop, "connected", text, dev)
            continue
        else:
            next_hops = route["next_hops"]
        branches = [(nh, hop, dev) for nh in next_hops]
        # eine Abweichung je Weg: Backup-Next-Hops dieses Knotens (nicht bei Policy-Routen)
        if backups and dev is None and follow is None and route is not None:
            for b in g.backups(key, cur, table, route):
                info = {k: b[k] for k in ("prefix", "protocol", "distance", "reason")}
                deviation = {"hop": len(hops), "label": node.label, "reason": b["reason"]}
                branches.append((b["next_hop"], {**hop, "backup": info}, deviation))
        for nh, base, deviation in branches:
            queue = active if deviation is None else backup
            target = paths if deviation is None else backups_found
            ip = nh.get("ip")
            egress = {**_port(g, key, nh.get("interface")), "next_hop": ip, **g.resolve(ip)}
            branch = {**base, "egress": egress}

            nxt = _neighbors(g, key, ip)
            peer = None if nxt else g.vdom_peer(key, nh.get("interface"))
            if peer is not None:  # Inter-VDOM-Link ohne (gelesene) Next-Hop-IP
                link = f"{nh.get('interface')} ↔ {peer[1]}"
                nxt = [(peer[0], peer[1], L(f"VDOM-Link {link}", f"VDOM link {link}"))]
            for nkey, niface, how in nxt:
                queue.append(
                    ([*hops, branch], nkey, niface, how, g.table(nkey, niface), deviation, cur)
                )
            if nxt:
                continue
            ex = g.exit_for(key, nh.get("interface"), egress.get("zone"), ip)
            if ex is not None:
                egress["exit"] = {"id": ex.id, "name": ex.name, "kind": ex.kind}
                target_key = _exit_target(ex, cur)
                if target_key is not None and target_key in g.nodes:
                    via_exit = L(f"über {_exit_label(ex)} (manuelle Route)",
                                 f"via {_exit_label(ex)} (manual route)")  # fmt: skip
                    queue.append(([*hops, branch], target_key, None, via_exit, 0, deviation, cur))
                    continue
                text = _exit_text(ex, cur, ip, g.names(ip))
                edge = g.boundary().judge(key, ip, nh.get("interface"), egress.get("zone"), ex)
                _record(target, hops, branch, "exit", text, deviation, edge)
                continue
            # VIP eines Load Balancers hinter einem Router ohne gelesenes Geraet (0.79.0): weiter
            # vom Load Balancer zum Server, am selben Knoten
            if behind(hops, branch, key, nh.get("interface"), table, deviation, cur):
                continue
            known = g.names(ip) or ([egress["network"]] if egress.get("network") else [])
            name = f" ({', '.join(known)})" if known else ""
            if not ip:
                iface = nh.get("interface") or "?"
                kind, text = "unknown", L(f"weiter über {iface} ohne Next-Hop",
                                          f"onward via {iface} without next-hop")  # fmt: skip
            elif route is not None and route["prefix_len"] == 0 and follow is None:
                kind, text = (
                    "external",
                    L(
                        f"weiter nach extern über {ip}{name} – Default-Route",
                        f"onward to external via {ip}{name} – default route",
                    ),
                )
            else:
                kind, text = (
                    "unknown",
                    L(
                        f"Next-Hop {ip}{name} gehört keinem gelesenen Gerät",
                        f"Next-hop {ip}{name} does not belong to any device read",
                    ),
                )
            # Firmengrenze (ADR 0032): eBGP zu fremdem AS bzw. Default-Route Richtung Provider
            edge = g.boundary().judge(key, ip, nh.get("interface"), egress.get("zone"),
                                      default=kind == "external") if ip else None  # fmt: skip
            _record(target, hops, branch, kind, text, deviation, edge)
    if cut:
        notes.append(L(f"mehr als {MAX_PATHS} Wege – gekürzt",
                       f"more than {MAX_PATHS} paths – truncated"))  # fmt: skip
    primary = _unique(paths)
    for p in primary:
        p["kind"] = "active"
    shown, hidden = _backups(primary, _unique(backups_found), slack)
    if hidden:
        notes.append(
            L(
                f"{hidden} weitere Backup-Wege ausgeblendet (mehr als {slack} Hop länger bzw. "
                "ohne Ziel)",
                f"{hidden} more backup paths hidden (more than {slack} hop longer or without "
                "destination)",
            )
        )
    _mark_length(primary + shown)
    notes += _inner_notes(primary + shown, dst)
    policy_check.check_paths(session, tenant_id, primary + shown, src, dst, service, checks)
    return {
        "start": [g.nodes[k].out() | {"via": via} for k, _, via in starts],
        # Session-Sync-Paar bzw. Verbund als ein Hop (0.29.0)
        "paths": _merge_parallel(primary) + _merge_parallel(shown),
        "notes": notes,
        "slack": slack,
        "service": _service_text(service),
    }


def _inner_notes(paths: list[dict], dst) -> list[str]:
    """Netz als Ziel (0.39.0): je Knoten ein Hinweis, wenn Teile davon anders geroutet werden."""
    out: dict[str, str] = {}
    for p in paths:
        for h in p["hops"]:
            route = h.get("route") or {}
            if route.get("inner") and h["label"] not in out:
                via = L("über", "via")
                parts = ", ".join(f"{r['prefix']} {via} {r['via']}" for r in route["inner"])
                more = route["inner_total"] - len(route["inner"])
                parts += f" (+{more})" if more > 0 else ""
                out[h["label"]] = L(
                    f"Teile von {dst} werden an {h['label']} anders geroutet: {parts}",
                    f"Parts of {dst} are routed differently at {h['label']}: {parts}",
                )
    return list(out.values())


def _service_text(service: tuple[str, int | None] | None) -> str | None:
    if service is None:
        return None
    return f"{service[0]}/{service[1]}" if service[1] else service[0]


def _signature(p: dict) -> tuple:
    return tuple(
        (h["key"], *((h.get("egress") or {}).get(k) for k in ("next_hop", "interface")))
        for h in p["hops"]
    ) + (p["end"]["kind"],)


def _merge_parallel(paths: list[dict]) -> list[dict]:
    """Session-Sync-Paar als ein Hop (0.29.0, ADR 0019 Punkt 5): Wege, die sich nur in
    Firewalls desselben Verbunds unterscheiden (z. B. ECMP auf zwei Cluster mit Session-Sync),
    sind ein Weg, redundant ueber den Verbund (`parallel`). Der Verbund ist ein Hop mit den
    Firewalls darin (`parts`, je mit Eingang, Ausgang und Regeln); ebenso ein Knoten, den die
    Firewalls ueber verschiedene Interfaces erreichen. ECMP ueber andere Router bleibt je Weg."""
    order: dict[tuple, list[dict]] = {}
    for p in paths:
        order.setdefault(_parallel_signature(p), []).append(p)
    out = []
    for same in order.values():
        if len({tuple(h["key"] for h in p["hops"]) for p in same}) < 2:
            out += same
        else:
            out.append(_merged(same))
    return out


def _parallel_signature(p: dict) -> tuple:
    """Weg ohne die Firewalls eines Verbunds: je Hop Verbund (und Kontext) bzw. Knoten und
    der Next-Hop - in den Verbund hinein zaehlt der Verbund, nicht die Adresse des Mitglieds."""
    hops, parts = p["hops"], []
    for i, h in enumerate(hops):
        nxt = hops[i + 1] if i + 1 < len(hops) else None
        into = (nxt["group"], nxt["context"]) if nxt is not None and nxt.get("group") else None
        here = (h["group"], h["context"]) if h.get("group") else h["key"]
        parts.append((here, into or (h.get("egress") or {}).get("next_hop")))
    dev = p.get("deviation") or {}
    return (*parts, p["end"]["kind"], p.get("kind"), dev.get("hop"), dev.get("reason"))


def _port_key(h: dict) -> tuple:
    return (h["key"], *(json.dumps(h.get(k) or {}, sort_keys=True) for k in ("ingress", "egress")))


def _merged(same: list[dict]) -> dict:
    """Ein Weg aus gleichen Wegen ueber die Firewalls eines Verbunds: je Hop die
    verschiedenen Varianten in `parts`, Urteil der Regeln je Firewall zusammengefasst."""
    first = same[0]
    hops = []
    for i in range(len(first["hops"])):
        variants = list({_port_key(h): h for h in (p["hops"][i] for p in same)}.values())
        hop = variants[0]
        if len(variants) > 1:
            hop = {**hop, "parts": variants}
            if len({v["key"] for v in variants}) > 1:
                vdom = f" · VDOM {hop['context']}" if hop["kind"] == "vdom" else ""
                hop["label"] = L(f"Verbund {hop['group']}{vdom}",
                                 f"Parallel group {hop['group']}{vdom}")  # fmt: skip
        hops.append(hop)
    merged = {**first, "hops": hops}
    merged["parallel"] = sorted({h["group"] for h in hops if h.get("parts") and h.get("group")})
    ends = list(dict.fromkeys(p["end"]["text"] for p in same))
    if len(ends) > 1:
        merged["end"] = {**first["end"], "also": ends[1:]}
    if first.get("deviation"):
        idx = first["deviation"]["hop"]
        merged["deviation"] = {**first["deviation"], "label": hops[idx]["label"]}
    policies = [p.get("policy") for p in same]
    if any(policies):
        verdicts = {x["verdict"] if x else None for x in policies}
        if len(verdicts) == 1:  # gleiches Urteil: ueber alle Firewalls des Verbunds
            from app.store import policy_check  # spaet importiert wie in trace()

            hops_rules = [(h["label"], h["rules"]["verdict"], h["kind"]) for p in same
                          for h in p["hops"] if h.get("rules")]  # fmt: skip
            merged["policy"] = policy_check._overall(list(dict.fromkeys(hops_rules)))
        else:
            who = [", ".join(h["label"] for h in p["hops"] if h.get("group")) for p in same]
            text = "; ".join(
                f"{w}: {x['text'] if x else '–'}" for w, x in zip(who, policies, strict=True)
            )
            text = L(f"im Verbund verschieden – {text}",
                     f"different within the parallel group – {text}")  # fmt: skip
            merged["policy"] = {"verdict": "depends", "text": text}
    return merged


def _mark_length(paths: list[dict]) -> None:
    """Laenge im Vergleich (0.20.0): `extra` = Hops mehr als der kuerzeste Weg, der ein Ziel
    erreicht (0 = gleich lang); bei Wegen ohne Ziel `None`."""
    reached = [len(p["hops"]) for p in paths if p["end"]["kind"] in REACHED]
    shortest = min(reached) if reached else None
    for p in paths:
        ok = shortest is not None and p["end"]["kind"] in REACHED
        p["extra"] = len(p["hops"]) - shortest if ok else None


def _backups(primary: list[dict], found: list[dict], slack: int) -> tuple[list[dict], int]:
    """Backup-Wege, die ein Ziel erreichen und hoechstens `slack` Hops laenger sind als der
    kuerzeste aktive Weg; ohne doppelte zu aktiven Wegen. Rueckgabe: (gezeigt, ausgeblendet)."""
    reached = [len(p["hops"]) for p in primary if p["end"]["kind"] in REACHED]
    shortest = min(reached or [len(p["hops"]) for p in primary] or [0])
    known = {_signature(p) for p in primary}
    shown, hidden = [], 0
    for p in found:
        if _signature(p) in known:
            continue
        if p["end"]["kind"] not in REACHED or (primary and len(p["hops"]) > shortest + slack):
            hidden += 1
            continue
        p["kind"] = "backup"
        shown.append(p)
    shown.sort(key=lambda p: len(p["hops"]))
    return shown, hidden


def _bounds(text: str | None):
    """(erste, letzte) IP eines Werts: IP, Bereich a-b oder Netz; sonst None."""
    value = (text or "").strip()
    try:
        if "-" in value:
            a, _, b = value.partition("-")
            lo, hi = ipaddress.ip_address(a.strip()), ipaddress.ip_address(b.strip())
        elif "/" in value:
            net = ipaddress.ip_network(value, strict=False)
            lo, hi = net.network_address, net.broadcast_address
        else:
            lo = hi = ipaddress.ip_address(value)
    except ValueError:
        return None
    return (lo, hi) if lo.version == hi.version else None


def _after_balancer(hops: list[dict]) -> int:
    """Index nach dem letzten Hop mit Load Balancer (0 ohne): dort beginnt ein neuer Abschnitt."""
    return max((i + 1 for i, h in enumerate(hops) if h.get("lb")), default=0)


def _first_port(text: str | None) -> int | None:
    span = _port_span(text)
    return span[0] if span else None


def _port_span(text: str | None) -> tuple[int, int] | None:
    """`443` bzw. `8000-8010` -> (von, bis); leer bzw. unlesbar = jeder Port."""
    lo, _, hi = (text or "").partition("-")
    try:
        return (int(lo), int(hi or lo)) if lo.strip() else None
    except ValueError:
        return None


def _vip_target(n: NatRule, ip) -> list[str]:
    """Interne IP(s) einer VIP fuer das Ziel: Bereich auf Bereich mit demselben Abstand, sonst
    die zugeordnete IP bzw. alle Real Server; leer, wenn das Ziel nicht die externe IP ist."""
    ext = _bounds(n.ext_ip)
    if ext is None or ext[0].version != ip.version or not ext[0] <= ip <= ext[1]:
        return []
    mapped = _bounds(n.mapped_ip)
    if mapped is not None and mapped[0] != mapped[1] and not n.targets:
        moved = ipaddress.ip_address(int(mapped[0]) + int(ip) - int(ext[0]))
        if moved <= mapped[1]:
            return [str(moved)]
    return [str(_bounds(t)[0]) if _bounds(t) else t for t in nat_store.internal_ips(n)]


def _exit_target(ex: TopoExit, dst) -> Key | None:
    """Manuelle Route der Anbindung mit dem laengsten passenden Praefix -> Knoten."""
    best: tuple[int, Key] | None = None
    for r in ex.routes or []:
        try:
            net = ipaddress.ip_network(r.get("prefix") or "", strict=False)
        except ValueError:
            continue
        key = parse_key(r.get("target"))
        if key is not None and within(dst, net) and (best is None or net.prefixlen > best[0]):
            best = (net.prefixlen, key)
    return best[1] if best else None


def _exit_label(ex: TopoExit) -> str:
    """„MPLS Kunde A“ - die Art nur, wenn der Name sie nicht schon nennt."""
    label = exit_kind(ex.kind, L("Anbindung", "Exit"))
    return ex.name if ex.name.lower().startswith(label.lower()) else f"{label} {ex.name}"


def _exit_text(ex: TopoExit, dst, ip: str | None, names: list[str]) -> str:
    text = L(f"weiter über {_exit_label(ex)}", f"onward via {_exit_label(ex)}")
    if names:
        text += f" ({', '.join(names)})"
    routers = ex.next_hops or []
    if ip in routers and len(routers) > 1:
        n, total = routers.index(ip) + 1, len(routers)
        text += L(f" – Router {n} von {total}", f" – router {n} of {total}")
    nets = []
    for p in ex.prefixes or []:
        try:
            nets.append(ipaddress.ip_network(p, strict=False))
        except ValueError:
            continue
    if nets and not any(within(dst, n) for n in nets):
        text += L(" – Ziel liegt nicht in den Netzen der Anbindung",
                  " – destination is not in the networks of the exit")  # fmt: skip
    return text


def _port(g: Graph, key: Key, interface: str | None) -> dict:
    out: dict = {"interface": interface}
    zone = g.zone(key[0], interface)
    if zone:
        out["zone"] = zone
    return out


def _neighbors(g: Graph, key: Key, ip: str | None) -> list[tuple[Key, str | None, str]]:
    """Knoten hinter dem Next-Hop: dem die Adresse gehoert (Interface, L3Out-SVI). Ein
    gemeinsames Transfernetz allein reicht nicht - dort haengt meist ein Aussenrouter."""
    exact: list[tuple[Key, str | None, str]] = []
    how = L("Adresse des Nachbarn", "Neighbor address")
    for nkey, iface, _zone in g.owners().get(ip or "", []):
        if nkey != key and all(nkey != e[0] for e in exact):
            exact.append((nkey, iface, how))
    return exact


def _end(hops: list, hop: dict, kind: str, text: str) -> dict:
    return {"hops": [*hops, hop], "end": {"kind": kind, "text": text}}


def _record(out: list, hops: list, hop: dict, kind: str, text: str, deviation,
            boundary: dict | None = None) -> None:  # fmt: skip
    """Weg mit seinem Ende ablegen; Backup-Wege mit der Abweichung (Hop, Grund); an der
    Firmengrenze (ADR 0032) der Grund am Weg und am letzten Hop."""
    if boundary:
        hop["boundary"] = boundary["reason"]
    path = {**_end(hops, hop, kind, text), **({"deviation": deviation} if deviation else {})}
    if boundary:
        path["boundary"] = boundary
    out.append(path)


def _unique(paths: list[dict]) -> list[dict]:
    """Gleiche Wege (z. B. ueber zwei Next-Hops zum selben Nachbarn) nur einmal."""
    seen, out = set(), []
    for p in paths:
        sig = _signature(p)
        if sig not in seen:
            seen.add(sig)
            out.append(p)
    return out


def device_names(session: Session, source_ids: set[int]) -> dict[tuple[int, str], str]:
    """ACI: Knoten-ID -> Leaf-Name (Inventar), fuer L3Out-Pfade."""
    if not source_ids:
        return {}
    return {
        (d.source_system_id, d.node_id): d.name
        for d in session.scalars(
            select(InvDevice).where(
                InvDevice.source_system_id.in_(source_ids), InvDevice.node_id.is_not(None)
            )
        )
    }


def _vdom_links(session: Session, sid: int) -> dict[tuple[str, str], tuple[str, str]]:
    """(VDOM, Interface) -> (VDOM, Interface) am anderen Ende der Inter-VDOM-Links einer
    FortiGate: `<name>0` <-> `<name>1` (vdom-link bzw. NPU-vlink, nur virtuelle bzw.
    vlink-Interfaces, Enden in verschiedenen VDOMs), VLAN-Subinterfaces ueber Parent und VLAN-ID
    (NPU-vlinks: beide Enden oft in root, die VLANs darauf in den VDOMs)."""
    rows = list(
        session.execute(
            select(
                InvInterface.name,
                InvInterface.vdom,
                InvInterface.kind,
                InvInterface.parent,
                InvInterface.tagged_vlans,
            ).where(InvInterface.source_system_id == sid, InvInterface.vdom.is_not(None))
        )
    )
    by_name = {r.name: r for r in rows}

    def link_end(r) -> bool:
        return r.name[-1:] in ("0", "1") and (
            r.kind in ("virtual", "vdom-link") or "vlink" in r.name.lower()
        )

    pairs: dict[str, str] = {}  # beide Enden; NPU-vlinks liegen oft beide im VDOM root
    for r in rows:
        other = by_name.get(r.name[:-1] + "1") if r.name.endswith("0") else None
        if other is not None and link_end(r) and link_end(other):
            pairs[r.name], pairs[other.name] = other.name, r.name
    out: dict[tuple[str, str], tuple[str, str]] = {}
    for a, b in pairs.items():
        if by_name[a].vdom != by_name[b].vdom:
            out[(by_name[a].vdom, a)] = (by_name[b].vdom, b)
    subs: dict[tuple[str, int], list] = defaultdict(list)
    for r in rows:
        if r.kind == "vlan" and r.parent in pairs and r.tagged_vlans:
            subs[(r.parent, int(r.tagged_vlans[0]))].append(r)
    for (parent, vid), mine in subs.items():
        for a in mine:
            for b in subs.get((pairs[parent], vid), []):
                if a.vdom != b.vdom:
                    out[(a.vdom, a.name)] = (b.vdom, b.name)
    return out
