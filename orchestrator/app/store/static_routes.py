"""Statische Routen je Geraet und VRF (1.19.0) - Ansicht, Befunde und Doku der Applikation.

Anwender 2026-11-17 (Prioritaeten 1-7, Nr. 2 „statisches Routing“): gelesen wurden statische
Routen schon (FortiGate `router/static[6]`, Router und L3-Switches, ACI-L3Outs, ADR 0019/0032).
Neu sind die Ansicht je Geraet und VRF (`GET /static-routes`), die Befunde (Pruefungen
`app/checks/analyse/statisch_*.py`, ADR 0039) und in der Doku der Applikation die statischen
Routen, die ihre Wege nutzen (`on_paths`, `for_doc`).

**Quellen** (nur gelesen, `topo_route` bzw. `topo_l3out`):

- FortiGate: die Konfiguration (`table = static`, auch deaktivierte und schwebende); statische
  Eintraege der Routing-Tabelle ohne Eintrag in der Konfiguration (z. B. IPsec `add-route`)
  kommen als „nur Routing-Tabelle“ dazu (`origin = rib`).
- Junos: alle statischen Eintraege aus `show route` (`table = static`, auch inaktive).
- IOS/IOS-XE, NX-OS, Arista: nur die Routing-Tabelle (`rib`, Protokoll static) - inaktive bzw.
  schwebende Routen sind dort nicht lesbar. Solche Kontexte weist die Ansicht aus (`rib_only`);
  doppelte Routen kann es dort nicht geben (dieselbe Route ersetzt die alte).
- ACI: die statischen Routen der L3Outs je Border-Leaf (`topo_l3out.nodes[].static_routes`, ohne
  Next-Hop = Null-Route). Die statischen Eintraege der Routing-Tabelle eines Leafs sind meist
  Bridge-Domain-Subnetze (pervasive) und zaehlen nicht als statische Route.

**Zustand** je Route (`state`) aus der Routing-Tabelle desselben Geraets, Kontexts und derselben
VRF (bzw. VRF im VDOM):

- `active`: steht in der Routing-Tabelle (gleiches Praefix, Next-Hop bzw. Interface, Distanz);
- `backup`: bewusste Backup-Route (siehe unten) - greift, wenn die bessere Route wegfaellt;
- `shadowed` (ueberdeckt): wird nie aktiv bzw. nie genutzt;
- `not_installed`: eingeschaltet, aber nicht in der Routing-Tabelle, und keine bessere Route fuer
  das Praefix erklaert es (Next-Hop bzw. Interface pruefen);
- `disabled`: in der Konfiguration ausgeschaltet;
- `unknown`: nicht pruefbar (Gruende in `unchecked`).

**Backup oder ueberdeckt** - Begruendung: eine schwebende Route (floating static) ist ein
gewolltes Mittel: man setzt ihre Distanz bewusst ueber den Standard des Geraets (FortiGate 10,
Junos 5, IOS/NX-OS/Arista und ACI 1), damit sie nur greift, wenn die bessere Route wegfaellt.
Schlaegt eine bessere Route (kleinere Distanz, gleiches Praefix) eine Route, deren Distanz auf dem
Standard steht, hat das niemand als Backup gemeint - sie ist ueberdeckt. Ebenso ueberdeckt ist
eine Route, die nie greifen kann: die bessere ist eine Blackhole-Route (faellt nie weg) bzw. eine
statische oder angeschlossene Route ueber denselben Next-Hop bzw. dasselbe Interface (faellt nur
weg, wenn auch diese hier nicht mehr geht). Eine Backup-Route ueber denselben Next-Hop wie eine
BGP- bzw. OSPF-Route ist dagegen gewollt (die Sitzung kann ausfallen, die Leitung nicht). Decken
spezifischere Routen das ganze Netz einer aktiven Route ab, wird sie nie genutzt: mit bewusst
erhoehter Distanz ein Backup, sonst ueberdeckt. FortiGate: bei gleicher Distanz geht die kleinere
Prioritaet vor - die anderen sind Backup.

**Befunde** (je Pruefung eine Datei, nur Befunde ihres Typs): `static_nexthop_unreachable` (eine
nicht aktive Route - auch eine Backup-Route - erreicht ihren Next-Hop nicht: kein angeschlossenes
Netz und keine Route im selben VRF, nur ueber die Default-Route bzw. ueber ein anderes Interface
als angegeben), `static_shadowed`, `static_duplicate` (gleiches Praefix, gleicher Next-Hop bzw.
gleiches Interface mehrfach eingeschaltet), `static_blackhole` (Hinweis) und
`static_unchecked`.

**Keine stillen Grenzen** (ADR 0048): was nicht pruefbar ist, steht je Route in `unchecked` und
je Kontext als Hinweis `static_unchecked` - Routing-Tabelle nicht (ganz) lesbar, Ziel ist ein
Adressobjekt (Praefix unbekannt), Next-Hop aus einer SD-WAN-Zone bzw. dynamisch (DHCP/PPPoE),
nicht installierte Route nur ueber ein Interface (sein Zustand ist nicht gelesen). Ein Next-Hop
bei einem nicht gelesenen Geraet ist nur bis ins angeschlossene Netz geprueft - gezaehlt
(`remote`) und je Route ausgewiesen. Kontexte nur mit Routing-Tabelle zaehlt `rib_only`.

Nur lesend gegenueber allen Geraeten; kein neuer Schreibweg.
"""

import ipaddress
import re
from bisect import bisect_left
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import SourceSystem, TopoContext, TopoL3Out, TopoRoute
from app.store import paths
from app.store.topology import MGMT, Table, table_from

STATIC = "static"
CONFIG, RIB, L3OUT = "config", "rib", "l3out"
ACTIVE, BACKUP, SHADOWED, NOT_INSTALLED, DISABLED, UNKNOWN = (
    "active", "backup", "shadowed", "not_installed", "disabled", "unknown",
)  # fmt: skip
STATES = (ACTIVE, BACKUP, SHADOWED, NOT_INSTALLED, DISABLED, UNKNOWN)
# Standard-Distanz je Plattform: darueber = bewusst gesetzt (Backup-Route)
DEFAULT_DISTANCE = {"fortigate": 10, "junos": 5, "aci": 1, "cisco": 1}
UNCHECKED = ("rib", "object", "sdwan", "dynamic", "interface")
_CONNECTED = ("connected", "local")
_NULL = re.compile(r"^(null\d*|discard|blackhole|reject)$", re.I)
_RIB_LABEL = {4: "Routing-Tabelle IPv4", 6: "Routing-Tabelle IPv6"}
_STATIC_LABEL = {4: "statische Routen IPv4", 6: "statische Routen IPv6"}
SAMPLES = 5  # spezifischere Routen bzw. Verbindungen in der Anzeige
DOC_CONNECTIONS = 50  # Verbindungen je Route in der Doku (Anzahl immer vollstaendig)
_PAGE = 500


def unchecked_text(reason: str) -> str:
    """Grund, warum etwas nicht geprueft ist - in der Sprache der Anfrage."""
    return {
        "rib": L("Routing-Tabelle nicht (ganz) lesbar", "routing table not (fully) readable"),
        "object": L("Ziel ist ein Adressobjekt – Präfix unbekannt",
                    "destination is an address object – prefix unknown"),
        "sdwan": L("Next-Hop aus einer SD-WAN-Zone", "next-hop from an SD-WAN zone"),
        "dynamic": L("Gateway dynamisch (DHCP/PPPoE)", "gateway dynamic (DHCP/PPPoE)"),
        "interface": L("nur über ein Interface – sein Zustand ist nicht gelesen",
                       "only via an interface – its state is not read"),
    }.get(reason, reason)  # fmt: skip


def state_text(state: str) -> str:
    return {
        ACTIVE: L("aktiv", "active"),
        BACKUP: L("Backup", "backup"),
        SHADOWED: L("überdeckt", "shadowed"),
        NOT_INSTALLED: L("nicht installiert", "not installed"),
        DISABLED: L("deaktiviert", "disabled"),
        UNKNOWN: L("unbekannt", "unknown"),
    }.get(state, state)


@dataclass
class Static:
    """Eine statische Route (je Next-Hop eine) mit dem Ergebnis der Pruefung."""

    source_system_id: int
    source: str
    node: str  # Geraet bzw. Border-Leaf
    context: str  # VDOM, Geraet[:VRF] bzw. Tenant:VRF
    kind: str  # firewall | vdom | vrf | l3 (wie die Knoten der Weg-Suche)
    label: str
    vrf: Table  # VRF im VDOM (FortiGate) bzw. 0; MGMT = Management-Tabelle
    family: int
    prefix: str
    prefix_len: int
    lo: int
    hi: int
    next_hop: str | None
    interface: str | None
    distance: int | None
    origin: str  # config | rib | l3out
    enabled: bool = True
    priority: int | None = None
    blackhole: bool = False
    target: dict | None = None  # {"kind": object | sdwan | dynamic, "name": ...}
    seq: str | None = None
    comment: str | None = None
    l3out: str | None = None
    # Ergebnis
    state: str = UNKNOWN
    reason: str | None = None
    winner: dict | None = None  # die Route, die gewinnt bzw. vorgeht
    covered_by: list[str] = field(default_factory=list)  # spezifischere Routen (Auszug)
    covered_total: int = 0
    nexthop: dict | None = None  # {"state": ok | unreachable | interface | unchecked, ...}
    unchecked: list[str] = field(default_factory=list)
    duplicate: int = 0  # Anzahl gleicher Routen (mit dieser)
    resolved: list[str] = field(default_factory=list)  # Adressobjekt: Netze in der RIB

    @property
    def node_key(self) -> str:
        return f"{self.source_system_id}|{self.context}"

    @property
    def platform(self) -> str:
        if self.kind in ("firewall", "vdom"):
            return "fortigate"
        if self.kind == "vrf":
            return "aci"
        return "junos" if self.origin == CONFIG else "cisco"

    @property
    def raised(self) -> bool:
        """Distanz bewusst ueber den Standard des Geraets gesetzt (schwebende Route)."""
        return self.distance is not None and self.distance > DEFAULT_DISTANCE[self.platform]

    @property
    def key(self) -> str:
        parts = (self.source_system_id, self.node, self.context, self.vrf, self.prefix,
                 self.next_hop or "", self.interface or "", self.origin, self.seq or "",
                 self.l3out or "", (self.target or {}).get("name") or "")  # fmt: skip
        return "|".join(str(p) for p in parts)

    def where(self) -> str:
        """Geraet bzw. Kontext und VRF: „fw-01 · VRF 2“ (VRF nur, wenn nicht die Standard-VRF)."""
        if self.vrf == MGMT:
            return f"{self.label} · Management"
        return f"{self.label} · VRF {self.vrf}" if self.vrf else self.label

    def via(self) -> str:
        if self.blackhole:
            return "Blackhole"
        if self.target and self.target.get("kind") == "sdwan":
            return f"SD-WAN {self.target.get('name') or ''}".strip()
        if self.next_hop and self.interface:
            return f"{self.next_hop} ({self.interface})"
        return self.next_hop or self.interface or "?"

    def destination(self) -> str:
        if self.target and self.target.get("kind") == "object":
            return str(self.target.get("name"))
        return self.prefix

    def subject(self) -> str:
        return f"{self.where()}: {self.destination()} → {self.via()}"

    def detail(self) -> dict:
        """Angaben fuer einen Befund (gespeichert, deutsch)."""
        out = {
            "node": self.node_key, "label": self.label, "source": self.source,
            "device": self.node, "prefix": self.destination(), "next_hop": self.next_hop,
            "interface": self.interface, "via": self.via(), "distance": self.distance,
            "priority": self.priority, "origin": self.origin, "seq": self.seq,
            "comment": self.comment, "l3out": self.l3out,
            "vrf": self.vrf if self.vrf else None, "state": self.state, "reason": self.reason,
        }  # fmt: skip
        return {k: v for k, v in out.items() if v not in (None, "")}

    def out(self) -> dict:
        """Zeile der Ansicht."""
        return {
            "key": self.key,
            "source_system_id": self.source_system_id,
            "source": self.source,
            "node": self.node,
            "context": self.context,
            "node_key": self.node_key,
            "display": self.kind,
            "label": self.label,
            "vrf": self.vrf,
            "family": self.family,
            "prefix": self.prefix,
            "next_hop": self.next_hop,
            "interface": self.interface,
            "distance": self.distance,
            "priority": self.priority,
            "enabled": self.enabled,
            "origin": self.origin,
            "blackhole": self.blackhole,
            "target": self.target,
            "seq": self.seq,
            "comment": self.comment,
            "l3out": self.l3out,
            "state": self.state,
            "state_text": state_text(self.state),
            "reason": self.reason,
            "winner": self.winner,
            "covered_by": self.covered_by,
            "covered_total": self.covered_total,
            "nexthop": self.nexthop,
            "unchecked": [{"reason": u, "text": unchecked_text(u)} for u in self.unchecked],
            "duplicate": self.duplicate,
            "resolved": self.resolved,
            "issues": issues_of(self),
        }


def issues_of(s: Static) -> list[str]:
    """Befund-Typen einer Route (wie die Pruefungen)."""
    out = []
    if not s.enabled:
        return out
    if (s.nexthop or {}).get("state") == "unreachable":
        out.append("static_nexthop_unreachable")
    if s.state == SHADOWED and s.duplicate < 2:
        out.append("static_shadowed")
    if s.duplicate > 1:
        out.append("static_duplicate")
    if s.blackhole:
        out.append("static_blackhole")
    if s.unchecked:
        out.append("static_unchecked")
    return out


@dataclass(frozen=True)
class _R:
    """Route der Routing-Tabelle (nur die Spalten der Pruefung)."""

    id: int
    prefix: str
    prefix_len: int
    protocol: str | None
    distance: int | None
    next_hops: tuple
    table: Table
    family: int = 4
    lo: int = 0
    hi: int = 0

    def hops(self) -> list[dict]:
        return [dict(h) for h in self.next_hops]

    def via(self) -> str:
        return ", ".join(
            f"{h.get('ip')} ({h.get('interface')})" if h.get("ip") and h.get("interface")
            else (h.get("ip") or h.get("interface") or "?")
            for h in self.hops()
        ) or "?"  # fmt: skip

    def out(self) -> dict:
        return {"prefix": self.prefix, "protocol": self.protocol, "distance": self.distance,
                "via": self.via()}  # fmt: skip


class _Table:
    """Beste Routen der RIB eines Geraets im Kontext im Speicher: je Praefix, je Laenge zum
    Nachschlagen einer IP (hoechstens 33 bzw. 129 Laengen, wie `paths._Rib`) und nach Anfang
    sortiert fuer die Abdeckung eines Netzes; dazu die Interfaces mit angeschlossenem Netz."""

    def __init__(self, rows) -> None:
        self.by_prefix: dict[str, list[_R]] = defaultdict(list)
        self.by_len: dict[tuple[int, int], dict[int, list[_R]]] = defaultdict(dict)
        self.sorted: dict[int, list[_R]] = defaultdict(list)
        self.up: set[tuple[Table, str]] = set()
        for rid, prefix, plen, family, lo, hi, protocol, distance, hops, attrs in rows:
            try:
                start, end = int(lo, 16), int(hi, 16)
            except (TypeError, ValueError):
                continue
            r = _R(rid, prefix, plen, protocol, distance, tuple(dict(h) for h in hops or []),
                   table_from(attrs), family, start, end)  # fmt: skip
            self.by_prefix[prefix].append(r)
            self.by_len[(family, plen)].setdefault(start, []).append(r)
            self.sorted[family].append(r)
            if protocol in _CONNECTED:
                self.up |= {(r.table, h["interface"]) for h in r.next_hops if h.get("interface")}
        for family in self.sorted:
            self.sorted[family].sort(key=lambda r: (r.lo, r.hi, r.id))
        self.starts = {f: [r.lo for r in rows] for f, rows in self.sorted.items()}
        self.lengths = {f: sorted({plen for (fam, plen) in self.by_len if fam == f}, reverse=True)
                        for f in (4, 6)}  # fmt: skip

    def longest(self, ip, vrf: Table, skip: str) -> _R | None:
        bits = 32 if ip.version == 4 else 128
        x = int(ip)
        for length in self.lengths.get(ip.version, []):
            mask = ((1 << length) - 1) << (bits - length) if length else 0
            found = [r for r in self.by_len[(ip.version, length)].get(x & mask, [])
                     if r.table == vrf and r.prefix != skip]  # fmt: skip
            if found:
                return min(found, key=lambda r: (r.distance or 0, r.id))
        return None

    def covered(self, family: int, lo: int, hi: int, plen: int, vrf: Table) -> list[_R] | None:
        """Spezifischere Routen, die [lo, hi] ganz abdecken - an der ersten Luecke None."""
        if plen >= (32 if family == 4 else 128):
            return None
        rows, cursor, found = self.sorted.get(family, []), lo, []
        for r in rows[bisect_left(self.starts.get(family, []), lo) :]:
            if r.lo > hi:
                break
            if r.prefix_len <= plen or r.hi > hi or r.table != vrf:
                continue
            if r.lo > cursor:
                return None  # Luecke: ein Teil des Netzes geht ueber diese Route
            found.append(r)
            cursor = max(cursor, r.hi + 1)
            if cursor > hi:
                return found
        return None


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _addr(text: str | None) -> str | None:
    """Next-Hop ohne Zusatz (`fe80::1%port1`, `10.0.0.1/32`)."""
    if not text:
        return None
    return str(text).split("%")[0].split("/")[0].strip() or None


def _net(prefix: str):
    try:
        return ipaddress.ip_network(prefix, strict=False)
    except ValueError:
        return None


def _null(interface: str | None) -> bool:
    return bool(interface) and bool(_NULL.match(str(interface)))


def _rib_missing(missing: list | None, family: int) -> bool:
    label = _RIB_LABEL[family]
    return any(str(m).startswith(label) for m in missing or [])


def _static_missing(missing: list | None, family: int) -> bool:
    label = _STATIC_LABEL[family]
    return any(str(m).startswith(label) for m in missing or [])


def _hop_match(s: Static, hop: dict) -> bool:
    ip, iface = _addr(hop.get("ip")), hop.get("interface")
    if s.blackhole:
        return not ip and (not iface or _null(iface))
    if s.next_hop:
        return ip == s.next_hop
    return bool(s.interface) and not ip and iface == s.interface


def _distance_match(s: Static, r: _R) -> bool:
    return s.distance is None or r.distance is None or s.distance == r.distance


class Statics:
    """Statische Routen eines Mandanten: `load()` liest sie, `evaluate()` prueft sie gegen die
    Routing-Tabelle. Der Graph der Weg-Suche liefert Bezeichnungen, Knoten und die Adressen der
    gelesenen Geraete (Gegenstelle eines Next-Hops)."""

    def __init__(self, session: Session, tenant_id: int, graph: paths.Graph | None = None):
        self.session = session
        self.tenant_id = tenant_id
        self.graph = graph or paths.Graph(session, tenant_id)
        self.items: list[Static] = []
        self.contexts: dict[tuple[int, str, str], tuple[str, list]] = {}
        # Kontexte nur mit Routing-Tabelle: (Quellsystem, Geraet, Kontext) -> Grund
        self.rib_only: dict[tuple[int, str, str], str] = {}
        self._tables: dict[tuple[int, str, str], _Table] = {}
        self._names: dict[int, str] | None = None
        # ACI: Netze der L3Out-Interfaces je (APIC, Leaf, VRF) - angeschlossen am Border-Leaf
        self._l3nets: dict[tuple[int, str, str], list[tuple]] = defaultdict(list)

    # -- Lesen ------------------------------------------------------------------------------

    def _kind(self, sid: int, context: str) -> str:
        node = self.graph.nodes.get((sid, context))
        return node.kind if node is not None else "vdom"

    def _label(self, sid: int, node: str, context: str, kind: str) -> str:
        if kind == "vrf":
            return f"{node} · VRF {context}"
        return self.graph.label(sid, node, context)[1]

    def _source(self, sid: int) -> str:
        if self._names is None:
            self._names = dict(self.session.execute(
                select(SourceSystem.id, SourceSystem.name)
                .where(SourceSystem.tenant_id == self.tenant_id)).all())  # fmt: skip
        return self._names.get(sid) or f"#{sid}"

    def _make(self, r: TopoRoute, hop: dict, origin: str) -> Static | None:
        net = _net(r.prefix)
        if net is None:
            return None
        attrs = r.attrs or {}
        kind = self._kind(r.source_system_id, r.context)
        target = None
        if attrs.get("dstaddr"):
            target = {"kind": "object", "name": attrs["dstaddr"]}
        elif attrs.get("sdwan-zone"):
            target = {"kind": "sdwan", "name": attrs["sdwan-zone"]}
        elif attrs.get("dynamic-gateway"):
            target = {"kind": "dynamic"}
        iface = hop.get("interface") or None
        return Static(
            source_system_id=r.source_system_id, source=self._source(r.source_system_id),
            node=r.node, context=r.context, kind=kind,
            label=self._label(r.source_system_id, r.node, r.context, kind),
            vrf=table_from(attrs), family=net.version, prefix=str(net),
            prefix_len=net.prefixlen, lo=int(net.network_address),
            hi=int(net.broadcast_address), next_hop=_addr(hop.get("ip")), interface=iface,
            distance=r.distance, origin=origin, enabled=bool(r.best) if origin == CONFIG else True,
            priority=_int(attrs.get("priority")),
            blackhole=bool(attrs.get("blackhole")) or (_null(iface) and not hop.get("ip")),
            target=target, seq=str(attrs["seq-num"]) if attrs.get("seq-num") is not None else None,
            comment=attrs.get("comment") or None,
        )  # fmt: skip

    def load(self) -> "Statics":
        tid = self.tenant_id
        for c in self.session.scalars(select(TopoContext).where(TopoContext.tenant_id == tid)):
            self.contexts[(c.source_system_id, c.node, c.context)] = (c.kind, c.missing or [])
        config: dict[tuple, list[Static]] = defaultdict(list)  # (sid, node, ctx) -> Routen
        for r in self.session.scalars(
            select(TopoRoute)
            .where(TopoRoute.tenant_id == tid, TopoRoute.table == STATIC)
            .order_by(TopoRoute.source_system_id, TopoRoute.node, TopoRoute.context,
                      TopoRoute.family, TopoRoute.lo, TopoRoute.prefix_len, TopoRoute.id)
        ):  # fmt: skip
            s = self._make(r, (r.next_hops or [{}])[0] or {}, CONFIG)
            if s is not None:
                config[(s.source_system_id, s.node, s.context)].append(s)
                self.items.append(s)
        aci = self._l3outs()
        # statische Eintraege der Routing-Tabelle: zur Konfiguration zuordnen, sonst eigene Zeile
        rib: dict[tuple, list[TopoRoute]] = defaultdict(list)
        for r in self.session.scalars(
            select(TopoRoute)
            .where(TopoRoute.tenant_id == tid, TopoRoute.table == "rib",
                   TopoRoute.protocol == STATIC, TopoRoute.best.is_(True))
            .order_by(TopoRoute.source_system_id, TopoRoute.node, TopoRoute.context,
                      TopoRoute.family, TopoRoute.lo, TopoRoute.prefix_len, TopoRoute.id)
        ):  # fmt: skip
            rib[(r.source_system_id, r.node, r.context)].append(r)
        configured = {(sid, node) for sid, node, _ctx in config}  # Junos: Konfiguration da
        for where, rows in rib.items():
            sid, node, ctx = where
            kind = self._kind(sid, ctx)
            if kind == "vrf":
                continue  # ACI: Bridge-Domain-Subnetze bzw. die Routen der L3Outs (oben)
            used = self._consume(config.get(where, []), rows)
            missing = self.contexts.get(where, ("", []))[1]
            for r in rows:
                for i, hop in enumerate(r.next_hops or [{}]):
                    if (r.id, i) in used:
                        continue
                    s = self._make(r, hop or {}, RIB)
                    if s is None:
                        continue
                    s.state, s.reason = ACTIVE, L("in der Routing-Tabelle", "in the routing table")
                    self.items.append(s)
                    # nur die wirksamen lesbar: Router ohne gelesene Konfiguration (IOS, NX-OS,
                    # Arista) bzw. FortiGate ohne Leserecht auf die statischen Routen
                    if kind in ("firewall", "vdom") and _static_missing(missing, s.family):
                        self.rib_only.setdefault(where, "static")
                    elif kind == paths.L3 and (sid, node) not in configured:
                        self.rib_only.setdefault(where, "platform")
        for s in aci:
            self.items.append(s)
        self.items.sort(key=lambda s: (s.source, s.node, s.context, str(s.vrf), s.family, s.lo,
                                       s.prefix_len, s.next_hop or "", s.seq or ""))  # fmt: skip
        return self

    def _l3outs(self) -> list[Static]:
        """Statische Routen der ACI-L3Outs je Border-Leaf (je Next-Hop eine; ohne = Null-Route)."""
        rows = list(self.session.scalars(
            select(TopoL3Out).where(TopoL3Out.tenant_id == self.tenant_id)
            .order_by(TopoL3Out.source_system_id, TopoL3Out.aci_tenant, TopoL3Out.name)
        ))  # fmt: skip
        if not rows:
            return []
        leafs = paths.device_names(self.session, {o.source_system_id for o in rows})
        out = []
        for o in rows:
            sid, vrf = o.source_system_id, o.vrf or ""
            for path in o.paths or []:
                for entry in path.get("ips") or []:
                    try:
                        net = ipaddress.ip_interface(str(entry.get("ip") or "")).network
                    except ValueError:
                        continue
                    for nid in path.get("nodes") or []:
                        name = leafs.get((sid, str(nid))) or f"node-{nid}"
                        self._l3nets[(sid, name, vrf)].append((net, path.get("interface")))
            for n in o.nodes or []:
                name = leafs.get((sid, str(n.get("node")))) or f"node-{n.get('node')}"
                for sr in n.get("static_routes") or []:
                    net = _net(str(sr.get("prefix") or ""))
                    if net is None:
                        continue
                    for nh in sr.get("next_hops") or [None]:
                        out.append(Static(
                            source_system_id=sid, source=self._source(sid), node=name,
                            context=vrf, kind="vrf", label=f"{name} · VRF {vrf}", vrf=0,
                            family=net.version, prefix=str(net), prefix_len=net.prefixlen,
                            lo=int(net.network_address), hi=int(net.broadcast_address),
                            next_hop=_addr(nh), interface=None,
                            distance=_int(sr.get("pref")), origin=L3OUT,
                            blackhole=nh is None, comment=sr.get("descr") or None,
                            l3out=o.name,
                        ))  # fmt: skip
        return out

    def _consume(self, config: list[Static], rows: list[TopoRoute]) -> set[tuple[int, int]]:
        """Statische Eintraege der RIB, die zu einer Route der Konfiguration gehoeren: (Route,
        Next-Hop). Adressobjekt: die Netze des Objekts mit demselben Gateway (`resolved`)."""
        used: set[tuple[int, int]] = set()
        by_prefix: dict[str, list[TopoRoute]] = defaultdict(list)
        for r in rows:
            by_prefix[str(_net(r.prefix) or r.prefix)].append(r)
        for s in config:
            if not s.enabled or (s.target and s.target["kind"] == "object"):
                continue
            for r in by_prefix.get(s.prefix, []):
                if table_from(r.attrs) != s.vrf or not _distance_match(s, _row(r)):
                    continue
                for i, hop in enumerate(r.next_hops or [{}]):
                    if (s.target and s.target["kind"] == "sdwan") or _hop_match(s, hop or {}):
                        used.add((r.id, i))
        for s in config:
            if not s.enabled or not s.target or s.target["kind"] != "object":
                continue
            for r in rows:
                if table_from(r.attrs) != s.vrf or not _distance_match(s, _row(r)):
                    continue
                for i, hop in enumerate(r.next_hops or [{}]):
                    if (r.id, i) not in used and _hop_match(s, hop or {}):
                        used.add((r.id, i))
                        if r.prefix not in s.resolved:
                            s.resolved.append(r.prefix)
        return used

    # -- Routing-Tabelle ---------------------------------------------------------------------

    def _table(self, s: Static) -> "_Table":
        """Beste Routen der RIB des Geraets im Kontext - einmal je Kontext gelesen (eine Abfrage
        statt einer je Route; `evaluate` gibt sie nach dem Kontext wieder frei)."""
        where = (s.source_system_id, s.node, s.context)
        table = self._tables.get(where)
        if table is None:
            rows = self.session.execute(
                select(TopoRoute.id, TopoRoute.prefix, TopoRoute.prefix_len, TopoRoute.family,
                       TopoRoute.lo, TopoRoute.hi, TopoRoute.protocol, TopoRoute.distance,
                       TopoRoute.next_hops, TopoRoute.attrs)
                .where(TopoRoute.tenant_id == self.tenant_id,
                       TopoRoute.source_system_id == s.source_system_id,
                       TopoRoute.node == s.node, TopoRoute.context == s.context,
                       TopoRoute.table == "rib", TopoRoute.best.is_(True))
                .order_by(TopoRoute.id)
            ).all()  # fmt: skip
            table = self._tables[where] = _Table(rows)
        return table

    def exact(self, s: Static) -> list[_R]:
        """Beste Routen der RIB mit genau diesem Praefix in der VRF der Route."""
        return [r for r in self._table(s).by_prefix.get(s.prefix, []) if r.table == s.vrf]

    def longest(self, s: Static, ip) -> _R | None:
        """Laengste beste Route der RIB in der VRF der Route, die die IP enthaelt - ohne Routen
        fuer das Praefix der Route selbst (ein Next-Hop loest nie ueber die eigene Route auf)."""
        return self._table(s).longest(ip, s.vrf, s.prefix)

    def up(self, s: Static) -> set[tuple[Table, str]]:
        """(VRF, Interface) mit einem angeschlossenen Netz in der RIB - das Interface ist aktiv."""
        return self._table(s).up

    def covered(self, s: Static) -> list[_R] | None:
        """Spezifischere Routen der RIB, die das ganze Netz der Route abdecken - sonst None."""
        return self._table(s).covered(s.family, s.lo, s.hi, s.prefix_len, s.vrf)

    # -- Pruefen ----------------------------------------------------------------------------

    def evaluate(self) -> "Statics":
        """Je Kontext: Zustand, Prioritaet (FortiGate), doppelte und Next-Hop - die Routing-Tabelle
        des Kontexts liegt dafuer einmal im Speicher und faellt danach weg."""
        owners = self.graph.owners()
        by_context: dict[tuple, list[Static]] = defaultdict(list)
        for s in self.items:
            by_context[(s.source_system_id, s.node, s.context)].append(s)
        for where, items in by_context.items():
            for s in items:
                self._state(s)
            self._priorities(items)
            self._duplicates(items)
            for s in items:
                self._nexthop(s, owners)
            self._tables.pop(where, None)
        return self

    def _readable(self, s: Static) -> bool:
        found = self.contexts.get((s.source_system_id, s.node, s.context))
        return found is not None and not _rib_missing(found[1], s.family)

    def _state(self, s: Static) -> None:
        if not s.enabled:
            s.state, s.reason = DISABLED, L("in der Konfiguration ausgeschaltet",
                                            "switched off in the configuration")  # fmt: skip
            return
        if s.origin == RIB:
            self._coverage(s)  # steht in der RIB: aktiv, ausser spezifischere decken alles ab
            return
        if s.target and s.target["kind"] == "object":
            s.unchecked.append("object")
            if s.resolved:
                s.state = ACTIVE
                s.reason = L(f"Netze des Objekts in der Routing-Tabelle: "
                             f"{', '.join(s.resolved[:SAMPLES])}",
                             f"networks of the object in the routing table: "
                             f"{', '.join(s.resolved[:SAMPLES])}")  # fmt: skip
            else:
                s.state, s.reason = UNKNOWN, unchecked_text("object")
            return
        if not self._readable(s):
            s.unchecked.append("rib")
            s.state, s.reason = UNKNOWN, unchecked_text("rib")
            return
        rows = self.exact(s)
        sdwan = bool(s.target and s.target["kind"] == "sdwan")
        mine = [r for r in rows if r.protocol == STATIC and _distance_match(s, r)
                and (sdwan or any(_hop_match(s, h) for h in r.hops()))]  # fmt: skip
        if mine:
            s.state, s.reason = ACTIVE, L("in der Routing-Tabelle", "in the routing table")
            self._coverage(s)
            return
        if not rows:
            s.state = NOT_INSTALLED
            s.reason = L("eingeschaltet, aber nicht in der Routing-Tabelle – und keine andere "
                         "Route für das Präfix (Next-Hop bzw. Interface prüfen)",
                         "enabled but not in the routing table – and no other route for the "
                         "prefix (check next-hop or interface)")  # fmt: skip
            return
        w = min(rows, key=lambda r: (r.distance if r.distance is not None else 0, r.id))
        s.winner = w.out()
        self._versus(s, w)

    def _versus(self, s: Static, w: _R) -> None:
        """Nicht in der RIB, eine andere Route fuer dasselbe Praefix gewinnt: Backup oder
        ueberdeckt (Begruendung im Modul-Text)."""
        wd = w.distance if w.distance is not None else 0
        what = f"{w.protocol or '?'}, {L('Distanz', 'distance')} {wd}"
        if s.distance is None or s.distance <= wd:
            s.state = NOT_INSTALLED
            s.reason = L(f"nicht in der Routing-Tabelle – wirksam ist {w.prefix} über {w.via()} "
                         f"({what}); Next-Hop bzw. Interface prüfen",
                         f"not in the routing table – effective is {w.prefix} via {w.via()} "
                         f"({what}); check next-hop or interface")  # fmt: skip
            return
        black = w.protocol == STATIC and all(
            not _addr(h.get("ip")) and (not h.get("interface") or _null(h.get("interface")))
            for h in w.hops())  # fmt: skip
        same = any(_hop_match(s, h) for h in w.hops()) if not s.blackhole else black
        dynamic = w.protocol not in (STATIC, *_CONNECTED)
        std = DEFAULT_DISTANCE[s.platform]
        if black and not s.blackhole:
            s.state = SHADOWED
            s.reason = L(f"die Blackhole-Route {w.prefix} (Distanz {wd}) gewinnt immer – diese "
                         "Route wird nie aktiv",
                         f"the blackhole route {w.prefix} (distance {wd}) always wins – this "
                         "route never becomes active")  # fmt: skip
        elif same and not dynamic:
            s.state = SHADOWED
            s.reason = L(f"gleicher Weg wie die wirksame Route über {w.via()} ({what}) – sie "
                         "übernimmt bei einem Ausfall nichts",
                         f"same path as the effective route via {w.via()} ({what}) – it takes "
                         "over nothing on a failure")  # fmt: skip
        elif s.raised:
            s.state = BACKUP
            s.reason = L(f"Backup-Route: Distanz {s.distance} bewusst über dem Standard {std} – "
                         f"übernimmt, wenn die Route über {w.via()} ({what}) wegfällt",
                         f"backup route: distance {s.distance} deliberately above the default "
                         f"{std} – takes over when the route via {w.via()} ({what}) "
                         "disappears")  # fmt: skip
        else:
            s.state = SHADOWED
            s.reason = L(f"Distanz {s.distance} ist der Standard – die Route über {w.via()} "
                         f"({what}) gewinnt; eine Backup-Route hätte eine bewusst höhere Distanz",
                         f"distance {s.distance} is the default – the route via {w.via()} "
                         f"({what}) wins; a backup route would have a deliberately higher "
                         "distance")  # fmt: skip

    def _coverage(self, s: Static) -> None:
        """Aktive Route: decken spezifischere Routen ihr ganzes Netz ab, wird sie nie genutzt."""
        if not self._readable(s):
            return
        inner = self.covered(s)
        if not inner:
            return
        s.covered_by = [r.prefix for r in inner[:SAMPLES]]
        s.covered_total = len(inner)
        parts = ", ".join(s.covered_by) + (f" (+{len(inner) - SAMPLES})"
                                           if len(inner) > SAMPLES else "")  # fmt: skip
        if s.raised:
            s.state = BACKUP
            std = DEFAULT_DISTANCE[s.platform]
            s.reason = L(f"Backup-Route: spezifischere Routen decken das ganze Netz ab ({parts}) "
                         f"– greift erst, wenn sie wegfallen (Distanz {s.distance} bewusst über "
                         f"dem Standard {std})",
                         f"backup route: more specific routes cover the whole network ({parts}) "
                         f"– takes effect only when they disappear (distance {s.distance} "
                         f"deliberately above the default {std})")  # fmt: skip
        else:
            s.state = SHADOWED
            s.reason = L(f"spezifischere Routen decken das ganze Netz ab ({parts}) – diese Route "
                         "wird nie genutzt",
                         f"more specific routes cover the whole network ({parts}) – this route "
                         "is never used")  # fmt: skip

    def _priorities(self, items: list[Static]) -> None:
        """FortiGate: aktive Routen mit gleichem Praefix und gleicher Distanz - die kleinere
        Prioritaet geht vor, die anderen sind Backup."""
        groups: dict[tuple, list[Static]] = defaultdict(list)
        for s in items:
            if s.state == ACTIVE and s.origin == CONFIG and s.platform == "fortigate":
                groups[(s.source_system_id, s.node, s.context, s.vrf, s.prefix,
                        s.distance)].append(s)  # fmt: skip
        for same in groups.values():
            prio = {s.priority or 0 for s in same}
            if len(prio) < 2:
                continue
            low = min(prio)
            first = next(s for s in same if (s.priority or 0) == low)
            for s in same:
                if (s.priority or 0) > low:
                    s.state = BACKUP
                    s.winner = {"prefix": first.prefix, "protocol": STATIC,
                                "distance": first.distance, "via": first.via()}  # fmt: skip
                    s.reason = L(f"Backup-Route: Priorität {s.priority} – die Route über "
                                 f"{first.via()} mit Priorität {low} geht vor",
                                 f"backup route: priority {s.priority} – the route via "
                                 f"{first.via()} with priority {low} takes precedence")  # fmt: skip

    def _duplicates(self, items: list[Static]) -> None:
        """Gleiche Route mehrfach eingeschaltet (Konfiguration): Praefix, Next-Hop bzw. Interface,
        Ziel (Objekt, SD-WAN-Zone) und Blackhole gleich."""
        groups: dict[tuple, list[Static]] = defaultdict(list)
        for s in items:
            if s.enabled and s.origin in (CONFIG, L3OUT):
                target = (s.target or {}).get("name") or (s.target or {}).get("kind")
                groups[(s.source_system_id, s.node, s.context, s.vrf, s.prefix, s.next_hop,
                        s.interface, s.blackhole, target)].append(s)  # fmt: skip
        for same in groups.values():
            if len(same) > 1:
                for s in same:
                    s.duplicate = len(same)

    def _nexthop(self, s: Static, owners: dict) -> None:
        """Erreicht die Route ihren Next-Hop? Aktive sind aufgeloest (stehen in der RIB); fuer die
        anderen (auch Backup) ein angeschlossenes Netz bzw. eine Route im selben VRF."""
        if not s.enabled or s.blackhole:
            return
        kind = (s.target or {}).get("kind")
        if kind in ("sdwan", "dynamic"):
            if s.state != ACTIVE:
                s.unchecked.append(kind)
                s.nexthop = {"state": "unchecked", "text": unchecked_text(kind)}
            return
        if not s.next_hop:
            if s.interface and s.state != ACTIVE and self._readable(s):
                if (s.vrf, s.interface) in self.up(s):
                    s.nexthop = {"state": "ok", "text": L(
                        f"{s.interface} hat ein angeschlossenes Netz (Interface aktiv)",
                        f"{s.interface} has a connected network (interface up)")}  # fmt: skip
                else:
                    s.unchecked.append("interface")
                    s.nexthop = {"state": "unchecked", "text": unchecked_text("interface")}
            return
        try:
            ip = ipaddress.ip_address(s.next_hop)
        except ValueError:
            return
        found = [k for k, *_ in owners.get(s.next_hop, []) if k != (s.source_system_id,
                                                                    s.context)]  # fmt: skip
        owner = self.graph.nodes[found[0]].label if found else None
        if s.state == ACTIVE:  # aufgeloest - die Weg-Suche zeigt, wie es dahinter weitergeht
            s.nexthop = {
                "state": "ok",
                "text": L(
                    "aufgelöst (Route in der Routing-Tabelle)",
                    "resolved (route in the routing table)",
                ),
            }
            if owner:
                s.nexthop["owner"] = owner
            return
        # nicht aktiv: geprueft bis ins angeschlossene Netz - ohne gelesenes Geraet dahinter
        # nicht weiter (gezaehlt, `remote`)
        self._reach(s, ip, {"owner": owner} if owner else {})
        if (s.nexthop or {}).get("state") == "ok" and not owner:
            s.nexthop["remote"] = True

    def _reach(self, s: Static, ip, base: dict) -> None:
        """Next-Hop einer nicht aktiven Route im selben VRF: angeschlossen, rekursiv ueber eine
        Route, nur ueber die Default-Route bzw. an einem anderen Interface als angegeben."""
        if not self._readable(s):
            s.nexthop = {**base, "state": "unchecked", "text": unchecked_text("rib")}
            return
        if ip.is_link_local and s.interface:
            s.nexthop = {
                **base,
                "state": "ok",
                "text": L(f"Link-local an {s.interface}", f"link-local on {s.interface}"),
            }
            return
        if s.kind == "vrf":  # ACI: im Netz eines L3Out-Interfaces des Leafs
            for net, iface in self._l3nets.get((s.source_system_id, s.node, s.context), []):
                if ip.version == net.version and ip in net:
                    text = L(
                        f"angeschlossen ({net} am L3Out-Interface {iface or '?'})",
                        f"connected ({net} on the L3Out interface {iface or '?'})",
                    )
                    s.nexthop = {**base, "state": "ok", "text": text}
                    return
        r = self.longest(s, ip)
        if r is None:
            text = L(
                "keine Route und kein angeschlossenes Netz zum Next-Hop im selben VRF",
                "no route and no connected network to the next-hop in the same VRF",
            )
            s.nexthop = {**base, "state": "unreachable", "text": text}
        elif r.prefix_len == 0:
            text = L(f"Next-Hop nur über die Default-Route {r.prefix} ({r.via()}) erreichbar – "
                     "kein angeschlossenes Netz",
                     f"next-hop only reachable via the default route {r.prefix} ({r.via()}) – "
                     "no connected network")  # fmt: skip
            s.nexthop = {**base, "state": "unreachable", "text": text, "via": r.out()}
        elif r.protocol in _CONNECTED:
            ifaces = {h.get("interface") for h in r.hops() if h.get("interface")}
            if s.interface and ifaces and s.interface not in ifaces:
                text = L(f"Next-Hop liegt im Netz {r.prefix} an {', '.join(sorted(ifaces))}, "
                         f"nicht an {s.interface}",
                         f"next-hop is in network {r.prefix} on {', '.join(sorted(ifaces))}, "
                         f"not on {s.interface}")  # fmt: skip
                s.nexthop = {**base, "state": "unreachable", "text": text, "via": r.out()}
            else:
                text = L(
                    f"angeschlossen ({r.prefix} an {', '.join(sorted(ifaces)) or '?'})",
                    f"connected ({r.prefix} on {', '.join(sorted(ifaces)) or '?'})",
                )
                s.nexthop = {**base, "state": "ok", "text": text, "via": r.out()}
        else:
            text = L(
                f"über die Route {r.prefix} ({r.protocol}, {r.via()})",
                f"via the route {r.prefix} ({r.protocol}, {r.via()})",
            )
            s.nexthop = {**base, "state": "ok", "text": text, "via": r.out()}

    # -- Zusammenfassung ---------------------------------------------------------------------

    def summary(self, items: list[Static] | None = None) -> dict:
        rows = self.items if items is None else items
        issues: Counter = Counter(t for s in rows for t in issues_of(s))
        unchecked: Counter = Counter(u for s in rows for u in s.unchecked)
        wanted = {(s.source_system_id, s.node, s.context) for s in rows}
        return {
            "total": len(rows),
            "states": {k: v for k, v in Counter(s.state for s in rows).items()},
            "issues": dict(issues),
            "unchecked": [{"reason": k, "text": unchecked_text(k), "count": v}
                          for k, v in sorted(unchecked.items())],
            "remote": sum(1 for s in rows if (s.nexthop or {}).get("remote")),
            "rib_only": [
                {"node_key": f"{sid}|{ctx}", "label": self._label(sid, node, ctx,
                                                                  self._kind(sid, ctx)),
                 "reason": reason, "text": _rib_only_text(reason)}
                for (sid, node, ctx), reason in sorted(self.rib_only.items())
                if (sid, node, ctx) in wanted
            ],
        }  # fmt: skip


def _rib_only_text(reason: str) -> str:
    if reason == "static":
        return L("statische Routen der Konfiguration nicht lesbar – nur die wirksamen aus der "
                 "Routing-Tabelle", "static routes of the configuration not readable – only the "
                 "effective ones from the routing table")  # fmt: skip
    return L("nur die Routing-Tabelle gelesen – inaktive bzw. schwebende statische Routen sind "
             "nicht sichtbar (überdeckt bzw. doppelt dort nicht prüfbar)",
             "only the routing table read – inactive or floating static routes are not visible "
             "(shadowed or duplicate not checkable there)")  # fmt: skip


def _row(r) -> _R:
    return _R(r.id, r.prefix, r.prefix_len, r.protocol, r.distance,
              tuple(dict(h) for h in r.next_hops or []), table_from(r.attrs))  # fmt: skip


def evaluate(session: Session, tenant_id: int, graph: paths.Graph | None = None) -> Statics:
    return Statics(session, tenant_id, graph).load().evaluate()


def of_check(session: Session, tenant_id: int, cx) -> Statics:
    """Fuer die Pruefungen eines Durchgangs einmal gerechnet (`cx.extra`, Graph geteilt)."""
    found = cx.extra.get("static_routes")
    if found is None:
        graph = cx.extra.get("graph") or paths.Graph(session, tenant_id)
        cx.extra["graph"] = graph
        found = cx.extra["static_routes"] = evaluate(session, tenant_id, graph)
    return found


# -- Doku der Applikation: statische Routen auf den Wegen ------------------------------------


def on_paths(found: list[dict]) -> list[dict]:
    """Statische Routen, die die Wege einer Verbindung nutzen - aus der Spur der Weg-Suche
    (`paths.trace`): je Hop die gewaehlte Route mit Protokoll static (nicht bei einer
    Policy-Route und nicht am Ende im Fabric bzw. angeschlossen) und die statische Backup-Route an
    der Abweichung eines Backup-Wegs. Ohne Datenbank; Angaben aus der Konfiguration ergaenzt
    `for_doc`."""
    out: dict[tuple, dict] = {}
    for p in found:
        backup = p.get("kind") == "backup"
        hops = p.get("hops") or []
        for i, h in enumerate(hops):
            last = i == len(hops) - 1 and (p.get("end") or {}).get("kind") in (
                "fabric",
                "connected",
            )
            for part in h.get("parts") or [h]:
                info = part.get("backup")
                if info is not None:  # Abweichung des Backup-Wegs: nicht die aktive Route
                    if info.get("protocol") == STATIC:
                        eg = part.get("egress") or {}
                        hop = {"ip": eg.get("next_hop"), "interface": eg.get("interface")}
                        _ref(out, part, info, [hop], "backup")
                    continue
                route = part.get("route") or {}
                if last or part.get("policy") or route.get("protocol") != STATIC:
                    continue
                _ref(out, part, route, route.get("next_hops") or [],
                     "backup" if backup else "active")  # fmt: skip
    return list(out.values())


def _ref(out: dict, hop: dict, route: dict, next_hops: list, use: str) -> None:
    key = (hop.get("key"), hop.get("vrf", 0), route.get("prefix"))
    ref = out.get(key)
    if ref is None:
        ref = out[key] = {
            "key": hop.get("key"), "source_system_id": hop.get("source_system_id"),
            "context": hop.get("context"), "kind": hop.get("kind"), "label": hop.get("label"),
            "members": list(route.get("members") or hop.get("members") or []),
            "vrf": hop.get("vrf", 0), "prefix": route.get("prefix"),
            "distance": route.get("distance"), "next_hops": [], "use": use,
        }  # fmt: skip
    elif use == "active":
        ref["use"] = "active"
    for nh in next_hops:
        item = {"ip": _addr(nh.get("ip")), "interface": nh.get("interface")}
        if item not in ref["next_hops"]:
            ref["next_hops"].append(item)


def for_doc(session: Session, tenant_id: int, connections: list[dict]) -> list[dict]:
    """Statische Routen auf den Wegen der Applikation: je Route (Geraet, VRF, Praefix, Next-Hop)
    die Angaben aus der Konfiguration (Distanz, Prioritaet, Seq, Kommentar, L3Out) und die
    Verbindungen, die sie nutzen (aktiv bzw. als Backup). Ersetzt je Verbindung `statics` durch
    die Schluessel der Routen. ACI: nur die statischen Routen der L3Outs (die uebrigen statischen
    Eintraege eines Leafs sind Bridge-Domain-Subnetze)."""
    refs = [(c, r) for c in connections for r in c.get("statics") or []]
    if not refs:
        for c in connections:
            c["statics"] = []
        return []
    sids = {r["source_system_id"] for _c, r in refs if r.get("source_system_id") is not None}
    prefixes = sorted({r["prefix"] for _c, r in refs if r.get("prefix")})
    config: dict[tuple, list[TopoRoute]] = defaultdict(list)
    for i in range(0, len(prefixes), _PAGE):
        for row in session.scalars(
            select(TopoRoute).where(
                TopoRoute.tenant_id == tenant_id, TopoRoute.table == STATIC,
                TopoRoute.source_system_id.in_(sids),
                TopoRoute.prefix.in_(prefixes[i : i + _PAGE]))
            .order_by(TopoRoute.id)
        ):  # fmt: skip
            config[(row.source_system_id, row.context, row.prefix)].append(row)
    l3: dict[tuple, list[dict]] = defaultdict(list)
    if any(r.get("kind") == "vrf" for _c, r in refs):
        for o in session.scalars(
            select(TopoL3Out).where(
                TopoL3Out.tenant_id == tenant_id, TopoL3Out.source_system_id.in_(sids)
            )
        ):
            for n in o.nodes or []:
                for sr in n.get("static_routes") or []:
                    net = _net(str(sr.get("prefix") or ""))
                    if net is not None:
                        l3[(o.source_system_id, o.vrf or "", str(net))].append(
                            {**sr, "l3out": o.name, "node": n.get("node")}
                        )
    items: dict[str, dict] = {}
    for c in connections:
        keys: list[str] = []
        for r in c.get("statics") or []:
            for hop in r["next_hops"] or [{}]:
                entry = _doc_entry(r, hop, config, l3)
                if entry is None:
                    continue
                item = items.setdefault(entry["key"], {**entry, "connections": [], "count": 0,
                                                       "use": r["use"]})  # fmt: skip
                if r["use"] == "active":
                    item["use"] = "active"
                if c["key"] not in item["connections"]:
                    item["count"] += 1
                    if len(item["connections"]) < DOC_CONNECTIONS:
                        item["connections"].append(c["key"])
                if entry["key"] not in keys:
                    keys.append(entry["key"])
        c["statics"] = keys
    return sorted(items.values(), key=lambda x: (x["label"] or "", str(x["vrf"]), x["prefix"],
                                                  x.get("next_hop") or ""))  # fmt: skip


def _doc_entry(r: dict, hop: dict, config: dict, l3: dict) -> dict | None:
    sid, ctx, prefix, vrf = r["source_system_id"], r["context"], r["prefix"], r["vrf"]
    ip, iface = _addr(hop.get("ip")), hop.get("interface")
    base = {"source_system_id": sid, "node_key": r["key"], "label": r["label"],
            "context": ctx, "vrf": vrf, "prefix": prefix, "next_hop": ip, "interface": iface,
            "distance": r.get("distance")}  # fmt: skip
    if r.get("kind") == "vrf":
        for sr in l3.get((sid, ctx, prefix), []):
            if ip and ip in {_addr(x) for x in sr.get("next_hops") or []}:
                return {**base, "key": f"{r['key']}|{vrf}|{prefix}|{ip}|l3out|{sr['l3out']}",
                        "origin": L3OUT, "l3out": sr["l3out"],
                        "distance": _int(sr.get("pref")) or r.get("distance"),
                        "comment": sr.get("descr") or None}  # fmt: skip
        return None  # Bridge-Domain-Subnetz (pervasive) - keine statische Route der L3Outs
    for row in config.get((sid, ctx, prefix), []):
        attrs = row.attrs or {}
        if table_from(attrs) != vrf or not row.best:
            continue
        if row.distance is not None and r.get("distance") is not None \
                and row.distance != r.get("distance"):  # fmt: skip
            continue
        h = (row.next_hops or [{}])[0] or {}
        if (ip and _addr(h.get("ip")) == ip) or (not ip and iface and h.get("interface") == iface):
            seq = attrs.get("seq-num")
            return {**base, "key": f"{r['key']}|{vrf}|{prefix}|{ip or iface}|config|{seq or ''}",
                    "origin": CONFIG, "interface": iface or h.get("interface"),
                    "distance": row.distance, "priority": _int(attrs.get("priority")),
                    "seq": str(seq) if seq is not None else None,
                    "comment": attrs.get("comment") or None}  # fmt: skip
    return {**base, "key": f"{r['key']}|{vrf}|{prefix}|{ip or iface}|rib", "origin": RIB}
