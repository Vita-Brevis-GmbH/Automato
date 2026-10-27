"""Routing der Router und L3-Switches bis an die Firmengrenze (ADR 0032), strikt lesend.

Bisher endete der Weg an Routern, die Automato nicht liest. Hier liest der Collector der Switches
& Router zusaetzlich das Routing - nur `show`-Befehle der Positivliste (`COMMANDS`, in
`client.ALLOWED`; jeder andere Befehl bricht vor dem Senden ab), keine Konfiguration:

- IOS/IOS-XE: `show ip route` (global), `show ip route vrf *` (alle VRFs), `show ipv6 route`,
  `show ip bgp summary` (Nachbarn, lokales AS), `show bgp ipv4 unicast` (alle Pfade, auch nicht
  beste = Backup-Wege) - ausgewertet mit den ntc-Vorlagen;
- NX-OS: `show ip route vrf all`, `show ipv6 route vrf all` (eigene Auswertung, beide Familien),
  `show ip bgp summary vrf all` (ntc);
- Junos: `show route | display json | no-more` (alle Tabellen mit aktiven und inaktiven
  Eintraegen: RIB, BGP-Pfade, statische Routen) und `show bgp neighbor | display json | no-more`
  (lokales AS, Typ intern/extern und Routing-Instanz je Nachbar - `show bgp summary` nennt das
  lokale AS nicht);
- Arista EOS: `show ip route vrf all | json`, `show ipv6 route vrf all | json`,
  `show ip bgp summary vrf all | json` und `show ip interface | json` (EOS zeigt die eigenen
  Adressen nicht als Route - sie werden zu `local`-Routen wie bei IOS/NX-OS/Junos).

Ergebnis wie bei FortiGate und ACI (`RoutingInventory`): je Geraet und VRF ein Kontext (Art `l3`,
Name = Geraet bzw. `Geraet:VRF`), Routen mit Next-Hops (Interface in der Kurzform wie im
Inventar), Protokoll, Distanz und Metrik, BGP-Nachbarn mit AS und Zustand (lokales AS und Typ in
`attrs`). Management-VRFs (`management`, `mgmt`, `Mgmt-vrf`, `mgmt_junos` …) sind kein Weg fuer
Daten und fallen weg, ebenso Link-local und Multicast.

- **L3-Geraet** ist, wer Routen hat (auch nur angeschlossene) bzw. BGP-Nachbarn - ein L2-Switch
  ohne `ip routing` zeigt nur sein Default-Gateway und bekommt keinen Kontext.
- **Nicht lesbar:** fehlen Rechte bzw. bricht ein Befehl ab, steht der Teil im Kontext unter
  `missing` (gleiche Bezeichnungen wie bei der FortiGate) und als Hinweis im Lauf; gibt es den
  Befehl bzw. die Funktion nicht (`% Invalid input`, BGP aus), bleibt der Teil ohne Hinweis leer.
- **Obergrenze** (`MAX_ROUTES` je Kontext und Tabelle, `client.MAX_OUTPUT` je Befehl): bei einem
  Internet-Fullfeed bleiben alle Routen ausser BGP, die Default-Route und die BGP-Routen in
  privaten Netzen vor den oeffentlichen (kuerzeste Praefixe zuerst); der Rest ist ein Hinweis und
  der Teil gilt als „nicht (ganz) lesbar“ (`… (gekuerzt: n von m)`).
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from collectors.common.dto import (
    NormalizedNextHop,
    NormalizedPeer,
    NormalizedRoute,
    NormalizedRoutingContext,
    RoutingInventory,
)

IOS_RIB = "show ip route"
IOS_RIB_VRF = "show ip route vrf *"
IOS_RIB6 = "show ipv6 route"
IOS_BGP_SUMMARY = "show ip bgp summary"
IOS_BGP = "show bgp ipv4 unicast"
NXOS_RIB = "show ip route vrf all"
NXOS_RIB6 = "show ipv6 route vrf all"
NXOS_BGP_SUMMARY = "show ip bgp summary vrf all"
JUNOS_ROUTE = "show route | display json | no-more"
JUNOS_BGP = "show bgp neighbor | display json | no-more"
EOS_RIB = "show ip route vrf all | json"
EOS_RIB6 = "show ipv6 route vrf all | json"
EOS_BGP_SUMMARY = "show ip bgp summary vrf all | json"
EOS_ADDRESSES = "show ip interface | json"

# Befehle je Plattform (nur lesend); der erste ist die IPv4-RIB
COMMANDS: dict[str, tuple[str, ...]] = {
    "ios": (IOS_RIB, IOS_RIB_VRF, IOS_RIB6, IOS_BGP_SUMMARY, IOS_BGP),
    "nxos": (NXOS_RIB, NXOS_RIB6, NXOS_BGP_SUMMARY),
    "junos": (JUNOS_ROUTE, JUNOS_BGP),
    "eos": (EOS_RIB, EOS_RIB6, EOS_BGP_SUMMARY, EOS_ADDRESSES),
}
ALL = frozenset(c for cmds in COMMANDS.values() for c in cmds)
# Text-Ausgaben, die bei Ueberlaenge gekuerzt ausgewertet werden (JSON waere dann kaputt)
PARTIAL = frozenset({IOS_RIB, IOS_RIB_VRF, IOS_RIB6, IOS_BGP, NXOS_RIB, NXOS_RIB6})

V4, V6, BGP4, PEERS = (
    "Routing-Tabelle IPv4", "Routing-Tabelle IPv6", "BGP-Pfade IPv4", "BGP-Nachbarn",
)  # fmt: skip
# Teil(e) je Befehl fuer „nicht lesbar“ - wie FortiGate (`topo_changes._MISSING`); `global` =
# nur der Kontext der Standard-VRF, sonst alle Kontexte des Geraets; leer = nur Hinweis
PARTS: dict[str, tuple[tuple[str, ...], bool]] = {
    IOS_RIB: ((V4,), True),
    IOS_RIB_VRF: ((), False),  # VRFs unbekannt: nur Hinweis
    IOS_RIB6: ((V6,), True),
    IOS_BGP_SUMMARY: ((PEERS,), True),
    IOS_BGP: ((BGP4,), True),
    NXOS_RIB: ((V4,), False),
    NXOS_RIB6: ((V6,), False),
    NXOS_BGP_SUMMARY: ((PEERS,), False),
    JUNOS_ROUTE: ((V4, V6, BGP4), False),
    JUNOS_BGP: ((PEERS,), False),
    EOS_RIB: ((V4,), False),
    EOS_RIB6: ((V6,), False),
    EOS_BGP_SUMMARY: ((PEERS,), False),
    EOS_ADDRESSES: ((), False),
}
MAX_ROUTES = 20_000  # je Kontext und Tabelle (RIB bzw. BGP-Pfade); Rest = Hinweis
DEFAULT_VRF = "default"
# Management-VRFs: kein Weg fuer Daten (wie die Management-Tabelle der FortiGate)
MGMT_VRFS = frozenset({"management", "mgmt", "mgmt-vrf", "mgmt-intf", "mgmt_junos",
                       "mgmt-junos", "__mgmt__", "oob", "oob-mgmt"})  # fmt: skip
_INTERNAL = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7",
))  # fmt: skip
# Rechte fehlen (Teil „nicht lesbar“) - sonst gilt eine Fehlermeldung als „Funktion aus“
_DENIED = re.compile(r"authori[sz]|permission|denied|not allowed|privilege|insufficient", re.I)
# IOS-Switch ohne `ip routing`: nur das Default-Gateway - kein L3-Geraet
_NO_ROUTING = re.compile(r"^\s*Default gateway is ", re.I)


# -- kleine Helfer ------------------------------------------------------------------------


def context_name(device: str, vrf: str | None) -> str:
    """Kontext eines Routers: das Geraet (Standard-VRF) bzw. `Geraet:VRF`."""
    return device if not vrf or vrf == DEFAULT_VRF else f"{device}:{vrf}"


def _vrf(name: str | None) -> str:
    text = (name or "").strip()
    return DEFAULT_VRF if text.lower() in ("", "default", "global", "master", "inet") else text


def asn(value: Any) -> int | None:
    """AS-Nummer aus `65001`, `"65001"` bzw. asdot `1.10` (= 65546)."""
    text = str(value if value is not None else "").strip()
    if re.fullmatch(r"\d+", text):
        return int(text)
    m = re.fullmatch(r"(\d+)\.(\d+)", text)
    return int(m.group(1)) * 65536 + int(m.group(2)) if m else None


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _addr(value: Any) -> str | None:
    """IP ohne Zone bzw. VRF-Zusatz (`10.0.0.1%default`, `fe80::1%Vlan10`), sonst None."""
    text = str(value or "").strip().split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    return None if ip.is_unspecified else str(ip)


def prefix(value: Any, length: Any = None) -> str | None:
    """CIDR; ohne Laenge die klassische Maske (IOS: `10.0.0.0` = /8, `0.0.0.0` = /0)."""
    text = str(value or "").strip()
    if not text:
        return None
    if "/" not in text and length not in (None, ""):
        text = f"{text}/{length}"
    if "/" not in text:
        try:
            ip = ipaddress.IPv4Address(text)
        except ValueError:
            return None
        first = int(ip) >> 24
        bits = 0 if int(ip) == 0 else 8 if first < 128 else 16 if first < 192 else 24
        text = f"{text}/{bits}"
    try:
        net = ipaddress.ip_network(text, strict=False)
    except ValueError:
        return None
    if net.version == 6 and (net.is_link_local or net.is_multicast):
        return None
    if net.version == 4 and net.is_multicast:
        return None
    return str(net)


def _internal(net_text: str) -> bool:
    net = ipaddress.ip_network(net_text, strict=False)
    return any(net.version == n.version and net.subnet_of(n) for n in _INTERNAL)


def port(name: Any) -> str | None:
    """Interface in der Kurzform des Inventars (`GigabitEthernet0/1` -> `Gi0/1`, `Et49/1` ->
    `Eth49/1`, Junos `ge-0/0/1.0` -> `ge-0/0/1`)."""
    from collectors.cisco_switch.arista import port_name

    text = str(name or "").strip()
    if not text:
        return None
    if re.match(r"^[a-z]{2,4}-\d+/\d+/\d+\.0$|^(lo0|irb|ae\d+)\.0$", text):
        text = text[:-2]  # Junos: Unit 0 gehoert zum Port (wie im Inventar)
    return port_name(text) or None


def denied(error: str | None) -> bool:
    return bool(error and _DENIED.search(error))


def not_routing(text: str) -> bool:
    """Antwort auf den ersten Routing-Befehl: das Geraet routet nicht (nur Default-Gateway,
    Befehl unbekannt) - die weiteren Routing-Befehle entfallen. Fehlende Rechte zaehlen nicht."""
    from collectors.cisco_switch.normalize import cli_error

    error = cli_error(text or "")
    if not (text or "").strip() or _NO_ROUTING.match(text):
        return True
    return bool(error) and not denied(error)


# -- Zwischenform je Geraet ---------------------------------------------------------------


@dataclass
class Route:
    vrf: str
    prefix: str
    protocol: str | None
    table: str = "rib"
    best: bool = True
    distance: int | None = None
    metric: int | None = None
    hops: list[tuple[str | None, str | None]] = field(default_factory=list)
    attrs: dict[str, Any] = field(default_factory=dict)

    def hop(self, ip: str | None, interface: str | None) -> None:
        if self.protocol in ("connected", "local"):
            ip = None  # angeschlossen: kein Next-Hop (NX-OS nennt die eigene Adresse)
        if (ip, interface) not in self.hops:
            self.hops.append((ip, interface))


@dataclass
class Peer:
    vrf: str
    ip: str
    remote_as: int | None = None
    state: str | None = None
    local_ip: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class Parsed:
    routes: list[Route] = field(default_factory=list)
    peers: list[Peer] = field(default_factory=list)
    _index: dict[tuple, Route] = field(default_factory=dict, repr=False)

    def rib(self, vrf, net, protocol, distance=None, metric=None, attrs=None) -> Route:
        """RIB-Eintrag je VRF, Praefix, Protokoll und Distanz - weitere Next-Hops = ECMP."""
        key = (vrf, net, protocol, distance)
        route = self._index.get(key)
        if route is None:
            route = self._index[key] = Route(vrf, net, protocol, distance=distance,
                                             metric=metric, attrs=attrs or {})  # fmt: skip
            self.routes.append(route)
        return route


# -- IOS / IOS-XE (ntc-Vorlagen) ----------------------------------------------------------

_IOS_PROTOCOL = {"C": "connected", "L": "local", "S": "static", "B": "bgp", "O": "ospf",
                 "D": "eigrp", "EX": "eigrp", "R": "rip", "i": "isis", "I1": "isis",
                 "I2": "isis", "IA": "isis", "M": "mobile", "o": "odr", "H": "nhrp",
                 "l": "lisp", "a": "application", "U": "static", "P": "static", "ND": "nd",
                 "NDp": "nd", "OI": "ospf", "OE1": "ospf", "OE2": "ospf", "ON1": "ospf",
                 "ON2": "ospf"}  # fmt: skip


def _ntc(platform: str, command: str, text: str) -> list[dict]:
    from ntc_templates.parse import parse_output

    rows = parse_output(platform=platform, command=command, data=text)
    return [{k.lower(): v for k, v in r.items()} for r in rows]


def ios_rib(text: str, out: Parsed, vrfs_only: bool = False) -> None:
    """`show ip route` bzw. `show ip route vrf *` (Abschnitte „Routing Table: X“)."""
    for r in _ntc("cisco_ios", "show ip route", text):
        vrf = _vrf(r.get("vrf"))
        if vrfs_only and vrf == DEFAULT_VRF:
            continue  # global steht in `show ip route`
        net = prefix(r.get("network"), r.get("prefix_length"))
        if net is None:
            continue
        code = (r.get("protocol") or "").strip()
        proto = _IOS_PROTOCOL.get(code, code.lower() or None)
        distance = _int(r.get("distance"))
        if proto in ("connected", "local") and distance is None:
            distance = 0
        attrs = {"route_type": r["type"]} if r.get("type") else {}
        route = out.rib(vrf, net, proto, distance, _int(r.get("metric")), attrs)
        route.hop(_addr(r.get("nexthop_ip")), port(r.get("nexthop_if")))


def ios_rib6(text: str, out: Parsed) -> None:
    """`show ipv6 route` (global)."""
    for r in _ntc("cisco_ios", "show ipv6 route", text):
        net = prefix(r.get("network"))
        if net is None:
            continue
        code = (r.get("protocol") or "").strip()
        proto = _IOS_PROTOCOL.get(code, code.lower() or None)
        iface = port(r.get("nexthop_if"))
        if iface and iface.lower().startswith("null"):
            continue  # Multicast-/Discard-Eintraege
        route = out.rib(DEFAULT_VRF, net, proto, _int(r.get("distance")), _int(r.get("metric")))
        route.hop(_addr(r.get("nexthop_ip")), iface)


_ORIGIN = {"i": "igp", "e": "egp", "?": "incomplete", "I": "igp", "E": "egp"}


def ios_bgp(text: str, out: Parsed) -> None:
    """`show bgp ipv4 unicast` (Vorlage `show ip bgp`): alle gueltigen Pfade je Praefix."""
    for r in _ntc("cisco_ios", "show ip bgp", text):
        if "*" not in (r.get("status") or ""):
            continue  # unterdrueckt, gedaempft, Historie
        net = prefix(r.get("network"))
        if net is None:
            continue
        nh = _addr(r.get("next_hop"))
        attrs = {k: v for k, v in (
            ("as_path", (r.get("as_path") or "").strip()),
            ("origin", _ORIGIN.get((r.get("origin") or "").strip())),
            ("local_pref", _int(r.get("local_pref"))),
            ("weight", _int(r.get("weight"))),
            ("internal", True if (r.get("route_source") or "").strip() == "i" else None),
        ) if v not in (None, "")}  # fmt: skip
        route = Route(DEFAULT_VRF, net, "bgp", table="bgp",
                      best=(r.get("path_selection") or "").strip() == ">",
                      metric=_int(r.get("metric")), attrs=attrs)  # fmt: skip
        route.hops = [(nh, None)] if nh else []
        out.routes.append(route)


def _state(text: Any) -> tuple[str | None, dict]:
    """Spalte State/PfxRcd: Zahl = Established, sonst der Zustand (`Idle (Admin)`)."""
    raw = str(text or "").strip()
    if not raw:
        return None, {}
    if raw.isdigit():
        return "Established", {}
    attrs = {"admin_status": "down"} if "admin" in raw.lower() else {}
    return raw.split(" (", 1)[0].strip() or raw, attrs


def _peer(out: Parsed, vrf, ip, remote, local_as, state_text, local_ip=None,
          kind: str | None = None) -> None:  # fmt: skip
    """BGP-Nachbar mit lokalem AS und Typ (`ibgp`/`ebgp`: angegeben bzw. aus den AS)."""
    peer_ip = _addr(ip)
    if peer_ip is None:
        return
    state, attrs = _state(state_text)
    remote_as = asn(remote)
    if local_as is not None:
        attrs["local_as"] = local_as
    if kind is None and local_as is not None and remote_as is not None:
        kind = "ibgp" if remote_as == local_as else "ebgp"
    if kind:
        attrs["type"] = kind
    out.peers.append(Peer(_vrf(vrf), peer_ip, remote_as, state, _addr(local_ip), attrs))


def ios_bgp_summary(text: str, out: Parsed) -> None:
    for r in _ntc("cisco_ios", "show ip bgp summary", text):
        _peer(out, DEFAULT_VRF, r.get("bgp_neighbor"), r.get("neighbor_as"),
              asn(r.get("local_as")), r.get("state_or_prefixes_received"))  # fmt: skip


# -- NX-OS --------------------------------------------------------------------------------

_NX_VRF = re.compile(r'^IP(?:v6)? Route Table for VRF "([^"]+)"')
_NX_PREFIX = re.compile(r"^(\S+/\d+), ubest/mbest:")
_NX_VIA = re.compile(
    r"^\s+(\*)?via\s+([^,]+),\s*(?:([^,\[]+?),\s*)?\[(\d+)/(\d+)\],\s*([^,]+),\s*([^,\s]+)"
    r"(?:,\s*(.*))?$"
)
_NX_PROTOCOL = {"direct": "connected", "local": "local", "am": "connected", "hmm": "connected",
                "hsrp": "local", "vrrp": "local", "glbp": "local", "static": "static",
                "discard": "discard"}  # fmt: skip


def nxos_rib(text: str, out: Parsed) -> None:
    """`show ip route vrf all` bzw. `show ipv6 route vrf all`: je Praefix die besten Next-Hops
    (`*via`); HSRP/VRRP-Adressen gelten als eigene, virtuelle Adressen (`local`, `virtual`)."""
    vrf, net = DEFAULT_VRF, None
    for line in text.splitlines():
        m = _NX_VRF.match(line)
        if m:
            vrf, net = _vrf(m.group(1)), None
            continue
        m = _NX_PREFIX.match(line)
        if m:
            net = prefix(m.group(1))
            continue
        m = _NX_VIA.match(line)
        if not m or net is None or not m.group(1):
            continue
        target, iface = m.group(2).strip(), (m.group(3) or "").strip() or None
        raw_proto = m.group(7).strip().lower()
        base = raw_proto.split("-", 1)[0]
        proto = _NX_PROTOCOL.get(base, base)
        attrs: dict[str, Any] = {}
        extra = (m.group(8) or "").lower()
        if proto == "bgp":
            attrs["bgp"] = (
                "ebgp" if "external" in extra else "ibgp" if "internal" in extra else None
            )
            attrs = {k: v for k, v in attrs.items() if v}
        if "%" in target:
            attrs["nexthop_vrf"] = target.split("%", 1)[1]
        if base in ("hsrp", "vrrp", "glbp"):
            attrs["virtual"] = True  # Gateway-Adresse: kann auch der Partner haben
        ip = _addr(target)
        if ip is None and iface is None:
            iface = target  # `*via Null0, [1/0] …`
        route = out.rib(vrf, net, proto, int(m.group(4)), int(m.group(5)), attrs)
        route.hop(ip, port(iface))


def nxos_bgp_summary(text: str, out: Parsed) -> None:
    for r in _ntc("cisco_nxos", "show ip bgp summary", text):
        _peer(out, r.get("vrf"), r.get("bgp_neigh"), r.get("neigh_as"), asn(r.get("local_as")),
              r.get("state_pfxrcd"))  # fmt: skip


# -- Junos (JSON) -------------------------------------------------------------------------

_JUNOS_PROTOCOL = {"direct": "connected", "local": "local", "static": "static", "bgp": "bgp",
                   "ospf": "ospf", "ospf3": "ospf", "is-is": "isis", "rip": "rip",
                   "ripng": "rip", "aggregate": "aggregate", "access-internal": "static",
                   "access": "static"}  # fmt: skip


def _junos_table(name: str) -> tuple[str, int] | None:
    """`inet.0` -> (default, 4), `KUNDE.inet6.0` -> (KUNDE, 6); andere Tabellen (inet.3,
    mpls.0, bgp.l3vpn.0, private) -> None."""
    m = re.fullmatch(r"(?:(.+)\.)?inet(6)?\.0", name or "")
    if not m or (m.group(1) or "").startswith("__"):
        return None
    return _vrf(m.group(1)), 6 if m.group(2) else 4


def junos_routes(text: str, out: Parsed) -> None:
    """`show route`: je Ziel der aktive Eintrag (RIB, alle Next-Hops), alle BGP-Eintraege (auch
    inaktive = Backup-Wege) und alle statischen (auch schwebende)."""
    from collectors.cisco_switch.junos import load, nodes, v

    for table in nodes(load(text), "route-table"):
        where = _junos_table(v(table, "table-name") or "")
        if where is None:
            continue
        vrf = where[0]
        for rt in table.get("rt") or []:
            dest = v(rt, "rt-destination")
            net = prefix(dest, v(rt, "rt-prefix-length"))
            if net is None:
                continue
            for e in rt.get("rt-entry") or []:
                raw = (v(e, "protocol-name") or "").strip().lower()
                proto = _JUNOS_PROTOCOL.get(raw, raw or None)
                active = (v(e, "active-tag") or "").strip() == "*"
                hops = []
                for nh in e.get("nh") or []:
                    iface = v(nh, "via") or v(nh, "nh-local-interface")
                    hops.append((_addr(v(nh, "to")), port(iface)))
                distance, metric = _int(v(e, "preference")), _int(v(e, "metric"))
                if active:
                    route = out.rib(vrf, net, proto, distance, metric)
                    for ip, iface in hops:
                        route.hop(ip, iface)
                if proto == "bgp":
                    path = (v(e, "as-path") or "").replace("AS path:", "").split()
                    origin = _ORIGIN.get(path.pop()) if path and path[-1] in _ORIGIN else None
                    attrs = {k: x for k, x in (
                        ("as_path", " ".join(p for p in path if p.strip("()[]{}").isdigit())),
                        ("origin", origin), ("local_pref", _int(v(e, "local-preference"))),
                        ("learned_from", _addr(v(e, "learned-from"))),
                    ) if x not in (None, "")}  # fmt: skip
                    bgp = Route(vrf, net, "bgp", table="bgp", best=active, distance=distance,
                                metric=metric, attrs=attrs)  # fmt: skip
                    bgp.hops = [(ip, iface) for ip, iface in hops if ip] or hops[:1]
                    out.routes.append(bgp)
                elif proto == "static":
                    st = Route(vrf, net, "static", table="static", best=True, distance=distance)
                    st.hops = hops[:1] or []
                    out.routes.append(st)


def junos_bgp(text: str, out: Parsed) -> None:
    """`show bgp neighbor`: Nachbar, AS beider Seiten, Typ, Zustand und Routing-Instanz."""
    from collectors.cisco_switch.junos import load, nodes, v

    kinds = {"external": "ebgp", "internal": "ibgp"}
    for p in nodes(load(text), "bgp-peer"):
        vrf = v(p, "peer-cfg-rti") or v(p, "peer-fwd-rti") or v(p, "peer-rti")
        _peer(out, vrf, (v(p, "peer-address") or "").split("+", 1)[0], v(p, "peer-as"),
              asn(v(p, "local-as")), v(p, "peer-state"),
              (v(p, "local-address") or "").split("+", 1)[0],
              kinds.get((v(p, "peer-type") or "").lower()))  # fmt: skip


# -- Arista EOS (JSON) --------------------------------------------------------------------

_EOS_PROTOCOL = {"connected": "connected", "static": "static", "ebgp": "bgp", "ibgp": "bgp",
                 "bgp": "bgp", "rip": "rip", "attached": "connected"}  # fmt: skip


def _eos(text: str) -> dict:
    from collectors.cisco_switch.arista import load

    return load(text)


def eos_rib(text: str, out: Parsed) -> None:
    for vrf_name, data in (_eos(text).get("vrfs") or {}).items():
        vrf = _vrf(vrf_name)
        for dest, r in ((data or {}).get("routes") or {}).items():
            net = prefix(dest)
            if net is None or not isinstance(r, dict):
                continue
            low = str(r.get("routeType") or "").lower()
            proto = _EOS_PROTOCOL.get(low) or next(
                (p for p in ("ospf", "isis") if low.startswith(p)), low or None
            )
            attrs = {"bgp": low} if low in ("ebgp", "ibgp") else {}
            distance = _int(r.get("preference"))
            if proto == "connected" and distance is None:
                distance = 0
            route = out.rib(vrf, net, proto, distance, _int(r.get("metric")), attrs)
            for via in r.get("vias") or []:
                if isinstance(via, dict):
                    route.hop(_addr(via.get("nexthopAddr")), port(via.get("interface")))


def eos_bgp_summary(text: str, out: Parsed) -> None:
    for vrf_name, data in (_eos(text).get("vrfs") or {}).items():
        data = data or {}
        local_as = asn(data.get("asn"))
        for ip, p in (data.get("peers") or {}).items():
            if isinstance(p, dict):
                _peer(out, vrf_name, ip, p.get("asn"), local_as, p.get("peerState"))


def eos_addresses(text: str, out: Parsed) -> None:
    """Eigene Adressen (primaer, sekundaer, virtuell) als `local`-Route je VRF."""
    for name, i in (_eos(text).get("interfaces") or {}).items():
        if not isinstance(i, dict):
            continue
        addr = i.get("interfaceAddress") or {}
        if isinstance(addr, list):  # aeltere EOS: Liste
            addr = addr[0] if addr else {}
        entries = [
            (e, False)
            for e in [addr.get("primaryIp"), *(addr.get("secondaryIpsOrderedList") or [])]
        ]
        entries.append((addr.get("virtualIp"), True))  # VARP: auf beiden Peers
        for e, virtual in entries:
            ip = _addr((e or {}).get("address")) if isinstance(e, dict) else None
            if ip is None or not (e.get("maskLen") or 0):
                continue
            attrs = {"virtual": True} if virtual else {}
            route = out.rib(_vrf(i.get("vrf")), f"{ip}/32", "local", 0, attrs=attrs)
            route.hop(None, port(name))


PARSERS: dict[str, Callable[[str, Parsed], None]] = {
    IOS_RIB: ios_rib,
    IOS_RIB_VRF: lambda text, out: ios_rib(text, out, vrfs_only=True),
    IOS_RIB6: ios_rib6,
    IOS_BGP_SUMMARY: ios_bgp_summary,
    IOS_BGP: ios_bgp,
    NXOS_RIB: nxos_rib,
    NXOS_RIB6: nxos_rib,
    NXOS_BGP_SUMMARY: nxos_bgp_summary,
    JUNOS_ROUTE: junos_routes,
    JUNOS_BGP: junos_bgp,
    EOS_RIB: eos_rib,
    EOS_RIB6: eos_rib,
    EOS_BGP_SUMMARY: eos_bgp_summary,
    EOS_ADDRESSES: eos_addresses,
}


# -- Geraet -> Kontexte ---------------------------------------------------------------------


def _interfaces(routes: list[Route]) -> None:
    """Next-Hops ohne Interface (IOS-BGP, statisch nur mit Gateway): das Interface des
    angeschlossenen Netzes, in dem der Next-Hop liegt (laengstes, je VRF)."""
    nets: dict[str, list[tuple[Any, str]]] = {}
    for r in routes:
        if r.table == "rib" and r.protocol == "connected":
            iface = next((i for _ip, i in r.hops if i), None)
            if iface:
                nets.setdefault(r.vrf, []).append((ipaddress.ip_network(r.prefix), iface))
    for found in nets.values():
        found.sort(key=lambda x: -x[0].prefixlen)
    for r in routes:
        if not any(ip and not iface for ip, iface in r.hops):
            continue
        hops = []
        for ip, iface in r.hops:
            if ip and not iface:
                addr = ipaddress.ip_address(ip)
                iface = next((i for n, i in nets.get(r.vrf, []) if addr in n), None)
            hops.append((ip, iface))
        r.hops = hops


def _cap(routes: list[Route], limit: int) -> tuple[list[Route], int]:
    """Hoechstens `limit` Routen: alle ausser BGP und die Default-Route zuerst, dann BGP in
    privaten Netzen vor oeffentlichen, kuerzeste Praefixe zuerst. Rueckgabe: (behalten, weg)."""
    if len(routes) <= limit:
        return routes, 0

    def rank(r: Route) -> tuple:
        net = ipaddress.ip_network(r.prefix)
        first = r.protocol != "bgp" or net.prefixlen == 0
        return (not first, not _internal(r.prefix), net.prefixlen, int(net.network_address))

    ordered = sorted(routes, key=rank)
    return ordered[:limit], len(routes) - limit


def device(name: str, platform: str, outputs: dict[str, str], errors: dict[str, str] | None,
           cut: set[str] | None = None, notes: list[str] | None = None,
           limit: int = MAX_ROUTES) -> RoutingInventory:  # fmt: skip
    """Routing eines Geraets aus den Ausgaben seiner Routing-Befehle. `errors` = Befehle, die
    abbrachen bzw. eine Fehlermeldung brachten; `cut` = gekuerzte Ausgaben (zu gross)."""
    from collectors.cisco_switch.normalize import cli_error

    inv = RoutingInventory()
    cmds = COMMANDS.get(platform)
    if not cmds:
        return inv
    errors, cut = errors or {}, cut or set()
    parsed = Parsed()
    missing: dict[bool, list[str]] = {True: [], False: []}  # global -> Teile
    hints: list[str] = []
    for cmd in cmds:
        labels, only_global = PARTS[cmd]
        text = outputs.get(cmd) or ""
        problem = errors.get(cmd)
        error = None if problem else cli_error(text)
        if problem or denied(error):  # abgebrochen bzw. keine Rechte: nicht lesbar
            missing[only_global] += labels
            hints.append(f"{cmd}: {problem or error}")
            continue
        if error or not text.strip() or _NO_ROUTING.match(text):
            continue  # Befehl bzw. Funktion gibt es nicht (BGP aus, IPv6 aus): ohne Hinweis
        try:
            PARSERS[cmd](text, parsed)
        except Exception as exc:  # noqa: BLE001 - Ausgabe passt nicht: Teil nicht lesbar
            missing[only_global] += labels
            hints.append(f"{cmd}: Ausgabe nicht auswertbar ({type(exc).__name__})")
            continue
        if cmd in cut:
            missing[only_global] += [f"{label} (gekuerzt: Ausgabe zu gross)" for label in labels]
            hints.append(f"{cmd}: Ausgabe zu gross - gekuerzt ausgewertet")
    routes = [r for r in parsed.routes if r.vrf.lower() not in MGMT_VRFS]
    peers = [p for p in parsed.peers if p.vrf.lower() not in MGMT_VRFS]
    _interfaces(routes)
    # L3-Geraet: Routen bzw. BGP-Nachbarn; ohne beides nur, wenn das Routing nicht lesbar war
    if not any(r.table == "rib" for r in routes) and not peers and not any(missing.values()):
        return inv
    vrfs = {r.vrf for r in routes} | {p.vrf for p in peers}
    if missing[True] or not vrfs:
        vrfs.add(DEFAULT_VRF)
    for vrf in sorted(vrfs, key=lambda x: (x != DEFAULT_VRF, x)):
        ctx = context_name(name, vrf)
        here = missing[False] + (missing[True] if vrf == DEFAULT_VRF else [])
        gone: list[str] = list(dict.fromkeys(here))
        mine = [r for r in routes if r.vrf == vrf]
        for table, family in [(t, f) for t in ("rib", "bgp", "static") for f in (4, 6)]:
            part = [r for r in mine if r.table == table and (":" in r.prefix) == (family == 6)]
            kept, dropped = _cap(part, limit)
            if dropped:
                what = BGP4 if table == "bgp" else V4 if family == 4 else V6
                gone.append(f"{what} (gekuerzt: {len(kept)} von {len(part)})")
                hints.append(f"{ctx}: {what} gekuerzt - {dropped} von {len(part)} Routen ueber "
                             f"{limit} nicht uebernommen")  # fmt: skip
            inv.routes += [NormalizedRoute(
                node=name, context=ctx, prefix=r.prefix, table=r.table, protocol=r.protocol,
                best=r.best, distance=r.distance, metric=r.metric,
                next_hops=[NormalizedNextHop(ip=ip, interface=i) for ip, i in r.hops],
                attrs=r.attrs,
            ) for r in kept]  # fmt: skip
        for p in peers:
            if p.vrf == vrf:
                inv.peers.append(NormalizedPeer(node=name, context=ctx, peer_ip=p.ip,
                                                local_ip=p.local_ip, remote_as=p.remote_as,
                                                state=p.state, attrs=p.attrs))  # fmt: skip
        inv.contexts.append(NormalizedRoutingContext(node=name, context=ctx, kind="l3",
                                                     missing=gone))  # fmt: skip
    if notes is not None:
        notes += [f"Routing: {h}" for h in hints]
    return inv


def normalize(raw: dict) -> RoutingInventory:
    """Routing aller gelesenen Geraete eines Quellsystems (auch leer: der Orchestrator ersetzt
    damit den Stand, z. B. wenn ein Router nicht mehr routet). Nach `normalize.normalize` (dort
    steht der Geraetename je Switch)."""
    inv = RoutingInventory()
    for sw in raw.get("switches") or []:
        part = device(sw.get("name") or sw["host"], sw["platform"], sw.get("outputs") or {},
                      sw.get("routing_errors"), set(sw.get("routing_cut") or []),
                      sw.setdefault("notes", []))  # fmt: skip
        inv.contexts += part.contexts
        inv.routes += part.routes
        inv.peers += part.peers
    return inv


def summary(inv: RoutingInventory) -> str | None:
    """Teil der Lauf-Meldung: L3-Geraete und Kontexte (None ohne)."""
    if not inv.contexts:
        return None
    devices = len({c.node for c in inv.contexts})
    return f"Routing: {devices} L3-Geraete, {len(inv.contexts)} Kontexte, {len(inv.routes)} Routen"
