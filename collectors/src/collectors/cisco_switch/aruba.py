"""HPE Aruba CX (AOS-CX, z. B. 6100, 6300, 8360) -> Netzinventar (0.88.x, ADR 0025 Nr. 13).

Ausgewertet mit den ntc-Vorlagen `aruba_aoscx`: `show system` (Name, Produkt, Seriennummer,
Version), `show interface` (Zustand, Admin-Status, Beschreibung, Speed, Duplex, VLAN-Modus,
Access-/Native-VLAN, erlaubte VLANs, Mitglieder der LAGs), `show vlan`, `show lldp neighbor-info
detail`, `show mac-address-table` und `show arp all-vrfs`. Die Vorlage von `show interface` nimmt
hoechstens sechs Eintraege der erlaubten VLANs bzw. acht Mitglieder eines LAG - beide Listen
kommen darum aus dem Block des Interfaces selbst. Die IPv6-Nachbarn (`show ipv6 neighbors
all-vrfs`) kennt keine Vorlage: gleiche Tabellenform wie ARP, ausgewertet mit `nd.table`.

Portnamen wie auf dem Switch (`1/1/1`, `lag1`, `vlan10`) - so melden ihn auch die Gegenstellen per
LLDP. ArubaOS-Switch (ProCurve) liest Automato nicht (`client.UnsupportedDevice`).
"""

import ipaddress
import re

from collectors.cisco_switch import nd
from collectors.common.dto import (
    NetworkInventory,
    NormalizedArp,
    NormalizedDevice,
    NormalizedInterface,
    NormalizedMac,
    NormalizedNeighbor,
    NormalizedVlan,
)

CMD_SYSTEM = "show system"
CMD_INTERFACE = "show interface"
CMD_VLAN = "show vlan"
CMD_LLDP = "show lldp neighbor-info detail"
CMD_MAC = "show mac-address-table"
CMD_ARP = "show arp all-vrfs"
CMD_ND = nd.AOSCX
_TEMPLATES = (CMD_SYSTEM, CMD_INTERFACE, CMD_VLAN, CMD_LLDP, CMD_MAC, CMD_ARP)
_BLOCK = re.compile(r"^\s*(?:Interface|Aggregate)\s+(\S+)\s+is\b", re.M)
_PORT = re.compile(r"^(\d+/\d+/\d+|lag\d+)$", re.I)
_TAGGED_MAX = 512


def version_info(text: str) -> str | None:
    """`Version : FL.10.10.1010` aus `show version`."""
    m = re.search(r"^\s*Version\s*:\s*(\S+)", text or "", re.M)
    return m.group(1) if m else None


def blocks(text: str) -> dict[str, str]:
    """`show interface` -> Text je Interface (`Interface 1/1/1 is up` bzw. `Aggregate lag1 …`)."""
    marks = list(_BLOCK.finditer(text or ""))
    out: dict[str, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.setdefault(m.group(1), text[m.start() : end])
    return out


def _line(block: str, label: str) -> str | None:
    m = re.search(rf"^\s*{label}\s*:\s*(.*?)\s*$", block or "", re.M | re.I)
    return m.group(1) if m and m.group(1) else None


def _speed(text: str | None) -> str | None:
    """`1000 Mb/s` -> `1000`, `10 Gb/s` -> `10G`, `25000 Mb/s` -> `25000`."""
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*([MG])b", text or "", re.I)
    if not m:
        return None
    return f"{m.group(1)}G" if m.group(2).upper() == "G" else m.group(1)


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _ip(value) -> str | None:
    try:
        return str(ipaddress.ip_address(str(value or "").strip())) if value else None
    except ValueError:
        return None


def _kind(name: str) -> str:
    low = name.lower()
    if low.startswith("lag"):
        return "lag"
    if low.startswith("vlan"):
        return "vlan"
    if low.startswith("loopback"):
        return "loopback"
    return "physical"


def inventory(
    host: str, outputs: dict[str, str], notes: list[str] | None = None
) -> NetworkInventory:
    """Ein Aruba-CX-Switch: Ausgaben -> Netzinventar; passt eine Vorlage nicht, bleibt der Teil
    leer und steht als Hinweis im Lauf."""
    from collectors.cisco_switch.normalize import MAX_MACS, device_name, mac_text, parse

    p: dict[str, list[dict]] = {}
    for cmd in _TEMPLATES:
        try:
            p[cmd] = parse("aoscx", cmd, outputs.get(cmd, ""))
        except Exception as exc:  # noqa: BLE001 - Vorlage passt nicht: Teil leer, Hinweis im Lauf
            p[cmd] = []
            if notes is not None:
                notes.append(f"{cmd}: Ausgabe nicht auswertbar ({type(exc).__name__})")
    system = (p[CMD_SYSTEM] or [{}])[0]
    name = (system.get("hostname") or "").strip() or host
    device = NormalizedDevice(
        name=name, role="switch", model=(system.get("product") or "").strip()[:100] or None,
        serial=system.get("serial") or None,
        version=system.get("version") or version_info(outputs.get("show version", "")),
        mgmt_ip=_ip(host),
    )  # fmt: skip

    text = outputs.get(CMD_INTERFACE, "")
    raw_blocks = blocks(text)
    ports: dict[str, dict] = {}
    for r in p[CMD_INTERFACE]:
        pname = r.get("interface") or ""
        blk = raw_blocks.get(pname, "")
        admin, oper = (r.get("link_admin") or "").lower(), (r.get("link_status") or "").lower()
        e = ports.setdefault(pname, {"name": pname})
        e.update(status="disabled" if admin == "down" else oper or None,
                 enabled=admin != "down" if admin else None,
                 description=(r.get("interface_desc") or "").strip() or None,
                 speed=_speed(r.get("speed")), duplex=(r.get("duplex") or "").lower() or None,
                 media=(r.get("if_type") or "").strip() or None)  # fmt: skip
        vmode = (r.get("vlan_mode") or "").lower()
        if vmode == "access":
            e.update(mode="access", untagged=_int(r.get("vlan_access")))
        elif vmode.startswith("native") or vmode == "trunk":
            allowed = _line(blk, "Allowed VLAN List") or ",".join(r.get("vlan_trunk") or [])
            e.update(
                mode="tagged",
                allowed=allowed.replace(" ", ""),
                native=_int(r.get("vlan_native")) if vmode != "native-tagged" else None,
            )
        members = _line(blk, "Aggregated-interfaces")
        names = members.replace(",", " ").split() if members else r.get("aggregated_interfaces")
        for member in names or []:
            ports.setdefault(member, {"name": member})["lag"] = pname

    vlans = []
    for r in p[CMD_VLAN]:
        vid = _int(r.get("vlan_id"))
        if vid is not None:
            vlans.append(NormalizedVlan(vid=vid, name=(r.get("vlan_name") or str(vid)).strip()))

    neighbors: list[NormalizedNeighbor] = []
    for r in p[CMD_LLDP]:
        local = (r.get("local_interface") or "").strip()
        if not local:
            continue
        port_id = (r.get("neighbor_port_id") or "").strip()
        if mac_text(port_id):  # Port-ID als MAC: die Beschreibung des Ports gilt
            port_id = (r.get("neighbor_interface") or "").strip() or port_id
        desc = (r.get("neighbor_description") or "").strip()
        neighbors.append(NormalizedNeighbor(
            device=name, port=local, protocol="lldp",
            remote_name=device_name(r.get("neighbor_name")) or (r.get("chassis_id") or "?"),
            remote_port=port_id or None, remote_ip=_ip(r.get("mgmt_address")),
            remote_platform=desc[:200] or None, remote_description=desc[:500] or None,
            remote_caps=[c.lower() for c in re.split(r"[\s,]+", r.get("capabilities") or "") if c],
        ))  # fmt: skip
    first = {n.port: n for n in reversed(neighbors)}
    interfaces = [_interface(name, e, first.get(e["name"])) for e in ports.values()]

    macs: list[NormalizedMac] = []
    seen: set[tuple] = set()
    for r in p[CMD_MAC]:
        mac, port = mac_text(r.get("mac_address")), (r.get("port") or "").strip()
        vid = _int(r.get("vlan_id"))
        if not mac or not _PORT.match(port) or int(mac[:2], 16) & 1 or (mac, vid, port) in seen:
            continue
        seen.add((mac, vid, port))
        macs.append(NormalizedMac(device=name, mac=mac, port=port, vlan=vid,
                                  kind=(r.get("type") or "").lower()[:16] or None))  # fmt: skip
    if len(macs) > MAX_MACS and notes is not None:
        notes.append(f"MAC-Tabelle: {len(macs) - MAX_MACS} Eintraege ueber {MAX_MACS} nicht "
                     "uebernommen")  # fmt: skip
    arps: list[NormalizedArp] = []
    for r in p[CMD_ARP]:
        mac, ip = mac_text(r.get("mac_address")), _ip(r.get("ip_address"))
        if mac and ip:
            vrf = (r.get("vrf") or "").strip()
            arps.append(NormalizedArp(device=name, ip=ip, mac=mac,
                                      interface=(r.get("port_id") or "").strip() or None,
                                      vrf=None if vrf in ("", "default") else vrf))  # fmt: skip
    arps += nd.entries(name, nd.table(outputs.get(CMD_ND, "")))
    return NetworkInventory(devices=[device], interfaces=interfaces, vlans=vlans,
                            neighbors=neighbors, macs=macs[:MAX_MACS], arps=arps)  # fmt: skip


def _interface(device: str, e: dict, nb: NormalizedNeighbor | None) -> NormalizedInterface:
    from collectors.cisco_switch.normalize import vlan_ranges, vlan_set

    mode = e.get("mode")
    untagged = native = trunk_text = None
    tagged: list[int] = []
    if mode == "access":
        untagged = e.get("untagged")
    elif mode == "tagged":
        native = untagged = e.get("native")
        vset = vlan_set(e.get("allowed")) if e.get("allowed") else None
        if vset is not None:
            trunk_text = vlan_ranges(vset) or None
            tagged = sorted(vset) if len(vset) <= _TAGGED_MAX else []
    return NormalizedInterface(
        device=device, name=e["name"], kind=_kind(e["name"]), enabled=e.get("enabled"),
        mode=mode, untagged_vlan=untagged, tagged_vlans=tagged, lag=e.get("lag"),
        description=e.get("description"),
        neighbor=nb.remote_name if nb else None, neighbor_port=nb.remote_port if nb else None,
        status=e.get("status"), speed=e.get("speed"), duplex=e.get("duplex"), native_vlan=native,
        trunk_vlans=trunk_text, attrs={"media": e["media"]} if e.get("media") else None,
    )  # fmt: skip
