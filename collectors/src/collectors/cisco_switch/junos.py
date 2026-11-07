"""Juniper Junos (MX, EX, QFX) -> Netzinventar (0.68.0, ADR 0025).

Gelesen wird die strukturierte Ausgabe (`| display json`): Interfaces mit Zustand, Speed/Duplex,
Beschreibung, logischen Units (VLAN-Tag, IPv4/IPv6-Adressen) und Aggregat (`ae`), LLDP-Nachbarn,
ARP, MAC-Tabelle (`ethernet-switching` bzw. `bridge mac-table` beim MX) und VLANs (EX/QFX).
Eine Unit mit VLAN-Tag wird ein VLAN-Interface am Port (wie ein Subinterface der FortiGate), Unit 0
ohne Tag gehoert zum Port selbst; Netze der Adressen werden Interface-Netze des Inventars.

Seit 0.94.0 auch die IPv6-Nachbarn (`show ipv6 neighbors`, NDP) wie ARP.

Ein MX ist ein Router (Rolle `router`, leitet in der L2-Sicht nicht weiter), EX/QFX Switches.
Interne Interfaces des Systems (`pfe-`, `pfh-`, `cbp`, `dsc`, `lsi`, `tap` …) fallen weg.
"""

import ipaddress
import json
import re

from collectors.cisco_switch import nd
from collectors.common.dto import (
    NetworkInventory,
    NormalizedArp,
    NormalizedDevice,
    NormalizedInterface,
    NormalizedMac,
    NormalizedNeighbor,
    NormalizedSubnet,
    NormalizedVlan,
)

# Interfaces, die zaehlen: Ports, Aggregate, IRB/VLAN, Loopback, Management
_KEEP = re.compile(r"^(ge|xe|et|mge|ae|irb|vlan|lo0|fxp|em|me|reth)[-\d.]*", re.I)
_TAGGED_MAX = 512


def _cmd(name: str) -> str:
    return f"{name} | display json | no-more"


CMD_HARDWARE = _cmd("show chassis hardware")
CMD_INTERFACES = _cmd("show interfaces")
CMD_LLDP = _cmd("show lldp neighbors")
CMD_VLANS = _cmd("show vlans")
CMD_ARP = _cmd("show arp no-resolve")
CMD_MAC_ELS = _cmd("show ethernet-switching table")
CMD_MAC_MX = _cmd("show bridge mac-table")
CMD_ND = _cmd("show ipv6 neighbors")  # 0.94.0: IPv6-Nachbarn (NDP)
# Befehle, die es je nach Plattform nicht gibt (MX ohne ELS, EX ohne Bridge-Domains): kein Hinweis
OPTIONAL = frozenset({CMD_VLANS, CMD_MAC_ELS, CMD_MAC_MX})


def load(text: str) -> dict:
    """JSON der Ausgabe; Zeilen davor (Warnungen) fallen weg, Fehlermeldung = leer."""
    text = (text or "").strip()
    if not text or text.lower().startswith("error"):
        return {}
    m = re.search(r'\{\s*"', text)
    return json.loads(text[m.start() :]) if m else {}


def v(node, key: str):
    """Wert eines Felds: `{"key": [{"data": "x"}]}` -> "x"."""
    items = node.get(key) if isinstance(node, dict) else None
    if isinstance(items, list) and items and isinstance(items[0], dict):
        data = items[0].get("data")
        return data.strip() if isinstance(data, str) else data
    return None


def nodes(obj, key: str) -> list[dict]:
    """Alle Eintraege unter `key`, egal wie tief (auch je Routing-Engine)."""
    out: list[dict] = []
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, val in cur.items():
                if k == key and isinstance(val, list):
                    out += [x for x in val if isinstance(x, dict)]
                else:
                    stack.append(val)
        elif isinstance(cur, list):
            stack.extend(reversed(cur))
    return out


def physical(name: str | None) -> str:
    """`ge-0/0/1.100` -> `ge-0/0/1`, `irb.100 [ge-0/0/1.0]` -> `irb.100`."""
    text = (name or "").split(" ")[0]
    return text if text.startswith(("irb", "vlan")) else text.split(".")[0]


def version_info(text: str) -> dict:
    """Name, Modell und Version aus `show version` (Text)."""

    def field(pattern: str) -> str | None:
        m = re.search(pattern, text or "", re.M)
        return m.group(1).strip() if m else None

    return {"hostname": field(r"^Hostname:\s*(\S+)"), "model": field(r"^Model:\s*(\S+)"),
            "version": field(r"^Junos:\s*(\S+)") or field(r"JUNOS .*?\[(\S+)\]")}  # fmt: skip


def _speed(text: str | None) -> str | None:
    raw = (text or "").strip().lower()
    return None if not raw or raw in ("auto", "unspecified", "unlimited") else raw


def _duplex(text: str | None) -> str | None:
    raw = (text or "").lower()
    return "full" if "full" in raw else "half" if "half" in raw else None


def _vlan_tag(link_address: str | None) -> int | None:
    """`VLAN-Tag [ 0x8100.100 ]` -> 100 (aeusseres Tag)."""
    m = re.search(r"0x8100\.(\d+)", link_address or "")
    return int(m.group(1)) if m else None


def _addresses(unit: dict) -> list[str]:
    out = []
    for fam in nodes(unit, "address-family"):
        if v(fam, "address-family-name") not in ("inet", "inet6"):
            continue
        for addr in nodes(fam, "interface-address"):
            local, dest = v(addr, "ifa-local"), v(addr, "ifa-destination")
            try:
                ip = ipaddress.ip_address(local)
            except (TypeError, ValueError):
                continue
            if ip.is_link_local or ip.is_loopback:
                continue
            length = dest.split("/")[1] if dest and "/" in dest else str(ip.max_prefixlen)
            out.append(f"{ip}/{length}")
    return out


def _bundle(unit: dict) -> str | None:
    for fam in nodes(unit, "address-family"):
        if v(fam, "address-family-name") == "aenet" and v(fam, "ae-bundle-name"):
            return physical(v(fam, "ae-bundle-name"))
    return None


def _mac(value) -> str | None:
    digits = re.sub(r"[^0-9a-fA-F]", "", value if isinstance(value, str) else "")
    if len(digits) != 12:
        return None
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2)).lower()


def inventory(
    host: str, outputs: dict[str, str], notes: list[str] | None = None
) -> NetworkInventory:
    """Ein Junos-Geraet: Ausgaben -> Netzinventar; nicht lesbare JSON-Teile -> Hinweis."""

    def parsed(cmd: str) -> dict:
        try:
            return load(outputs.get(cmd, ""))
        except ValueError as exc:
            if notes is not None:
                notes.append(f"{cmd}: Ausgabe nicht auswertbar ({type(exc).__name__})")
            return {}

    info = version_info(outputs.get("show version", ""))
    name = info["hostname"] or host
    model = info["model"]
    chassis = next(iter(nodes(parsed(CMD_HARDWARE), "chassis")), {})
    serial = v(chassis, "serial-number")
    model = model or v(chassis, "description")
    role = "switch" if re.match(r"(ex|qfx)", (model or "").lower()) else "router"
    device = NormalizedDevice(name=name, role=role, model=model, serial=serial,
                              version=info["version"], mgmt_ip=_ip(host))  # fmt: skip

    ifaces: dict[str, dict] = {}
    subnets: list[NormalizedSubnet] = []
    for phy in nodes(parsed(CMD_INTERFACES), "physical-interface"):
        pname = v(phy, "name") or ""
        if not _KEEP.match(pname):
            continue
        admin, oper = (v(phy, "admin-status") or "").lower(), (v(phy, "oper-status") or "").lower()
        e = ifaces.setdefault(pname, {"name": pname, "tagged": []})
        e.update(status="disabled" if admin == "down" else oper or None,
                 enabled=admin != "down" if admin else None, description=v(phy, "description"),
                 speed=_speed(v(phy, "speed")), duplex=_duplex(v(phy, "link-mode")),
                 media=v(phy, "if-media-type") or v(phy, "link-level-type"))  # fmt: skip
        for unit in nodes(phy, "logical-interface"):
            uname = v(unit, "name") or ""
            if uname.endswith((".16384", ".16385", ".32767")):  # interne Units
                continue
            bundle = _bundle(unit)
            if bundle:
                e["lag"] = bundle
            ips, tag = _addresses(unit), _vlan_tag(v(unit, "link-address"))
            if tag is None and not pname.startswith(("irb", "vlan")):
                e.setdefault("ips", []).extend(ips)  # Unit 0 ohne Tag: Adressen am Port
                target = pname
            else:
                ifaces[uname] = {"name": uname, "kind": "vlan", "parent": pname, "vlan": tag,
                                 "ips": ips, "description": v(unit, "description"),
                                 "status": e.get("status"), "enabled": e.get("enabled"),
                                 "tagged": []}  # fmt: skip
                if tag is not None:
                    e["tagged"].append(tag)
                target = uname
            for ip in ips:
                net = ipaddress.ip_interface(ip).network
                if net.num_addresses > 1:
                    subnets.append(NormalizedSubnet(
                        prefix=str(net), gateway=ip, vlan=tag, context="interface",
                        context_name=target, description=v(unit, "description"),
                    ))  # fmt: skip

    for container in ("irb", "vlan"):  # nur die Units (irb.10) sind Interfaces
        if container in ifaces and not ifaces[container].get("ips"):
            del ifaces[container]
    vlans: dict[int, NormalizedVlan] = {}
    for inst in nodes(parsed(CMD_VLANS), "l2ng-l2ald-vlan-instance-group"):
        vid = _int(v(inst, "l2ng-l2rtb-vlan-tag"))
        if vid is None:
            continue
        vlans.setdefault(
            vid, NormalizedVlan(vid=vid, name=v(inst, "l2ng-l2rtb-vlan-name") or str(vid))
        )
        for member in nodes(inst, "l2ng-l2rtb-vlan-member"):
            port = physical((v(member, "l2ng-l2rtb-vlan-member-interface") or "").rstrip("*"))
            if port not in ifaces:
                continue
            if (v(member, "l2ng-l2rtb-vlan-member-tagness") or "").lower() == "untagged":
                ifaces[port]["untagged"] = vid
            else:
                ifaces[port]["tagged"].append(vid)

    neighbors: list[NormalizedNeighbor] = []
    for n in nodes(parsed(CMD_LLDP), "lldp-neighbor-information"):
        local = v(n, "lldp-local-port-id") or v(n, "lldp-local-interface")
        if not local:
            continue
        port_id = v(n, "lldp-remote-port-id") or ""
        port_desc = v(n, "lldp-remote-port-description")
        remote_port = port_id if re.search(r"[A-Za-z]", port_id) and not _mac(port_id) else (
            port_desc or port_id or None)  # fmt: skip
        neighbors.append(NormalizedNeighbor(
            device=name, port=physical(local), protocol="lldp",
            remote_name=v(n, "lldp-remote-system-name") or v(n, "lldp-remote-chassis-id") or "?",
            remote_port=remote_port,
        ))  # fmt: skip
    first = {n.port: n for n in reversed(neighbors)}

    macs: list[NormalizedMac] = []
    seen: set[tuple] = set()
    for vid, entry in _mac_entries(parsed(CMD_MAC_ELS)) + _mac_entries(parsed(CMD_MAC_MX)):
        mac = _mac(_ending(entry, "mac-address"))
        port = physical(_ending(entry, "mac-logical-interface"))
        if not mac or not port or int(mac[:2], 16) & 1 or (mac, vid, port) in seen:
            continue
        seen.add((mac, vid, port))
        flags = (_ending(entry, "mac-flags") or "").strip().lower()
        kind = "static" if flags.startswith("s") else "dynamic"
        macs.append(NormalizedMac(device=name, mac=mac, port=port, vlan=vid, kind=kind))
    arps: list[NormalizedArp] = []
    for a in nodes(parsed(CMD_ARP), "arp-table-entry"):
        mac, ip = _mac(v(a, "mac-address")), _ip(v(a, "ip-address"))
        if mac and ip:
            iface = (v(a, "interface-name") or "").split(" ")[0] or None
            arps.append(NormalizedArp(device=name, ip=ip, mac=mac, interface=iface))
    rows = [{"address": _ending(a, "neighbor-address"), "mac": _ending(a, "l2-address"),
             "interface": (_ending(a, "interface-name") or "").split(" ")[0],
             "state": _ending(a, "nd-state")}
            for a in nodes(parsed(CMD_ND), "ipv6-nd-entry")]  # fmt: skip
    arps += nd.entries(name, rows)

    interfaces = [_interface(name, e, first.get(e["name"])) for e in ifaces.values()]
    return NetworkInventory(devices=[device], interfaces=interfaces,
                            vlans=sorted(vlans.values(), key=lambda x: x.vid), subnets=subnets,
                            neighbors=neighbors, macs=macs, arps=arps)  # fmt: skip


def _ending(entry: dict, suffix: str):
    """Wert des Felds, dessen Name auf `suffix` endet (`l2ng-l2-mac-address`, `l2-mac-address`)."""
    for key in entry:
        if key.endswith(suffix):
            return v(entry, key)
    return None


def _mac_entries(obj) -> list[tuple[int | None, dict]]:
    """MAC-Eintraege mit VLAN aus `ethernet-switching table` bzw. `bridge mac-table`: Eintrag =
    Knoten mit `…mac-address` und `…mac-logical-interface`; VLAN aus ihm bzw. dem Knoten
    darueber."""
    out: list[tuple[int | None, dict]] = []

    def walk(cur, vid: int | None) -> None:
        if isinstance(cur, list):
            for x in cur:
                walk(x, vid)
            return
        if not isinstance(cur, dict):
            return
        for key in cur:
            if key.endswith(("vlan-id", "bridge-vlan", "vlan-tag")):
                vid = _int(v(cur, key)) or vid
        if any(k.endswith("mac-address") for k in cur) and any(
            k.endswith("mac-logical-interface") for k in cur
        ):
            out.append((vid, cur))
            return
        for val in cur.values():
            walk(val, vid)

    walk(obj, None)
    return out


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _ip(value) -> str | None:
    try:
        return str(ipaddress.ip_address(str(value or "").strip()))
    except ValueError:
        return None


def _kind(name: str, given: str | None) -> str:
    if given:
        return given
    low = name.lower()
    if low.startswith("ae"):
        return "lag"
    if low.startswith(("irb", "vlan")):
        return "vlan"
    if low.startswith("lo"):
        return "loopback"
    return "physical"


def _interface(device: str, e: dict, nb: NormalizedNeighbor | None) -> NormalizedInterface:
    from collectors.cisco_switch.normalize import vlan_ranges

    tagged = sorted(set(e.get("tagged") or []))
    untagged = e.get("untagged")
    if e.get("kind") == "vlan" and e.get("vlan") is not None:  # Unit mit Tag: getaggt am Port
        tagged, untagged = [e["vlan"]], None
    mode = "tagged" if tagged else ("access" if untagged is not None else None)
    return NormalizedInterface(
        device=device, name=e["name"], kind=_kind(e["name"], e.get("kind")),
        enabled=e.get("enabled"), mode=mode, untagged_vlan=untagged if mode == "access" else None,
        tagged_vlans=tagged if len(tagged) <= _TAGGED_MAX else [], lag=e.get("lag"),
        parent=e.get("parent"), ips=list(dict.fromkeys(e.get("ips") or [])),
        description=e.get("description"),
        neighbor=nb.remote_name if nb else None, neighbor_port=nb.remote_port if nb else None,
        status=e.get("status"), speed=e.get("speed"), duplex=e.get("duplex"),
        native_vlan=untagged if mode == "tagged" else None,
        trunk_vlans=vlan_ranges(set(tagged)) if tagged and e.get("kind") != "vlan" else None,
        attrs={"media": e["media"]} if e.get("media") else None,
    )  # fmt: skip
