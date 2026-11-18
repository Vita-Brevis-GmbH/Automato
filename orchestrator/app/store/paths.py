"""Weg einer Verbindung durchs Netz (ADR 0019 Stufe 3, erste Fassung 0.17.0).

Knoten sind Routing-Instanzen: ein FortiGate-VDOM (bzw. die Firewall, wenn sie nur `root`
hat) oder der Rand einer ACI-VRF - alle Border-Leafs der VRF zusammen, vier Anbindungen sind
ein Knoten und nicht vier Wege. Seit ADR 0032 auch Router und L3-Switches (Switches & Router,
Art `l3`): je Geraet und VRF ein Knoten; ihre Adressen kommen aus den `local`-Routen.

- **Start:** der gewaehlte Startpunkt bzw. der Knoten, an dem die Quelle angeschlossen ist
  (angeschlossenes Netz in der RIB, sonst EPG-/BD-Subnetz einer gelesenen ACI-VRF); eine
  Anbindung als Quelle beginnt an ihren Firewalls mit ihrem Eingang (`entries`,
  `store/exit_ends.py`).
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
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, or_, select
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
    TopoRoute,
)
from app.store import forwarding, path_service, path_stages, references
from app.store import vpn as vpn_store
from app.store.path_stages import bounds as _bounds
from app.store.path_stages import port_span as _port_span
from app.store.topology import (
    MGMT,
    Table,
    aci_vrf,
    endpoint,
    ip_hex,
    is_net,
    is_private,
    span,
    table_of,
    within,
)

MAX_HOPS = 12
MAX_PATHS = 16  # aktive Wege
MAX_BACKUP = 16  # Backup-Wege (vor dem Filter nach Laenge)
INNER = 5  # genauere Routen innerhalb eines Netzes je Hop (Anzeige)
# Ende = Ziel erreicht bzw. nach aussen; `vpn` = an der Gegenstelle eines IPsec-Tunnels ohne
# gelesenes Geraet (1.19.0, ADR 0063 - in den Verbindungen gezaehlt)
# `redirect` (ADR 0070) = der Load Balancer beantwortet den Fluss selbst (Umleitung HTTP → HTTPS)
REACHED = ("connected", "fabric", "exit", "external", "vpn", "redirect")
# Ende ohne Ziel (ADR 0070): `dropped` = das Geraet verwirft (Null-Route, Reverse Path, IPsec ohne
# Selektor bzw. Tunnel down); `unchecked` = nicht geprueft (privates Ziel ueber die Default-Route
# an ein nicht gelesenes Geraet, Reverse Path ohne gelesene Einstellungen) - gezaehlt, ADR 0048
NOT_CHECKED = ("unchecked",)
MAX_SERVERS = 20  # Ziel-Server einer VIP bzw. eines Pools im Hop (0.114.1)
_CONNECTED = ("connected", "local")
_OBJECT_ROUTE = ("dstaddr",)  # FortiGate: statische Route auf ein Adressobjekt (0.112.0)
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


@dataclass(frozen=True, slots=True)
class _Route:
    """Route als reine Daten (0.141.0): ohne Bindung an eine Sitzung - so teilen alle Graphen
    eines Datenstands den Index und die geladenen Routen (`path_store`)."""

    id: int
    source_system_id: int
    node: str
    context: str
    family: int
    prefix: str
    prefix_len: int
    lo: str
    hi: str
    table: str
    protocol: str | None
    best: bool
    distance: int | None
    metric: int | None
    next_hops: list | None
    attrs: dict | None

    @classmethod
    def of(cls, r: TopoRoute) -> "_Route":
        return cls(r.id, r.source_system_id, r.node, r.context, r.family, r.prefix, r.prefix_len,
                   r.lo, r.hi, r.table, r.protocol, r.best, r.distance, r.metric, r.next_hops,
                   r.attrs)  # fmt: skip


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
        # gelesene Einstellungen je Knoten (Central NAT, Reverse Path) - `path_stages.settings`
        self.settings: dict[Key, dict] = {}
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
            if c.settings:  # FortiGate: `system settings` des VDOMs (ADR 0070)
                self.settings.setdefault(key, {}).update(c.settings)
        self.refs = references.context(session, tenant_id)
        self._owners: dict[str, list[tuple[Key, str | None, str | None]]] | None = None
        self._zones: dict[tuple[int, str], str] = {}
        self._tables: dict[Key, dict[str, Table]] = {}
        # 0.132.1 (viele Wege einer Applikation): Subnetze des Fabrics und Policy-Routen je
        # Knoten einmal lesen statt je Hop
        self._fabric_rows: list[tuple] | None = None
        self._policy_routes: dict[Key, list] = {}
        # 0.133.1 (Anwender: „Das Berechnen hat immer noch über eine Stunde für eine riesige
        # App“): BGP-/statische Routen je Praefix und genauere Routen in einem Netz einmal je
        # Knoten - je Verbindung und Hop eine Abfrage las bei grossen BGP-Tabellen alle Routen
        self._backup_rows: dict[tuple, list] = {}
        self._inner_rows: dict[tuple, list] = {}
        self._vips: dict[Key, list[NatRule]] = {}
        self._lbs: list[tuple[NatRule, str]] | None = None  # NSX ALB (ADR 0027), bei Bedarf
        self._vlinks: dict[int, dict[tuple[str, str], tuple[str, str]]] = {}
        self._leaks = None  # aci_shared.Leaks, erst bei Bedarf
        self._ribs: dict[Key | None, _Rib] = {}  # beste RIB-Routen je Kontext (0.74.1)
        self._boundary = None  # boundary.Boundary, erst bei Bedarf (ADR 0032)
        self._bgp = None  # bgp_filters.Filters, erst bei einem BGP-Backup (0.114.0)
        self._vpn = None  # vpn.Index, IPsec-Tunnel (1.19.0, ADR 0063), erst bei Bedarf
        self._own_ips: dict[Key, set[str]] = {}
        # virtuelle Gateway-Adressen (HSRP/VRRP), die ein Router nur als Standby haelt (ADR 0070)
        self._standby: set[tuple[Key, str]] = set()
        self._rib_asked: Counter = Counter()  # Abfragen je Kontext bis zum Index
        self._loaded: dict[int, TopoRoute] = {}  # Routen aus dem Index, je Kennung
        # Entscheidung je Menge gleicher Routen (ADR 0070, `forwarding.decide`) - je Datenstand
        self._decisions: dict[tuple, dict] = {}
        self._shared = self._share(session, tenant_id)
        self.exits = list(
            session.scalars(
                select(TopoExit).where(TopoExit.tenant_id == tenant_id).order_by(TopoExit.id)
            )
        )

    def _share(self, session: Session, tenant_id: int) -> dict | None:
        """0.141.0 (Messung: 21 000 Abfragen der Routen fuer 50 Applikationen - jeder Graph lud
        Index und Routen neu): im Wege-Speicher des Mandanten teilen alle Graphen den Index der
        RIB, die geladenen Routen (als reine Daten), die VRF je Interface, die genaueren Routen
        und Backup-Routen je Praefix und die Subnetze des Fabrics. Ein Lauf legt die Routen neu an
        (neue Kennungen) - dann beginnt der geteilte Teil neu (hoechste Kennung der Routen)."""
        from app.store import path_store  # spaet: path_store -> doc_snapshots -> ...

        store = path_store.current(tenant_id)
        if store is None:
            return None
        newest = session.scalar(select(func.max(TopoRoute.id)).where(
            TopoRoute.tenant_id == tenant_id))  # fmt: skip
        with store.lock:
            shared = store.routing
            if shared.get("newest", -1) != newest:
                shared.clear()
                shared["newest"] = newest
            self._ribs = shared.setdefault("ribs", {})
            self._rib_asked = shared.setdefault("asked", Counter())
            self._loaded = shared.setdefault("loaded", {})
            self._tables = shared.setdefault("tables", {})
            self._inner_rows = shared.setdefault("inner", {})
            self._backup_rows = shared.setdefault("backup", {})
            self._decisions = shared.setdefault("decisions", {})
        return shared

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
            ip = str(prefix).split("/")[0]
            if (attrs or {}).get("virtual") and (attrs or {}).get("state") == "standby":
                self._standby.add((key, ip))  # IOS `show standby`: nur der aktive nimmt an
            out.append((key, ip, iface, bool((attrs or {}).get("virtual"))))
        return out

    def vpn(self):
        """IPsec-Tunnel des Mandanten (1.19.0, ADR 0063, `store/vpn.py`) - einmal je Graph."""
        if self._vpn is None:
            from app.store import vpn  # spaet: nur mit Firewalls auf dem Weg

            self._vpn = vpn.Index(self.session, self.tenant_id)
        return self._vpn

    def tunnel(self, key: Key, interface: str | None):
        """IPsec-Tunnel hinter einem Interface einer FortiGate: (Tunnel, Dial-up-Instanz)."""
        node = self.nodes.get(key)
        if not interface or node is None or node.kind not in ("firewall", "vdom"):
            return None
        return self.vpn().find(key, interface)

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
            rib = self._index(key)
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

    def standby_routes(self, ip, key: Key) -> list[TopoRoute]:
        """Routen der RIB, die die IP enthalten und **nicht** weiterleiten (`best=False`):
        FortiOS haelt Routen gleicher Distanz mit hoeherer Prioritaet in der Routing-Tabelle -
        gewaehlt wird ueber sie nicht, aktiv sind sie aber (Reverse Path „feasible“,
        Gegenpruefung P-1). Nur fuer den Reverse Path gefragt, dort je Quelle gemerkt."""
        lo, hi = (ip_hex(v) for v in span(ip))
        return list(
            self.session.scalars(
                select(TopoRoute).where(
                    TopoRoute.tenant_id == self.tenant_id,
                    TopoRoute.source_system_id == key[0],
                    TopoRoute.context == key[1],
                    TopoRoute.family == ip.version,
                    TopoRoute.table == "rib",
                    TopoRoute.best.is_(False),
                    TopoRoute.lo <= lo,
                    TopoRoute.hi >= hi,
                )
            )
        )

    def _index(self, key: Key | None) -> "_Rib | None":
        """Index der RIB eines Kontexts (bzw. der angeschlossenen Netze aller Kontexte) - erst ab
        einigen Abfragen lohnt das Laden; davor None (Abfrage der Datenbank)."""
        rib = self._ribs.get(key)
        if rib is None:
            self._rib_asked[key] += 1
            if self._rib_asked[key] > _RIB_AFTER:
                rib = self._ribs[key] = _Rib(self._rib_rows(key))
        return rib

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
        plain = self._shared is not None  # geteilt: reine Daten statt Zeilen der Sitzung
        for i in range(0, len(missing), 500):
            chunk = missing[i : i + 500]
            for r in self.session.scalars(select(TopoRoute).where(TopoRoute.id.in_(chunk))):
                self._loaded[r.id] = _Route.of(r) if plain else r
        return [self._loaded[i] for i in ids if i in self._loaded]

    def route(self, key: Key, ip, table: Table = 0) -> dict | None:
        """Wirksame Route des Knotens in der VRF `table` (`forwarding.decide`, ADR 0070):
        laengstes Praefix, kleinste Distanz, kleinste Prioritaet; Next-Hops aller Mitglieder
        (Border-Leafs) zusammen. Dazu `action` (connected | forward | link | drop) und `branches`
        (Next-Hops nach rekursiver Aufloesung)."""
        out = forwarding.decide(self, key, ip, table)
        if out is not None and is_net(ip):  # Netz (0.39.0): Teile, die genauer anders gehen
            out |= self._inner(key, ip, table, out["next_hops"])
        return out

    def blackholes(self, key: Key) -> set[tuple]:
        """(Praefix, VRF, Distanz) der eingeschalteten statischen Blackhole-Routen des Knotens
        (FortiGate-Konfiguration, Tabelle `static`) - einmal je Datenstand."""
        cache = self._decisions.setdefault(("blackholes",), {})
        if key not in cache:
            found = set()
            for r in self.session.scalars(
                select(TopoRoute).where(
                    TopoRoute.source_system_id == key[0],
                    TopoRoute.context == key[1],
                    TopoRoute.table == "static",
                    TopoRoute.best.is_(True),
                )
            ):
                if (r.attrs or {}).get("blackhole"):
                    # FortiOS: Distanz einer statischen Route ohne Angabe = 10
                    found.add((r.prefix, table_of(r), r.distance if r.distance is not None else 10))
            cache[key] = found
        return cache[key]

    def _inner(self, key: Key, net, table: Table, hops: list[dict]) -> dict:
        """Genauere Routen innerhalb des Netzes mit anderem Next-Hop bzw. Interface: der Teil
        geht einen anderen Weg (`inner`, hoechstens INNER, `inner_total`)."""
        mine = {(h.get("ip"), h.get("interface")) for h in hops}
        cache = (key, str(net))
        if cache not in self._inner_rows:
            lo, hi = (ip_hex(v) for v in span(net))
            self._inner_rows[cache] = [
                (r.prefix, r.protocol, table_of(r), r.next_hops)
                for r in self.session.execute(
                    select(TopoRoute.prefix, TopoRoute.protocol, TopoRoute.attrs,
                           TopoRoute.next_hops)
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
            ]  # fmt: skip
        rows = self._inner_rows[cache]
        other: dict[str, dict] = {}
        for prefix, protocol, rtable, next_hops in rows:
            if rtable != table:
                continue
            nhs = next_hops or []
            if {(h.get("ip"), h.get("interface")) for h in nhs} <= mine:
                continue
            via = ", ".join(h.get("ip") or h.get("interface") or "?" for h in nhs) or "?"
            other.setdefault(prefix, {"prefix": prefix, "protocol": protocol, "via": via})
        if not other:
            return {}
        return {"inner": list(other.values())[:INNER], "inner_total": len(other)}

    def rpf_interface(self, key: Key, ip) -> str | None:
        """Interface, ueber das der Knoten die Quelle erreicht (Reverse Path, 0.112.0) - der
        Eingang ihres Verkehrs; mehrere Interfaces (ECMP) bzw. keine Route: None."""
        route = self.route(key, ip)
        ifaces = {h.get("interface") for h in (route or {}).get("next_hops") or []}
        return ifaces.pop() if len(ifaces) == 1 and None not in ifaces else None

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

    def connected_parts(self, net) -> tuple[list[tuple[Key, str | None, str]], bool]:
        """Knoten, an denen **Teile** des Netzes angeschlossen sind (Gegenpruefung P-13: die
        Quelle einer breiten Regel, z. B. 10.10.0.0/16, ist groesser als das angeschlossene
        /24): je Knoten und Interface ein Eintrag (Knoten, Interface, Praefixe als Text) - der
        Eingang entscheidet ueber die Policies. Dazu die ACI-VRFs mit EPG-/BD-Subnetzen im Netz.
        -> (Eintraege, ob die Teile das ganze Netz decken)."""
        cache = self.__dict__.setdefault("_parts", {})
        if net in cache:
            return cache[net]
        lo, hi = (ip_hex(v) for v in span(net))
        found: dict[tuple[Key, str | None], list] = {}
        rows = self.session.execute(
            select(TopoRoute.source_system_id, TopoRoute.context, TopoRoute.prefix,
                   TopoRoute.next_hops)
            .where(
                TopoRoute.tenant_id == self.tenant_id,
                TopoRoute.family == net.version,
                TopoRoute.table == "rib",
                TopoRoute.best.is_(True),
                TopoRoute.protocol == "connected",
                TopoRoute.lo >= lo,
                TopoRoute.hi <= hi,
            )
            .order_by(TopoRoute.source_system_id, TopoRoute.context, TopoRoute.lo, TopoRoute.id)
        )  # fmt: skip
        for sid, context, prefix, hops in rows:
            key = (sid, context)
            if key not in self.nodes:
                continue
            iface = next((h.get("interface") for h in hops or [] if h.get("interface")), None)
            if self.table(key, iface) == MGMT:
                continue  # Management-Netz: kein Verkehr der Quelle
            found.setdefault((key, iface), []).append(prefix)
        self.fabric(net)  # laedt die Subnetze des Fabrics
        for sub, nkey, _text in self._fabric_rows or []:
            if sub.version == net.version and within(sub, net):
                found.setdefault((nkey, None), []).append(str(sub))
        nets = []
        for prefixes in found.values():
            for prefix in prefixes:
                try:
                    nets.append(ipaddress.ip_network(prefix, strict=False))
                except ValueError:
                    continue
        covered = sum(n.num_addresses for n in ipaddress.collapse_addresses(nets))
        out = [(key, iface, ", ".join(dict.fromkeys(prefixes)))
               for (key, iface), prefixes in found.items()]  # fmt: skip
        cache[net] = (out, covered >= net.num_addresses)
        return cache[net]

    def backups(self, key: Key, ip, table: Table, route: dict) -> list[dict]:
        """Backup-Next-Hops fuer das Praefix der aktiven Route: BGP-Pfade und eingeschaltete
        statische Routen derselben VRF mit einem anderen Next-Hop (z. B. schwebende Route mit
        hoeherer Distanz, zweiter Provider).

        Nur, was das Geraet fuer das Ziel wirklich gelernt hat (0.112.0, Anwender: „Der Pfad ist
        also nur eine Hochrechnung“): kein Backup ueber die Default-Route fuer ein privates Ziel
        (das Geraet kennt das Netz dort nicht, die Firewall dahinter schon), kein BGP-Bestpfad
        (anderer Next-Hop nur durch Rekursion) und keine statische Route auf ein Adressobjekt
        bzw. eine SD-WAN-Zone (als 0.0.0.0/0 gespeichert, das wirkliche Ziel ist unbekannt)."""
        if route.get("prefix_len") == 0 and is_private(ip):
            return []
        active = {h.get("ip") for h in route.get("next_hops") or []}
        out: list[dict] = []
        cache = (key, ip.version, route["prefix"])  # gleiches Praefix = gleiche Zeilen
        if cache not in self._backup_rows:
            rows = list(self.session.scalars(
                select(TopoRoute)
                .where(
                    TopoRoute.source_system_id == key[0],
                    TopoRoute.context == key[1],
                    TopoRoute.family == ip.version,
                    TopoRoute.table.in_(("bgp", "static")),
                    TopoRoute.prefix == route["prefix"],
                )
                .order_by(TopoRoute.table, TopoRoute.distance, TopoRoute.id)
            ))  # fmt: skip
            shared = self._shared is not None
            self._backup_rows[cache] = [_Route.of(r) for r in rows] if shared else rows
        for r in self._backup_rows[cache]:
            if table_of(r) != table or (r.table == "static" and not r.best):
                continue  # andere VRF bzw. abgeschaltete statische Route
            if r.table == "bgp" and r.best:
                continue  # der aktive Pfad selbst (Next-Hop nur rekursiv anders)
            if r.table == "static" and any((r.attrs or {}).get(k) for k in _OBJECT_ROUTE):
                continue  # Ziel ist ein Objekt bzw. eine SD-WAN-Zone, nicht das Praefix
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
                found = {"next_hop": hop, "prefix": r.prefix, "protocol": r.protocol,
                         "distance": r.distance, "reason": reason}  # fmt: skip
                if r.table == "bgp":  # Filter sperrt den Pfad (0.114.0, ADR 0041 Stufe 2)
                    peer = str(attrs.get("learned_from") or nh).split("%")[0].split("/")[0]
                    blocked = self.bgp_blocked(key, r.node, peer, r.prefix)
                    if blocked:
                        found["blocked"] = blocked
                out.append(found)
        return out

    def bgp_blocked(self, key: Key, node: str | None, peer: str, prefix: str) -> str | None:
        """Wuerde das Geraet das Netz `prefix` vom BGP-Nachbarn `peer` je uebernehmen (0.114.0,
        Anwender zu 0.110: „die Firewalls davor wuerden das Netzwerk niemals lernen, da sie in
        den Filtern gesperrt wurden“)? Sein Eingangsfilter bzw. - ist der Nachbar eine gelesene
        FortiGate - deren Ausgangsfilter zu diesem Geraet. -> Grund bzw. None."""
        from app.store import bgp_filters  # spaet: nur bei BGP-Backups

        if self._bgp is None:
            self._bgp = bgp_filters.Filters(self.session, self.tenant_id)
        if not self._bgp.configs or key not in self.nodes:
            return None
        if key not in self._own_ips:
            self._own_ips[key] = {ip for ip, uses in self.owners().items()
                                  if any(k == key for k, *_ in uses)}  # fmt: skip
        own = self._own_ips[key]
        neighbors = [
            ((k[0], member, k[1]), ip)
            for k, *_ in self.owners().get(peer, [])
            if k in self.nodes and k != key
            for member in self.nodes[k].members
            for ip in own
        ]
        for member in [node] if node else self.nodes[key].members:
            blocked = self._bgp.blocked((key[0], member, key[1]), peer, prefix, neighbors)
            if blocked:
                return blocked
        return None

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
        if self._fabric_rows is None and self._shared is not None:
            self._fabric_rows = self._shared.get("fabric")  # 0.141.0: geteilt
        if self._fabric_rows is None:
            rows = []
            stmt = select(InvSubnet).where(
                InvSubnet.tenant_id == self.tenant_id, InvSubnet.context.in_(("bd", "epg"))
            )
            for s in self.session.scalars(stmt):
                try:
                    net = ipaddress.ip_network(s.prefix, strict=False)
                except ValueError:
                    continue
                nkey = (s.source_system_id, aci_vrf(s.vrf, s.source_system_id, self.nodes))
                if nkey not in self.nodes:
                    continue
                where = (
                    f"EPG {s.aci_tenant}|{s.ap}|{s.epg}"
                    if s.epg and s.ap
                    else f"BD {s.aci_tenant}|{s.bd or s.context_name}"
                )
                rows.append((net, nkey, f"{where} ({s.prefix})"))
            self._fabric_rows = rows
            if self._shared is not None:
                self._shared["fabric"] = rows
        for net, nkey, text in self._fabric_rows:
            if key not in (None, nkey) or not within(ip, net):
                continue
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

    def vips(self, key: Key) -> list[NatRule]:
        """VIPs der FortiGate (DNAT) im VDOM des Knotens - einmal je Graph geladen."""
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
        return self._vips[key]

    def dnat(self, key: Key, ip, ingress: dict, service=None, src=None) -> dict | None:
        """VIP der FortiGate (DNAT) fuer das Ziel an diesem Knoten (`path_stages.dnat`, ADR
        0070): externe IP bzw. Bereich, `extintf` und - bei Port-Weiterleitung - Protokoll und
        Port passen; ohne Central NAT nur VIPs in einer eingeschalteten Policy -> interne IP(s),
        beim Virtual Server alle Real Server. Ein Netz als Ziel (0.39.0) geht ohne DNAT weiter."""
        n, to, _why = path_stages.dnat(self, key, ip, ingress, service, src)
        return self.nat_out(key, n, to) if n is not None else None

    def nat_out(self, key: Key, n: NatRule, to: list[str]) -> dict:
        """VIP am Hop (`hop.nat`): Name, externe und interne Adresse(n), Ports, Server."""
        raw, attrs = n.raw or {}, n.attrs or {}
        out = {
            "name": n.name,
            "id": n.id,
            "ext": n.ext_ip,
            "to": to,
            "type": n.vip_type,
            "ports": [n.ext_port, n.mapped_port] if n.portforward else None,
            # Ziel-Server mit Namen und Zustand (0.114.1, Anwender: „nur einer und nur die
            # IP, mehr Infos wäre super“): alle Real Server bzw. die zugeordnete(n) IP(s)
            "servers": self.vip_servers(n, to, key),
            "mapped": n.mapped_ip if n.mapped_ip and "-" in n.mapped_ip else None,
            "method": attrs.get("ldb-method"),
            "health": [m.get("name") for m in attrs.get("monitor") or [] if m.get("name")],
            "extintf": raw.get("extintf") if raw.get("extintf") not in (None, "any") else None,
            "comment": raw.get("comment") or None,
        }
        extra = ("servers", "mapped", "method", "health", "extintf", "comment")
        out = {k: v for k, v in out.items() if k not in extra or v not in (None, [], "")}
        # Ziel der VIP ist ein Virtual Service des NSX ALB (0.79.0): im Hop nennen
        port = n.mapped_port if n.portforward else None
        try:
            lb = self.balancer(ipaddress.ip_address(to[0]), ("tcp", _first_port(port)))
        except ValueError:
            lb = None
        if lb is not None:
            out["balancer"] = lb["name"]
        return out

    def vip_servers(self, n: NatRule, to: list[str], key: Key) -> list[dict]:
        """Ziel-Server einer VIP: Real Server (Port, Zustand, Gewicht, Health-Check) bzw. die
        zugeordneten IPs - je mit Namen aus IPAM, Firewall-Objekt und VM."""
        if n.targets:
            rows = [
                {
                    "ip": t.get("ip") or t.get("address"),
                    "port": t.get("port"),
                    "status": t.get("status"),
                    "weight": t.get("weight"),
                    "health": t.get("monitor") or None,
                }
                for t in n.targets
                if isinstance(t, dict) and (t.get("ip") or t.get("address"))
            ]
        else:
            port = n.mapped_port if n.portforward else None
            rows = [{"ip": ip, "port": port} for ip in to]
        return self.describe_servers(rows[:MAX_SERVERS], key[0], key[1])

    def describe_servers(self, rows: list[dict], sid: int | None = None,
                         vdom: str | None = None) -> list[dict]:  # fmt: skip
        """Namen je Server-IP: DNS/IPAM, Adressobjekt der Firewall, VM (vCenter) mit EPG."""
        from app.store import nat as nat_store_
        from app.store import virtual  # spaet: nur fuer VIP-Ziele

        ips = {str(r["ip"]) for r in rows if r.get("ip")}
        objects = nat_store_.servers_by_ip(self.session, self.tenant_id, ips)
        vms = virtual.Lookup(self.session, self.tenant_id, ips) if ips else None
        out = []
        for r in rows:
            ip = str(r.get("ip") or "")
            vm = vms.info(ip) if vms is not None else None
            found = objects.get(ip, [])
            entry = {
                **{k: v for k, v in r.items() if v not in (None, "", [])},
                "names": self.names(ip),
                "object": nat_store_.server_names(found, sid, vdom)
                if sid is not None
                else [o[2] for o in found],
                "vm": {k: vm[k] for k in ("name", "id", "power_state", "epg") if vm.get(k)}
                if vm
                else None,
            }
            out.append({k: v for k, v in entry.items() if v not in (None, [], {})})
        return out

    def balancer(self, ip, service: tuple[str, int | None] | None) -> dict | None:
        """Virtual Services eines Load Balancers (NSX ALB, ADR 0027) mit dieser VIP, deren Port
        zum Dienst passt (ohne Dienst die des ersten Ports) - **alle zusammen** (ADR 0070):
        mehrere Virtual Services auf derselben VIP und demselben Port (SNI-Hosting: der Parent
        ohne Pool, die Children je mit einem) verteilen nach dem Hostnamen auf alle ihre Pools.
        -> Server aller Pools (aktive zuerst); `redirect`, wenn der Port nur umleitet."""
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
            # Index je VIP-Adresse (ADR 0070): nicht je Aufruf alle Virtual Services pruefen
            index: dict[str, list] = defaultdict(list)
            for n, source in self._lbs:
                ext = _bounds(n.ext_ip)
                if ext is not None and ext[0] == ext[1]:
                    index[str(ext[0])].append((n, source))
            self.__dict__["_lb_index"] = index
        port = service[1] if service else None
        rows = []
        for n, source in self.__dict__["_lb_index"].get(str(ip), []):
            ports = _port_span(n.ext_port)
            if port is not None and ports is not None and not ports[0] <= port <= ports[1]:
                continue
            rows.append((n, source))
        if not rows:
            return None
        if port is None:  # ohne Dienst: die Virtual Services des ersten Ports
            rows = [x for x in rows if x[0].ext_port == rows[0][0].ext_port]
        servers: list[dict] = []
        spare: list[dict] = []  # Pool Group: Server der Pools tieferer Prioritaet (P-8)
        for n, _source in rows:
            later = _lower_priority(n.targets or [])
            for t in n.targets or []:
                if t.get("ip") and not any(
                    (t["ip"], t.get("port")) == (x["ip"], x.get("port")) for x in servers
                ):
                    servers.append(t)
                    if id(t) in later:
                        spare.append(t)
        # der Virtual Service, der im Hop steht: der erste mit Servern, sonst der erste
        n, source = next((x for x in rows if any(t.get("ip") for t in x[0].targets or [])),
                         rows[0])  # fmt: skip
        attrs = n.attrs or {}
        first = [t for t in servers if t not in spare]
        ready = [t for t in first if t.get("status", "active") == "active"] or first
        waiting = [t for t in spare if t.get("status", "active") == "active"]
        # abgeschaltet (`enabled` bzw. `traffic_enabled` false, P-9): die VIP antwortet nicht
        off = all((x.attrs or {}).get("enabled") is False
                  or (x.attrs or {}).get("traffic_enabled") is False for x, _s in rows)  # fmt: skip
        redirect = next(((x.attrs or {}).get("redirect") for x, _s in rows
                         if (x.attrs or {}).get("redirect")), None)  # fmt: skip
        down = all((x.attrs or {}).get("oper_status") == "OPER_DOWN"
                   or (x.attrs or {}).get("enabled") is False for x, _s in rows)  # fmt: skip
        out = {
            "name": n.name,
            "id": n.id,
            "source_system_id": n.source_system_id,
            "source": source,
            "ext": n.ext_ip,
            "port": n.ext_port,
            "to": [t["ip"] for t in ready],
            # Pool Group (Avi): die Pools tieferer Prioritaet bekommen erst Verkehr, wenn im
            # Pool mit der hoechsten kein Server mehr erreichbar ist - Backup-Wege
            "backup_to": [t["ip"] for t in waiting if t["ip"] not in {x["ip"] for x in ready}]
            or None,
            "server_port": ready[0].get("port") if ready else None,
            "servers": len(servers),
            # alle Server der Pools mit Namen und Zustand (0.114.1)
            "members": self.describe_servers(
                [
                    {
                        "ip": t["ip"],
                        "port": t.get("port"),
                        "status": t.get("status"),
                        "weight": t.get("ratio") or t.get("weight"),
                        "hostname": t.get("hostname"),
                        "pool": t.get("pool"),
                        "priority": t.get("priority"),
                    }
                    for t in servers[:MAX_SERVERS]
                ]
            ),  # fmt: skip
            # weitere Virtual Services derselben VIP und desselben Ports (SNI, ADR 0070)
            "services": [x.name for x, _s in rows] if len(rows) > 1 else None,
            "method": attrs.get("lb_algorithm"),
            "health": attrs.get("health_monitors") or None,
            "fqdn": attrs.get("fqdn"),
            "pool": attrs.get("pool") or attrs.get("pool_group"),
            "tenant": attrs.get("tenant"),
            # Quelle danach: Service Engine (SNAT) bzw. der Client (preserve_client_ip)
            "snat": attrs.get("snat") or "service_engine",
            "down": down,
            "off": off,
            # der Port leitet nur um (HTTP → HTTPS): der Fluss endet am Load Balancer
            "redirect": redirect if not servers else None,
        }
        return {k: v for k, v in out.items() if v not in (None, "", False)}

    def policies(self, key: Key, src, dst, ingress: str | None, service=None,
                 table: Table = 0) -> list[dict]:  # fmt: skip
        """Policy-Routen bzw. SD-WAN-Regeln des Knotens, die fuer den Verkehr gelten (koennen),
        in der Reihenfolge des Geraets (`path_stages.policy_routes`, ADR 0070): `certain` =
        Quelle, Ziel, Eingang und Dienst passen sicher."""
        return path_stages.policy_routes(self, key, src, dst, ingress, service, table)[0]

    def link_peer(self, key: Key, interface: str | None, table: Table) -> list[tuple]:
        """Gegenseite einer Punkt-zu-Punkt-Leitung (Route nur mit Interface, ADR 0070): der
        andere gelesene Knoten im /30- bzw. /31-Netz (IPv6 /126, /127) dieses Interfaces."""
        if not interface:
            return []
        out: list[tuple] = []
        for (nkey, iface), nets in self._p2p().items():
            if nkey != key or iface != interface:
                continue
            for net in nets:
                for (okey, oiface), others in self._p2p().items():
                    if okey != key and net in others and all(okey != x[0] for x in out):
                        out.append((okey, oiface))
        return out

    def _p2p(self) -> dict[tuple, set[str]]:
        """(Knoten, Interface) -> angeschlossene Punkt-zu-Punkt-Netze, einmal je Graph."""
        found = self.__dict__.get("_p2p_nets")
        if found is None:
            found = defaultdict(set)
            for sid, context, prefix, hops in self.session.execute(
                select(TopoRoute.source_system_id, TopoRoute.context, TopoRoute.prefix,
                       TopoRoute.next_hops)
                .where(TopoRoute.tenant_id == self.tenant_id, TopoRoute.table == "rib",
                       TopoRoute.protocol == "connected",
                       TopoRoute.prefix_len.in_((30, 31, 126, 127)))
            ):  # fmt: skip
                for h in hops or []:
                    if h.get("interface") and (sid, context) in self.nodes:
                        found[((sid, context), h["interface"])].add(prefix)
            self.__dict__["_p2p_nets"] = found
        return found


def _lower_priority(targets: list[dict]) -> set[int]:
    """Server einer Pool Group (NSX ALB), die **nicht** im Pool mit der hoechsten Prioritaet
    liegen (`id` der Eintraege): Avi waehlt einen Pool tieferer Prioritaet erst, wenn in keinem
    der hoechsten ein Server gefunden wird (Gegenpruefung P-8). Gelesen ist nur, ob ein Server
    eingeschaltet ist - die hoechste Prioritaet mit einem eingeschalteten Server gilt; ohne
    Prioritaet (ein einzelner Pool) sind alle gleich."""

    def rank(t: dict) -> int | None:
        try:
            return int(t["priority"])
        except (KeyError, TypeError, ValueError):
            return None

    ranked = [(rank(t), t) for t in targets if t.get("ip")]
    levels = {r for r, _t in ranked if r is not None}
    if len(levels) < 2:
        return set()
    usable = {r for r, t in ranked if r is not None and t.get("status", "active") == "active"}
    top = max(usable or levels)
    return {id(t) for r, t in ranked if r is not None and r != top}


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
    entries: list[tuple[Key, str | None, str | None, str]] | None = None,
    with_rules: bool = True,
    balancers: bool = True,
) -> dict:
    """Weg wie `_trace`; mit dem Wege-Speicher des Mandanten (0.140.0, `path_store`) je
    Datenstand nur einmal gerechnet - Doku, Verbindungen und Befunde aller Applikationen teilen
    ihn. Ohne gesetzten Speicher (z. B. Weg-Suche) wie bisher jedes Mal neu."""
    from app.store import path_store

    store = path_store.current(tenant_id)
    args = (session, tenant_id, dst_text, src_text, start, slack, service, graph, checks,
            backups, entries, with_rules, balancers)  # fmt: skip
    if store is None:
        return _trace(*args)
    after = getattr(checks, "after", None)
    key = ("trace", dst_text, src_text, start, slack, service, backups,
           tuple(entries) if entries is not None else None, with_rules, balancers,
           after)  # fmt: skip
    return store.trace(key, lambda: _trace(*args))


def _trace(
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
    entries: list[tuple[Key, str | None, str | None, str]] | None = None,
    with_rules: bool = True,
    balancers: bool = True,
) -> dict:
    """Weg von der Quelle (bzw. dem Startpunkt) zum Ziel: je Pfad die Hops mit Ein- und
    Ausgang (Interface, Zone), Route bzw. Policy-Route und Next-Hop, dazu das Ende - die
    aktiven Wege und die Backup-Wege mit hoechstens `slack` Hops mehr; je FortiGate-Hop die
    passenden Policies (Dienst optional, z. B. `("tcp", 443)`).

    Seit ADR 0070 entscheidet je Hop das Modell des Geraets: die Stufen des Herstellers
    (`path_stages`: DNAT mit Dienst, Policy-Routen in Listenreihenfolge, SD-WAN, IPsec, Load
    Balancer) und das Forwarding (`forwarding`: Prioritaet, Null-Route, rekursiver Next-Hop,
    Route nur mit Interface). Nach den Regeln (`policy_check`, SNAT je Hop) prueft `_enforce`
    Reverse Path und IPsec-Selektoren mit der Quelle, wie sie am Geraet wirklich ankommt. Was
    sich nicht sicher entscheiden laesst, steht gezaehlt in `uncertain` (ADR 0048).

    Viele Wege eines Mandanten (Verbindungen der Applikations-Doku, 0.45.0): `graph` und
    `checks` (`policy_check.Checks`) einmal laden; `backups=False` nur die aktiven Wege.
    `entries` (Anbindung als Quelle, `store/exit_ends.py`): Starts mit Eingang und Zone der
    Anbindung - (Knoten, Interface, Zone, wie erreicht). Rückweg (`store/return_path.py`,
    0.134.0): `with_rules=False` ohne Policies, `balancers=False` endet an der VIP eines Load
    Balancers (die Antwort an die Service Engine geht nicht weiter zum Server)."""
    from app.store import policy_check  # Regeln je Hop - spaet importiert (Regelindex)

    dst = endpoint(dst_text)  # IP bzw. Netz (0.39.0: das Netz als Ganzes)
    src = endpoint(src_text) if src_text and src_text.strip() else None
    g = graph or Graph(session, tenant_id)
    if with_rules and checks is None:
        checks = policy_check.Checks.load(session, tenant_id, after=policy_check.AFTER)
    notes: list[str] = []
    uncertain: Counter = Counter()  # Unsicherheiten am Weg, gezaehlt (ADR 0048, ADR 0070)

    def unsure(kind: str, text: str | None) -> None:
        if text and text not in notes:
            notes.append(text)
            uncertain[kind] += 1

    starts: list[tuple[Key, str | None, str]] = []
    entry_zones: dict[tuple[Key, str | None], str] = {}
    chosen = parse_key(start)
    if entries is not None:  # Anbindung als Quelle: Eingang wie von aussen (z. B. wan1)
        for k, iface, zone, via in entries:
            if k not in g.nodes:
                continue
            if iface is None and zone is None and src is not None and (
                    g.nodes[k].kind in ("firewall", "vdom")):  # fmt: skip
                # 0.133.4 (Anwender: „möglich (Eingang)“ bei allen Policies): Anbindung ohne
                # Interface bzw. Zone - Eingang nach der Route zur Quelle (wie beim Startpunkt)
                iface = g.rpf_interface(k, src)
                if iface is not None:
                    via += L(f", Eingang {iface} nach der Route zur Quelle",
                             f", incoming {iface} by the route to the source")  # fmt: skip
            starts.append((k, iface, via))
        entry_zones = {(k, iface): zone for k, iface, zone, _via in entries if zone}
    elif start and chosen not in g.nodes:
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
        how = L("gewählt", "selected")
        if iface is None and src is not None and g.nodes[chosen].kind in ("firewall", "vdom"):
            # 0.112.0 (Anwender: „abhängig“, obwohl die Regeln offen sind): ohne Eingang war jede
            # Policy mit Interface bzw. Zone davor nur „möglich“ - die Firewall erwartet den
            # Verkehr der Quelle dort, wohin ihre Route zur Quelle zeigt (Reverse Path)
            iface = g.rpf_interface(chosen, src)
            if iface is not None:
                how = L(f"gewählt, Eingang {iface} nach der Route zur Quelle",
                        f"selected, incoming {iface} by the route to the source")  # fmt: skip
        starts = [(chosen, iface, how)]
    elif src is not None:
        how = L("Quelle angeschlossen", "Source connected")
        starts = [(k, iface, how) for k, iface, _ in g.connected_nodes(src)]
        if not starts:
            starts = [(k, None, L(f"Quelle im Fabric: {text}", f"Source in fabric: {text}"))
                      for k, text in g.fabric(src)]  # fmt: skip
        if not starts and is_net(src) and src.prefixlen:
            # ein Netz, groesser als die angeschlossenen (Gegenpruefung P-13, ueblich bei breiten
            # Regeln): der Verkehr aus den angeschlossenen Teilen kommt dort an - je Knoten und
            # Eingang ein Start; fuer den uebrigen Teil ist der Start unbekannt, gezaehlt
            parts, whole = g.connected_parts(src)
            for k, iface, prefixes in parts:
                how = L(f"Teil der Quelle angeschlossen ({prefixes})",
                        f"part of the source connected ({prefixes})")  # fmt: skip
                starts.append((k, iface, how))
            if starts and not whole:
                unsure("start_part", L(
                    f"Von der Quelle {src} sind nur Teile an gelesenen Geräten angeschlossen – "
                    "der Weg beginnt dort; für den übrigen Teil ist der Start unbekannt "
                    "(nicht geprüft)",
                    f"Only parts of the source {src} are connected to devices read – the path "
                    "starts there; for the remaining part the start is unknown (not checked)",
                ))  # fmt: skip
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
    merged: dict[tuple, dict] = {}  # Wege zu Servern derselben Klasse: einer, mit allen Zielen
    dropped = [0]  # aktive Wege ueber der Grenze MAX_PATHS (gekuerzt, gezaehlt)

    def record(out: list, hops: list, hop: dict, kind: str, text: str, deviation,
               boundary: dict | None = None) -> None:  # fmt: skip
        """Weg ablegen. Wege, die sich nur im Server hinter einer VIP bzw. einem Load Balancer
        unterscheiden und fuer Routing und Regeln gleich sind (gleiche Hops, gleiche
        Adressklasse der Policies), sind **ein** Weg mit allen diesen Servern (`targets`) -
        gerechnet je Aequivalenzklasse, nicht je Server (ADR 0070)."""
        full = [*hops, hop]
        fan = [i for i, h in enumerate(full) if (h.get("lb") or h.get("nat") or {}).get("targets")]
        limit = MAX_PATHS if out is paths else MAX_BACKUP
        if fan:
            sig = (id(out), _fan_signature(full, kind, text, deviation), _classes(full, fan[-1]))
            prior = merged.get(sig)
            if prior is not None:
                for i in fan:
                    part = full[i].get("lb") or full[i].get("nat")
                    had = prior["hops"][i].get("lb") or prior["hops"][i].get("nat")
                    had["targets"] += [t for t in part["targets"] if t not in had["targets"]]
                return
        if len(out) >= limit:
            # mehr Wege als die Grenze (z. B. ECMP ueber viele Next-Hops an einem Knoten):
            # gekuerzt und gezaehlt, nie still (ADR 0048)
            if out is paths:
                dropped[0] += 1
            return
        _record(out, hops, hop, kind, text, deviation, boundary)
        if fan:
            merged[sig] = out[-1]

    def _classes(full: list[dict], after: int) -> tuple:
        """Adressklasse des Ziels je Firewall-Hop hinter der Verzweigung (`policy_check`): nur
        Wege gleicher Klasse haben dieselben Urteile. ACI prueft je Adresse - dort kein
        Zusammenfassen."""
        if checks is None:
            return ()
        out = []
        for h in full[after:]:
            cur = h.get("_cur")
            if h["kind"] in path_stages.FIREWALLS and cur:
                out.append(checks.checker.addr_class(h["source_system_id"], h["context"], "dst",
                                                     endpoint(cur)))  # fmt: skip
            elif h["kind"] == "vrf":
                out.append(cur)
        return tuple(out)

    def fan_out(hops, hop, key, iface, via, table, dev, part: str, targets: list[str],
                same_hop: bool) -> None:  # fmt: skip
        """Zu jedem Ziel einer VIP (Virtual Server) bzw. eines Pools einen Zweig: der Load
        Balancer verteilt auf alle Mitglieder."""
        queue = active if dev is None else backup
        for t in targets:
            try:
                server = ipaddress.ip_address(t)
            except ValueError:
                continue
            branch = {**hop, part: {**hop[part], "targets": [t]}}
            if same_hop:  # VIP der Firewall: derselbe Hop, DNAT schon entschieden
                queue.append((hops, key, iface, via, table, dev, server, branch))
            else:  # Load Balancer: eigener Hop, weiter am selben Knoten
                queue.append(([*hops, branch], key, iface, via, table, dev, server, None))

    def behind(hops, hop, key, iface, table, dev, cur) -> bool:
        """VIP eines Load Balancers hier angeschlossen (NSX ALB, ADR 0027): der Weg geht am
        selben Knoten vom Netz der VIP zu den Servern der Pools weiter (einmal je Weg) - zu
        allen (ADR 0070); eine Umleitung HTTP → HTTPS endet am Load Balancer."""
        if not balancers or any(h.get("lb") for h in hops):
            return False
        # Port am Eingang des Load Balancers: nach der Port-Weiterleitung der VIP (0.137.0)
        lb = g.balancer(cur, path_service.at_end([*hops, hop], service))
        if lb is None:
            return False
        hop["lb"] = lb
        out = paths if dev is None else backups_found
        if lb.get("off"):
            # abgeschalteter Virtual Service (Gegenpruefung P-9): die Service Engine antwortet
            # fuer die VIP nicht (kein ARP) - nicht erreicht
            text = L(
                f"verworfen am Load Balancer {lb['name']}: der Virtual Service ist abgeschaltet "
                "(enabled bzw. traffic_enabled aus) – die VIP antwortet nicht",
                f"dropped at load balancer {lb['name']}: the virtual service is disabled "
                "(enabled or traffic_enabled off) – the VIP does not answer",
            )
            hop["dropped"] = {"stage": "lb", "text": text}
            _record(out, hops, hop, "dropped", text, dev)
            return True
        if lb.get("redirect"):
            text = L(
                f"Load Balancer {lb['name']} leitet um (HTTP → HTTPS) – der Fluss endet am "
                "Load Balancer, der Client kommt auf dem anderen Port neu",
                f"Load Balancer {lb['name']} redirects (HTTP → HTTPS) – the flow ends at the "
                "load balancer, the client returns on the other port",
            )
            _record(out, hops, hop, "redirect", text, dev)
            return True
        if not lb.get("to"):
            text = L(
                f"Load Balancer {lb['name']}: kein Server mit IP im Pool",
                f"Load Balancer {lb['name']}: no server with an IP in the pool",
            )
            _record(out, hops, hop, "unknown", text, dev)
            return True
        name = f"Load Balancer {lb['name']} ({lb.get('source', 'NSX ALB')})"
        via = L(f"über {name}", f"via {name}")
        fan_out(hops, hop, key, iface, via, table, dev, "lb", lb["to"], False)
        if backups and dev is None and lb.get("backup_to"):
            # Pool Group (P-8): Pools tieferer Prioritaet sind Backup-Wege, nie aktive
            deviation = {"hop": len(hops), "label": name, "reason": L(
                "Pool tieferer Priorität der Pool Group – nur, wenn im Pool mit der höchsten "
                "Priorität kein Server erreichbar ist",
                "pool of lower priority in the pool group – only if no server of the pool "
                "with the highest priority is reachable",
            )}  # fmt: skip
            fan_out(hops, hop, key, iface, via, table, deviation, "lb", lb["backup_to"], False)
        return True

    # (Hops, Knoten, Eingang, wie erreicht, VRF, Abweichung, Ziel nach NAT, begonnener Hop) -
    # aktive zuerst
    active = [([], key, iface, via, g.table(key, iface), None, dst, None)
              for key, iface, via in starts]  # fmt: skip
    backup: list[tuple] = []
    cut = False
    while active or backup:
        if active and len(paths) >= MAX_PATHS or not active and len(backups_found) >= MAX_BACKUP:
            cut = True
            break
        hops, key, ingress, via, table, dev, cur, begun = (active or backup).pop(0)
        out = paths if dev is None else backups_found

        node = g.nodes[key]
        firewall = node.kind in path_stages.FIREWALLS
        if begun is not None:  # Zweig einer VIP mit mehreren Servern: der Hop steht schon
            hop = begun
        else:
            hop = {**node.out(), "via": via, "ingress": _port(g, key, ingress)}
            if not hops and (key, ingress) in entry_zones:  # Anbindung: Zone ohne Interface
                hop["ingress"].setdefault("zone", entry_zones[(key, ingress)])
            if table != 0:
                hop["vrf"] = table
            leg = hops[_after_balancer(hops) :]  # nach einem Load Balancer: neuer Abschnitt
            if any((h["key"], h.get("vrf", 0)) == (node.key, table) for h in leg):
                text = L(f"Schleife: {node.label} ist schon auf dem Weg",
                         f"Loop: {node.label} is already on the path")  # fmt: skip
                record(out, hops, hop, "loop", text, dev)
                continue
            if len(hops) >= MAX_HOPS:
                text = L(f"mehr als {MAX_HOPS} Hops – abgebrochen",
                         f"more than {MAX_HOPS} hops – aborted")  # fmt: skip
                record(out, hops, hop, "max_hops", text, dev)
                continue
            if cur != dst:
                hop["dst"] = str(cur)  # Ziel nach NAT auf einem Hop davor
            # Dienst an diesem Hop: nach der Port-Weiterleitung und den Load Balancern davor
            svc = path_service.at_end([*hops, hop], service)
            if firewall:  # Stufe DNAT: VIP nach Adresse, Eingang und Dienst (ADR 0070)
                vip, to, why = path_stages.dnat(g, key, cur, hop["ingress"], svc, src)
                unsure("nat", why)
                if src is not None and (names := path_stages.source_filtered(g, key, cur)):
                    hop["_src_filter"] = names
                if vip is not None:
                    hop["nat"] = g.nat_out(key, vip, to)
                    if len(to) > 1:  # Virtual Server: zu jedem Real Server
                        fan_out(hops, hop, key, ingress, via, table, dev, "nat", to, True)
                        continue
                    cur = ipaddress.ip_address(to[0])
        if begun is not None:
            cur = ipaddress.ip_address(hop["nat"]["targets"][0])
        hop["_cur"] = str(cur)
        if hop.get("nat"):  # nach der Port-Weiterleitung dieser VIP
            svc = path_service.at_end([*hops, hop], service)
        # Stufe Policy-Routen und SD-WAN (Reihenfolge der Liste, Protokoll und Port, deny)
        shown, follow, why = path_stages.policy_routes(g, key, src, cur, ingress, svc, table)
        unsure("policy_route", why)
        hop["policy_routes"] = shown
        decision = g.route(key, cur, table)  # Stufe Routing-Tabelle (`forwarding`)
        route = None
        if decision is not None:
            route = {k: v for k, v in decision.items() if k != "branches"}
        hop["route"] = route
        inside = g.fabric(cur, key) if node.kind == "vrf" else []
        if follow is not None:
            hop["policy"] = follow
            if len(follow.get("members") or []) > 1:
                names = ", ".join(m["interface"] for m in follow["members"])
                unsure("sdwan", L(
                    f"{node.label}: SD-WAN-Regel {follow.get('name') or follow.get('seq')} – "
                    f"welches Mitglied ({names}) gewählt wird, hängt vom Zustand der Links ab "
                    "(nicht gelesen); gezeigt ist das erste",
                    f"{node.label}: SD-WAN rule {follow.get('name') or follow.get('seq')} – "
                    f"which member ({names}) is chosen depends on the link state (not read); "
                    "the first one is shown",
                ))  # fmt: skip
            next_hops = path_stages.follow_hops(g, key, follow, cur, table)
        elif inside:
            if behind(hops, hop, key, None, table, dev, cur):
                continue
            text = L(f"Ziel im Fabric: {inside[0][1]}", f"Destination in fabric: {inside[0][1]}")
            record(out, hops, hop, "fabric", text, dev)
            continue
        elif node.kind == "vrf" and (leak := g.leaked(key, cur)) is not None:
            # Ziel in einer anderen VRF, ins Fabric dieser VRF geleakt (0.44.0): im Fabric,
            # ohne L3Out - der Contract zwischen den VRFs entscheidet (aci_check)
            vrf, where, why, leak_kind = leak
            # `kind` (shared | leak): die Oberflaeche unterscheidet danach, nicht am Text
            hop["leak"] = {"vrf": vrf, "where": where, "text": why, "kind": leak_kind}
            text = L(f"Ziel im Fabric der VRF {vrf}: {where} – {why}",
                     f"Destination in the fabric of VRF {vrf}: {where} – {why}")  # fmt: skip
            record(out, hops, hop, "fabric", text, dev)
            continue
        elif route is None:
            text = L("keine Route zum Ziel", "no route to the destination") + (
                f" (VRF {table})" if table != 0 else ""
            )
            if [m for m in node.missing if path_stages.is_routing_missing(m)]:
                text = L(
                    "keine Route bekannt – Routing-Tabelle nicht (ganz) lesbar",
                    "no route known – routing table not (fully) readable",
                )
            record(out, hops, hop, "no_route", text, dev)
            continue
        elif route["action"] == "drop":
            # Null-/Blackhole-Route (ADR 0070): das Geraet verwirft - nie „angeschlossen“
            iface = next((h.get("interface") for h in route["next_hops"]), None)
            hop["egress"] = _port(g, key, iface)
            text = L(f"verworfen an {node.label}: Null-Route {route['prefix']}",
                     f"dropped at {node.label}: null route {route['prefix']}")  # fmt: skip
            record(out, hops, hop, "dropped", text, dev)
            continue
        elif route["action"] == "connected":
            iface = next((h.get("interface") for h in route["next_hops"]), None)
            hop["egress"] = _port(g, key, iface)
            if behind(hops, hop, key, iface, table, dev, cur):
                continue
            where = f" ({iface})" if iface else ""
            text = L(f"Ziel angeschlossen an {node.label}{where}",
                     f"Destination connected to {node.label}{where}")  # fmt: skip
            record(out, hops, hop, "connected", text, dev)
            continue
        else:
            next_hops = decision["branches"]
        branches = [(nh, hop, dev) for nh in next_hops]
        # eine Abweichung je Weg: Backup-Next-Hops dieses Knotens (nicht bei Policy-Routen)
        if backups and dev is None and follow is None and route is not None:
            for b in g.backups(key, cur, table, route):
                if b.get("blocked"):  # BGP-Filter sperrt den Pfad (0.114.0): nie ein Backup
                    note = L(f"Backup an {node.label} über {b['next_hop'].get('ip')} verworfen: "
                             f"{b['blocked']}",
                             f"Backup at {node.label} via {b['next_hop'].get('ip')} discarded: "
                             f"{b['blocked']}")  # fmt: skip
                    if note not in notes:
                        notes.append(note)
                    continue
                info = {k: b[k] for k in ("prefix", "protocol", "distance", "reason")}
                deviation = {"hop": len(hops), "label": node.label, "reason": b["reason"]}
                branches.append((b["next_hop"], {**hop, "backup": info}, deviation))
        for nh, base, deviation in branches:
            queue = active if deviation is None else backup
            target = paths if deviation is None else backups_found
            ip = nh.get("ip")
            egress = {**_port(g, key, nh.get("interface")), "next_hop": ip, **g.resolve(ip)}
            if nh.get("recursive"):
                # rekursiver Next-Hop (ADR 0070): die Route nennt `recursive[0]`, erreicht wird
                # er ueber diesen Nachbarn (Route `recursive_via`)
                egress["recursive"] = {"next_hop": nh["recursive"][0],
                                       "via": nh.get("recursive_via")}  # fmt: skip
            branch = {**base, "egress": egress}

            nxt = _neighbors(g, key, ip)
            peer = None if nxt else g.vdom_peer(key, nh.get("interface"))
            if peer is not None:  # Inter-VDOM-Link ohne (gelesene) Next-Hop-IP
                link = f"{nh.get('interface')} ↔ {peer[1]}"
                nxt = [(peer[0], peer[1], L(f"VDOM-Link {link}", f"VDOM link {link}"))]
            tun = g.tunnel(key, nh.get("interface"))  # IPsec-Tunnel (1.19.0, ADR 0063)
            if tun is not None:
                nxt = vpn_store.hop(g, key, tun, cur, egress, nxt)
            elif not nxt and not ip and _is_line(nh.get("interface")):
                # Route nur mit Interface (ADR 0070): an die Gegenseite der Leitung - nur auf
                # einer echten Punkt-zu-Punkt-Leitung (Tunnel, seriell); auf Ethernet fragt das
                # Geraet auch im /30 per ARP nach dem Ziel (Gegenpruefung P-12, unten)
                line = L(f"Leitung {nh.get('interface')}", f"link {nh.get('interface')}")
                nxt = [(k, i, line) for k, i in g.link_peer(key, nh.get("interface"), table)]
            for nkey, niface, how in nxt:
                queue.append(
                    (
                        [*hops, branch],
                        nkey,
                        niface,
                        how,
                        g.table(nkey, niface),
                        deviation,
                        cur,
                        None,
                    )
                )
            if nxt:
                continue
            default = route is not None and route["prefix_len"] == 0 and follow is None
            ex = g.exit_for(key, nh.get("interface"), egress.get("zone"), ip)
            if ex is None and nh.get("recursive"):
                ex = g.exit_for(key, None, None, nh["recursive"][0])
            if ex is not None:
                egress["exit"] = {"id": ex.id, "name": ex.name, "kind": ex.kind}
                target_key = _exit_target(ex, cur)
                if target_key is not None and target_key in g.nodes:
                    via_exit = L(f"über {_exit_label(ex)} (manuelle Route)",
                                 f"via {_exit_label(ex)} (manual route)")  # fmt: skip
                    queue.append(([*hops, branch], target_key, None, via_exit, 0, deviation, cur,
                                  None))  # fmt: skip
                    continue
                if default and ex.kind == "internet" and is_private(cur) and not _in_exit(ex, cur):
                    # privates Ziel ueber die Default-Route ins Internet (ADR 0070): nicht geprueft
                    text = _unchecked_default(node.label, cur, ip, _exit_label(ex))
                    uncertain["default"] += 1
                    record(target, hops, branch, "unchecked", text, deviation)
                    continue
                text = _exit_text(ex, cur, ip, g.names(ip))
                edge = g.boundary().judge(key, ip, nh.get("interface"), egress.get("zone"), ex)
                record(target, hops, branch, "exit", text, deviation, edge)
                continue
            # VIP eines Load Balancers hinter einem Router ohne gelesenes Geraet (0.79.0): weiter
            # vom Load Balancer zum Server, am selben Knoten
            if behind(hops, branch, key, nh.get("interface"), table, deviation, cur):
                continue
            if tun is not None:  # Gegenstelle ohne gelesenes Geraet: sichtbar, gezaehlt (ADR 0048)
                gw = egress["vpn"].get("remote_gw")
                text = vpn_store.end_text(egress["vpn"], g.names(gw) if gw else None)
                record(target, hops, branch, "vpn", text, deviation)
                continue
            known = g.names(ip) or ([egress["network"]] if egress.get("network") else [])
            name = f" ({', '.join(known)})" if known else ""
            if not ip and _is_line(nh.get("interface")):
                iface = nh.get("interface") or "?"
                kind, text = "unknown", L(
                    f"weiter über {iface} ohne Next-Hop – die Gegenseite der Leitung ist nicht "
                    "gelesen",
                    f"onward via {iface} without next-hop – the far end of the link has not "
                    "been read")  # fmt: skip
            elif not ip:
                # Route nur mit Interface auf einem Netz mit mehreren Teilnehmern (ADR 0070):
                # das Geraet fragt direkt nach dem Ziel (ARP) - es ist dort nur, wenn es wirklich
                # am Interface haengt bzw. jemand per Proxy-ARP antwortet; nicht belegt
                iface = nh.get("interface") or "?"
                # im Transfernetz (/30, /31) liegt ein gelesenes Geraet: an kommt es dort nur,
                # wenn dieses per Proxy-ARP antwortet - nicht gelesen (P-12)
                far = ", ".join(g.nodes[k].label for k, _i in g.link_peer(key, iface, table))
                peer_de = (f"; die Gegenseite {far} nimmt den Verkehr nur mit Proxy-ARP an "
                           "(nicht gelesen)") if far else ""  # fmt: skip
                peer_en = (f"; the far end {far} accepts the traffic with proxy ARP only "
                           "(not read)") if far else ""  # fmt: skip
                kind, text = "unchecked", L(
                    f"nicht geprüft: Route nur mit Interface {iface} an {node.label} – das "
                    "Gerät sucht das Ziel direkt am Interface (ARP); ob es dort ist, ist nicht "
                    f"belegt{peer_de}",
                    f"not checked: route with interface {iface} only at {node.label} – the "
                    "device looks for the destination directly on the interface (ARP); whether "
                    f"it is there is not proven{peer_en}")  # fmt: skip
                uncertain["onlink"] += 1
            elif default and is_private(cur):
                # privates Ziel ueber die Default-Route an ein nicht gelesenes Geraet (ADR 0070):
                # kein Beleg, dass es dort ankommt - Grenze „nicht geprueft“, nie „erreicht“
                kind, text = "unchecked", _unchecked_default(node.label, cur, ip, name.strip())
                uncertain["default"] += 1
            elif default:
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
            record(target, hops, branch, kind, text, deviation, edge)
    pending = len(active) if cut else 0
    if pending or dropped[0]:
        # gekuerzt: sichtbar und gezaehlt (ADR 0048) - die uebrigen Wege sind nicht geprueft
        notes.append(L(f"mehr als {MAX_PATHS} Wege – gekürzt",
                       f"more than {MAX_PATHS} paths – truncated"))  # fmt: skip
        uncertain["truncated"] += pending + dropped[0]
    elif cut:
        notes.append(L(f"mehr als {MAX_BACKUP} Backup-Wege – gekürzt",
                       f"more than {MAX_BACKUP} backup paths – truncated"))  # fmt: skip
    primary = _unique(paths)
    for p in primary:
        p["kind"] = "active"
    found_backups = _unique(backups_found)
    if with_rules:  # ohne: nur das Routing (Rueckweg, 0.134.0)

        def central(hop: dict, cur_src, hop_dst, cur_svc) -> dict | None:
            """Central SNAT der FortiGate an diesem Hop (nur mit Central NAT, ADR 0070)."""
            key = (hop["source_system_id"], hop["context"])
            return path_stages.central_snat(g, key, hop.get("ingress") or {},
                                            hop.get("egress") or {}, cur_src, hop_dst,
                                            cur_svc)  # fmt: skip

        any_central = any(c.get("central_nat") for c in g.settings.values())
        for p in primary + found_backups:  # Central NAT je FortiGate/VDOM fuer den Abgleich
            for h in p["hops"]:  # (ADR 0071): True | False | None = nicht gelesen
                if h.get("kind") in policy_check.FIREWALL_KINDS:
                    key = (h.get("source_system_id"), h.get("context"))
                    h["central_nat"] = path_stages.settings(g, key).get("central_nat")
        policy_check.check_paths(session, tenant_id, primary + found_backups, src, dst, service,
                                 checks, central=central if any_central else None)  # fmt: skip
        for p in primary + found_backups:
            for h in p["hops"]:
                cen = h.get("central_snat") or {}
                if cen.get("unread") or cen.get("sure") is False:
                    unsure("nat", L(
                        f"NAT nicht geprüft an {h['label']}: Central SNAT "
                        + ("nicht gelesen" if cen.get("unread") else
                           "nicht sicher (Objekt bzw. Dienst nicht prüfbar)"),
                        f"NAT not checked at {h['label']}: central SNAT "
                        + ("not read" if cen.get("unread") else
                           "not certain (object or service cannot be checked)"),
                    ))  # fmt: skip
    # Reverse Path und IPsec-Selektoren mit der Quelle nach SNAT (ADR 0070)
    if _enforce(g, primary + found_backups, src, unsure, with_rules):
        # gekuerzte Wege koennen jetzt gleich sein (ECMP hinter der Stelle)
        primary, found_backups = _unique(primary), _unique(found_backups)
    shown, hidden = _backups(primary, found_backups, slack)
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
    for p in primary + found_backups:
        for h in p["hops"]:
            h.pop("_cur", None)
            h.pop("_src_filter", None)
    out = {
        "start": [g.nodes[k].out() | {"via": via} for k, _, via in starts],
        # Session-Sync-Paar bzw. Verbund als ein Hop (0.29.0)
        "paths": _merge_parallel(primary) + _merge_parallel(shown),
        "notes": notes,
        "slack": slack,
        "service": _service_text(service),
    }
    if uncertain:
        # was nicht sicher entschieden ist, je Art gezaehlt (`path_stages.UNCERTAIN`)
        out["uncertain"] = dict(uncertain)
    # Ports entlang des (ersten) Wegs: Client, VIP, Load Balancer, Server (0.137.0)
    first = next((p for p in out["paths"] if p.get("kind") != "backup"), None)
    if first is not None and (ports := path_service.chain(first, service)) is not None:
        if len({x["service"] for x in ports}) > 1:
            out["service_chain"] = ports
    return out


def _fan_signature(full: list[dict], kind: str, text: str, deviation) -> tuple:
    hops = tuple(
        (h["key"], *((h.get("egress") or {}).get(k) for k in ("next_hop", "interface")))
        for h in full
    )
    dev = deviation or {}
    return (hops, kind, text, dev.get("hop"), dev.get("reason"))


# Punkt-zu-Punkt nach dem Namen des Interfaces: Cisco `Tunnel0`/`Tu0`, `Serial0/0`/`Se0/0`,
# `Dialer1`/`Di1`, `Virtual-Access1`/`Vi1`, `Virtual-Template1`/`Vt1`, `Multilink1`, Junos `st0`
# (mit Ziffer hinter dem Kuerzel - „server“ bzw. „vip-net“ einer FortiGate sind keine Leitung);
# `gre…`, `vti…`, `ppp…`, Junos `gr-`, `ip-`, `lt-`.
_LINE = re.compile(
    r"^(?:(?:tu|tunnel|se|serial|di|dialer|vi|vt|virtual-access|virtual-template|mu|multilink"
    r"|st)\s*\d|gre|vti|ppp|gr-|ip-|lt-)",
    re.I,
)


def _is_line(interface: str | None) -> bool:
    """Interface einer Punkt-zu-Punkt-Leitung (Tunnel, seriell) - eine Route nur mit diesem
    Interface geht an die Gegenseite, nicht per ARP ans Ziel. Ein /30 bzw. /31 allein macht ein
    Ethernet-Interface nicht zur Leitung (Gegenpruefung P-12): der Router behandelt das Ziel als
    angeschlossen und sucht es per ARP - an kommt es nur mit Proxy-ARP der Gegenseite."""
    if not interface:
        return True
    return bool(_LINE.match(interface))


def _in_exit(ex: TopoExit, dst) -> bool:
    """Ziel in den Netzen der Anbindung (von Hand erfasst)?"""
    for p in ex.prefixes or []:
        try:
            if within(dst, ipaddress.ip_network(p, strict=False)):
                return True
        except ValueError:
            continue
    return False


def _unchecked_default(label: str, dst, ip: str | None, where: str) -> str:
    to = f" {where}" if where else ""
    return L(
        f"nicht geprüft (Default-Route): {label} kennt das private Ziel {dst} nur über die "
        f"Default-Route an {ip or '?'}{to} – kein gelesenes Gerät, ob es dort ankommt, ist "
        "nicht belegt",
        f"not checked (default route): {label} knows the private destination {dst} only via "
        f"the default route to {ip or '?'}{to} – no device read, whether it arrives there is "
        "not proven",
    )


def _enforce(g: Graph, found: list[dict], src, unsure, with_rules: bool) -> bool:
    """Stufen, die von der Quelle abhaengen, mit der Quelle, wie sie am Geraet ankommt (nach
    dem SNAT der Hops davor, `hop.snat` aus `policy_check`): Reverse Path am Eingang jeder
    FortiGate und die Phase-2-Selektoren am Ausgang in einen IPsec-Tunnel. Verwirft das Geraet,
    endet der Weg dort (`dropped`); fehlen die Einstellungen, endet er als `unchecked` - beides
    sichtbar, nie still (ADR 0070, ADR 0063 Nachtrag). -> ob ein Weg gekuerzt wurde."""
    from app.store import policy_check  # spaet wie in `_trace`

    changed = False
    for p in found:
        cur_src = src
        lb = None
        for i, hop in enumerate(p["hops"]):
            if lb is not None and lb.get("snat") != "client":
                cur_src = None  # hinter dem Load Balancer: Adresse der Service Engine unbekannt
            lb = hop.get("lb")
            if hop["kind"] not in path_stages.FIREWALLS:
                continue
            key = (hop["source_system_id"], hop["context"])
            ingress = (hop.get("ingress") or {}).get("interface")
            if src is not None and cur_src != src and path_stages.source_rules(g, key):
                # die Policy-Routen dieses Hops sind mit der urspruenglichen Quelle verglichen -
                # nach einem SNAT davor gilt die neue Quelle: nicht geprueft, gezaehlt
                unsure("policy_route", L(
                    f"{hop['label']}: Policy-Routen mit der ursprünglichen Quelle {src} "
                    "verglichen – nach dem SNAT davor nicht geprüft",
                    f"{hop['label']}: policy routes compared with the original source {src} – "
                    "not checked after the SNAT before it",
                ))  # fmt: skip
            if hop.get("_src_filter") and cur_src != src:
                # der Quell-Filter der VIP ist mit der urspruenglichen Quelle verglichen (P-2)
                names = ", ".join(hop["_src_filter"][:3])
                unsure("nat", L(
                    f"NAT nicht geprüft an {hop['label']}: Quell-Filter der VIP {names} mit der "
                    f"ursprünglichen Quelle {src} verglichen – nach dem SNAT davor nicht geprüft",
                    f"NAT not checked at {hop['label']}: source filter of VIP {names} compared "
                    f"with the original source {src} – not checked after the SNAT before it",
                ))  # fmt: skip
            if i > 0 and cur_src is not None and ingress:
                res = path_stages.reverse_path(g, key, hop.get("vrf", 0), cur_src, ingress)
                if res is not None and res["state"] == "note":
                    unsure("rpf", res["text"])
                elif res is not None:
                    if res["state"] == "unchecked":
                        unsure("rpf", res["text"])
                    _cut_path(p, i, res, before=True)
                    break
            snat = hop.get("snat")
            if snat:
                to = snat.get("to")
                cur_src = endpoint(to) if to else None
            v = (hop.get("egress") or {}).get("vpn")
            if not v:
                continue
            tun = g.vpn().by_name.get((key[0], key[1], v.get("name")))
            if tun is None:
                continue
            inst = next((x for x in tun.instances if x.get("name") == v.get("instance")), None)
            cur = endpoint(hop["_cur"]) if hop.get("_cur") else None
            res = path_stages.ipsec(tun, inst, cur_src, cur, hop["label"])
            if res is None:
                continue
            if res["state"] == "note":
                unsure("vpn", res["text"])
                continue
            _cut_path(p, i, res, before=False)
            break
        if with_rules and p.get("_cut"):
            verdicts = [(h["label"], h["rules"]["verdict"], h["kind"], h["rules"].get("text"))
                        for h in p["hops"] if h.get("rules")]  # fmt: skip
            p["policy"] = policy_check._overall(verdicts)
        changed = changed or bool(p.pop("_cut", None))
    return changed


def _cut_path(p: dict, i: int, res: dict, before: bool) -> None:
    """Weg endet an Hop `i`: `before` = am Eingang (vor NAT, Routing und Regeln dieses Hops),
    sonst am Ausgang (die Regeln dieses Hops galten noch)."""
    p["hops"] = p["hops"][: i + 1]
    hop = p["hops"][i] = dict(p["hops"][i])
    if before:
        for k in ("egress", "route", "policy", "policy_routes", "nat", "lb", "rules", "snat",
                  "backup", "checked_service"):  # fmt: skip
            hop.pop(k, None)
    hop.pop("boundary", None)
    hop["dropped"] = {"stage": "rpf" if before else "ipsec", "text": res["text"]}
    p["end"] = {"kind": res["state"], "text": res["text"]}
    p.pop("boundary", None)
    p["_cut"] = True
    dev = p.get("deviation")
    if dev and dev.get("hop", 0) > i:
        p.pop("deviation", None)


def target_of(g: Graph, result: dict) -> dict | None:
    """Ziel hinter einer VIP bzw. einem Load Balancer (0.117.0, Anwender: „wenn ich ein V-EXT_
    auflöse, nimmt er als Ziel nicht die VMs hinter dem Loadbalancer, sondern das Subnetz“): die
    Server am Ende des Wegs (aktive Wege zuerst) mit ihrer EPG - fuer die Anzeige, die Zielzone
    und die Absicht. Der Weg selbst bleibt (VIP, Load Balancer, Regeln je Hop). None ohne VIP
    bzw. Load Balancer auf dem Weg."""
    found = None
    for p in sorted(result.get("paths") or [], key=lambda p: p.get("kind") != "active"):
        hops = p.get("hops") or []
        lb = next((h["lb"] for h in reversed(hops) if h.get("lb")), None)
        nat = next((h["nat"] for h in hops if h.get("nat")), None)
        if lb is not None or nat is not None:
            found = (lb, nat)
            break
    if found is None:
        return None
    lb, nat = found
    if lb is not None:
        rows = lb.get("members") or [{"ip": ip} for ip in lb.get("to") or []]
    else:
        rows = nat.get("servers") or [{"ip": ip} for ip in nat.get("to") or []]
    from app.store import epg_locate  # spaet: nur mit VIP bzw. Load Balancer auf dem Weg

    servers: list[dict] = []
    epgs: dict[str, int] = {}
    located = epg_locate.server_epgs(g.session, g.tenant_id,
                                     [str(r.get("ip") or "") for r in rows])  # fmt: skip
    for r in rows:
        ip = str(r.get("ip") or "")
        if not ip:
            continue
        vm = r.get("vm") or {}
        epg = located.get(ip)
        names = r.get("names") or []
        entry = {"ip": ip, "name": vm.get("name") or (names[0] if names else None),
                 "epg": epg, "status": r.get("status")}  # fmt: skip
        servers.append({k: v for k, v in entry.items() if v})
        if epg:
            epgs[epg] = epgs.get(epg, 0) + 1
    out = {
        "vip": nat.get("name") if nat else None,
        "balancer": lb.get("name") if lb else None,
        "servers": servers,
        "ips": [x["ip"] for x in servers],
        "epgs": [{"epg": e, "servers": n} for e, n in sorted(epgs.items(), key=lambda x: -x[1])],
    }
    return {k: v for k, v in out.items() if v not in (None, [])} if servers else None


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
    """Woran zwei Wege derselbe sind: Hops mit Ausgang, die Server hinter VIP bzw. Load Balancer
    (`targets`) und das Ende. Wege, die sich nur im Server unterscheiden, sind **nicht** gleich
    (Gegenpruefung P-5): Server derselben Klasse hat `record` schon zu einem Weg mit allen
    Zielen zusammengefasst - was hier noch getrennt ankommt, hat eigene Regeln (andere EPG,
    anderes Urteil der Policies) und darf nicht still wegfallen. Ebenso der Eingang am Start:
    mehrere Starts am selben Knoten (Teile der Quelle an verschiedenen Interfaces, P-13; mehrere
    Eingaenge von aussen) haben je eigene Policies."""
    first = (p["hops"][0].get("ingress") or {}).get("interface") if p["hops"] else None
    return (
        (first,)
        + tuple(
            (
                h["key"],
                *((h.get("egress") or {}).get(k) for k in ("next_hop", "interface")),
                tuple((h.get("lb") or h.get("nat") or {}).get("targets") or ()),
            )
            for h in p["hops"]
        )
        + (p["end"]["kind"], p["end"].get("text"))
    )


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

            hops_rules = [(h["label"], h["rules"]["verdict"], h["kind"], h["rules"].get("text"))
                          for p in same for h in p["hops"] if h.get("rules")]  # fmt: skip
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


def _after_balancer(hops: list[dict]) -> int:
    """Index nach dem letzten Hop mit Load Balancer (0 ohne): dort beginnt ein neuer Abschnitt."""
    return max((i + 1 for i, h in enumerate(hops) if h.get("lb")), default=0)


def _first_port(text: str | None) -> int | None:
    span = _port_span(text)
    return span[0] if span else None


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
    owners = g.owners().get(ip or "", [])
    # virtuelle Gateway-Adresse (HSRP/VRRP, ADR 0070): den Verkehr nimmt der aktive Router an -
    # der Standby nur, wenn der Zustand keines Partners „aktiv“ ist (nicht gelesen)
    active = [o for o in owners if (o[0], ip) not in g._standby]
    for nkey, iface, _zone in active or owners:
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
