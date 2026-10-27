"""Arista EOS -> Netzinventar (0.88.x, ADR 0025 Nr. 13).

EOS gibt jede `show`-Ausgabe auch als JSON aus (`| json`, dieselbe Struktur wie eAPI). Gelesen
werden Version und Hostname, Ports mit Zustand, Speed, Duplex und Medium (`show interfaces
status`), Beschreibung und Admin-Status (`description`), Modus und VLANs (`switchport`), Port-
Channel mit Mitgliedern, VLANs, LLDP-Nachbarn (Detail: Port, Management-Adresse, Faehigkeiten),
MAC-Tabelle (nur Unicast an Ports, nicht Router/CPU/VXLAN), ARP und IPv6-Nachbarn aller VRFs.

Portnamen in der Kurzform wie bei Cisco (`Eth1`, `Eth49/1`, `Po10`, `Vl10`): die Gegenstellen
melden per LLDP `Ethernet1`, die Verkabelung vergleicht beides gleich.
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
    NormalizedVlan,
)

CMD_VERSION = "show version | json"
CMD_HOSTNAME = "show hostname | json"
CMD_STATUS = "show interfaces status | json"
CMD_DESCRIPTION = "show interfaces description | json"
CMD_SWITCHPORT = "show interfaces switchport | json"
CMD_VLAN = "show vlan | json"
CMD_PORT_CHANNEL = "show port-channel | json"
CMD_LLDP = "show lldp neighbors detail | json"
CMD_MAC = "show mac address-table | json"
CMD_ARP = "show ip arp vrf all | json"
CMD_ND = nd.EOS
_TAGGED_MAX = 512


def load(text: str) -> dict:
    """JSON der Ausgabe; Text davor (Warnungen) faellt weg, Fehlermeldung bzw. `errors` = leer."""
    text = (text or "").strip()
    if not text or text.startswith("%"):
        return {}
    m = re.search(r"\{", text)
    data = json.loads(text[m.start() :]) if m else {}
    if not isinstance(data, dict) or (set(data) <= {"errors", "messages"} and data.get("errors")):
        return {}
    return data


def port_name(raw: str | None) -> str:
    """`Ethernet49/1`, `Et49/1` -> `Eth49/1`, `Port-Channel10` -> `Po10`, `Vlan10` -> `Vl10`."""
    from collectors.cisco_switch.normalize import short_name

    text = (raw or "").strip()
    if re.match(r"Et\d", text):
        return "Eth" + text[2:]
    return short_name(text)


def version_info(text: str) -> dict:
    """Modell, Version, Seriennummer aus `show version` (Text), falls das JSON fehlt."""

    def field(pattern: str) -> str | None:
        m = re.search(pattern, text or "", re.M)
        return m.group(1).strip() if m else None

    return {"modelName": field(r"^Arista\s+(\S+)"),
            "version": field(r"^Software image version:\s*(\S+)"),
            "serialNumber": field(r"^Serial number:\s*(\S+)")}  # fmt: skip


def _speed(bandwidth) -> str | None:
    """Bandbreite in bit/s -> `1000` bzw. `10G` (wie die Cisco-Switches, Mbit/s)."""
    try:
        mbps = int(bandwidth) // 1_000_000
    except (TypeError, ValueError):
        return None
    if mbps <= 0:
        return None
    return f"{mbps // 1000}G" if mbps >= 1000 and mbps % 1000 == 0 else str(mbps)


def _duplex(value) -> str | None:
    raw = str(value or "").lower()
    return "full" if "full" in raw else "half" if "half" in raw else None


def _media(value) -> str | None:
    text = str(value or "").strip()
    return None if not text or text.lower() in ("not present", "n/a", "unknown") else text


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _names(members) -> list[str]:
    """Mitglieder eines Port-Channels: Schluessel eines Objekts bzw. Liste."""
    if isinstance(members, dict):
        return list(members)
    if isinstance(members, list):
        return [m.get("name") if isinstance(m, dict) else str(m) for m in members if m]
    return []


def _caps(nb: dict) -> list[str]:
    """Faehigkeiten aus LLDP: eingeschaltete (`{"bridge": true, "router": false}`) bzw. Liste."""
    for key in ("enabledCapabilities", "systemCapabilities"):
        value = nb.get(key)
        if isinstance(value, dict):
            on = [k.lower() for k, v in value.items() if v is True or v == "enabled"]
            if on or key == "systemCapabilities":
                return on
        if isinstance(value, list) and value:
            return [str(v).lower() for v in value]
    return []


def _neighbor_rows(obj: dict, key: str) -> list[dict]:
    """ARP bzw. IPv6-Nachbarn: `{"vrfs": {vrf: {key: […]}}}` bzw. `{key: […]}`."""
    rows: list[dict] = []
    scopes = obj.get("vrfs") if isinstance(obj.get("vrfs"), dict) else {None: obj}
    for vrf, data in scopes.items():
        for e in (data or {}).get(key) or []:
            if not isinstance(e, dict):
                continue
            iface = str(e.get("interface") or "").split(",")[0].strip()
            rows.append({"address": e.get("address"),
                         "mac": e.get("hwAddress") or e.get("macAddress"),
                         "interface": port_name(iface) if iface else None,
                         "state": e.get("state"), "vrf": vrf})  # fmt: skip
    return rows


def _ip4(value) -> str | None:
    try:
        return str(ipaddress.IPv4Address(str(value or "").strip()))
    except ValueError:
        return None


def inventory(
    host: str, outputs: dict[str, str], notes: list[str] | None = None
) -> NetworkInventory:
    """Ein Arista-Switch: Ausgaben (JSON) -> Netzinventar; nicht lesbares JSON -> Hinweis."""
    from collectors.cisco_switch.normalize import MAX_MACS, _l2_port, device_name, mac_text

    def parsed(cmd: str) -> dict:
        try:
            return load(outputs.get(cmd, ""))
        except ValueError as exc:
            if notes is not None:
                notes.append(f"{cmd}: Ausgabe nicht auswertbar ({type(exc).__name__})")
            return {}

    ver = parsed(CMD_VERSION) or version_info(outputs.get("show version", ""))
    name = (parsed(CMD_HOSTNAME).get("hostname") or "").strip() or host
    device = NormalizedDevice(
        name=name, role="switch", model=(ver.get("modelName") or "")[:100] or None,
        serial=ver.get("serialNumber") or None, version=ver.get("version") or None,
        mgmt_ip=_ip(host),
    )  # fmt: skip

    ports: dict[str, dict] = {}

    def port(raw: str) -> dict:
        key = port_name(raw)
        return ports.setdefault(key, {"name": key})

    for raw, st in (parsed(CMD_STATUS).get("interfaceStatuses") or {}).items():
        e = port(raw)
        link = str(st.get("linkStatus") or "").lower()
        e.update(status=link or None, speed=_speed(st.get("bandwidth")),
                 duplex=_duplex(st.get("duplex")),
                 media=_media(st.get("interfaceType")))  # fmt: skip
        if st.get("description"):
            e["description"] = st["description"]
        if link == "disabled":
            e["admin_down"] = True
        info = st.get("vlanInformation") or {}
        m = re.match(r"in\s+(Po\S+|Port-Channel\S+)", str(info.get("vlanExplanation") or ""), re.I)
        if m:  # Mitglied eines Port-Channels
            e["lag"] = port_name(m.group(1))
        mode = str(info.get("interfaceMode") or "").lower()
        if mode == "bridged" and _int(info.get("vlanId")) is not None:
            e.setdefault("mode", "access")
            e.setdefault("access", _int(info["vlanId"]))
        elif mode == "trunk":
            e.setdefault("mode", "tagged")
    for raw, d in (parsed(CMD_DESCRIPTION).get("interfaceDescriptions") or {}).items():
        e = port(raw)
        if d.get("description"):
            e["description"] = d["description"]
        state = str(d.get("interfaceStatus") or "").lower()
        if state == "admindown":
            e["admin_down"] = True
        if state and not e.get("status"):
            e["status"] = "disabled" if state == "admindown" else state
    for raw, sp in (parsed(CMD_SWITCHPORT).get("switchports") or {}).items():
        if sp.get("enabled") is False:  # geroutet
            continue
        info = sp.get("switchportInfo") or {}
        mode = str(info.get("mode") or "").lower()
        e = port(raw)
        if mode in ("access", "dot1qtunnel"):
            e.update(mode="access", access=_int(info.get("accessVlanId")))
        elif mode == "trunk":
            e.update(mode="tagged", native=_int(info.get("trunkingNativeVlanId")),
                     allowed=str(info.get("trunkAllowedVlans") or "") or None)  # fmt: skip
    for raw, pc in (parsed(CMD_PORT_CHANNEL).get("portChannels") or {}).items():
        bundle = port(raw)["name"]
        for key in ("activePorts", "inactivePorts", "ports"):
            for member in _names(pc.get(key)):
                port(member)["lag"] = bundle

    vlans: list[NormalizedVlan] = []
    for vid, info in (parsed(CMD_VLAN).get("vlans") or {}).items():
        n = _int(vid)
        if n is not None:
            vlans.append(NormalizedVlan(vid=n, name=str((info or {}).get("name") or n)))

    neighbors: list[NormalizedNeighbor] = []
    lldp = parsed(CMD_LLDP).get("lldpNeighbors") or {}
    if isinstance(lldp, dict):
        items = list(lldp.items())
    else:  # Form ohne Detail: Liste mit `port`, `neighborDevice`, `neighborPort`
        items = [(n.get("port"), {"lldpNeighborInfo": [n]}) for n in lldp if isinstance(n, dict)]
    for raw, info in items:
        local = port_name(raw)
        for nb in (info or {}).get("lldpNeighborInfo") or []:
            nii = nb.get("neighborInterfaceInfo") or {}
            pid = str(nii.get("interfaceId_v2") or nii.get("interfaceId") or nb.get("neighborPort")
                      or "").strip().strip('"')  # fmt: skip
            if str(nii.get("interfaceIdType") or "").lower() == "macaddress" or mac_text(pid):
                pid = str(nii.get("interfaceDescription") or "") or pid
            addrs = [a for a in nb.get("managementAddresses") or [] if isinstance(a, dict)]
            v4 = [a.get("address") for a in addrs if str(a.get("addressType")).lower() == "ipv4"]
            remote_ip = _ip(next(iter(v4 + [a.get("address") for a in addrs]), None))
            desc = str(nb.get("systemDescription") or "").strip()
            neighbors.append(NormalizedNeighbor(
                device=name, port=local, protocol="lldp",
                remote_name=device_name(nb.get("systemName") or nb.get("neighborDevice"))
                or str(nb.get("chassisId") or "?"),
                remote_port=pid or None, remote_ip=remote_ip,
                remote_platform=desc.splitlines()[0][:200] if desc else None,
                remote_caps=_caps(nb), remote_description=desc[:500] or None,
            ))  # fmt: skip
    first = {n.port: n for n in reversed(neighbors)}
    interfaces = [_interface(name, e, first.get(e["name"])) for e in ports.values()]

    macs: list[NormalizedMac] = []
    seen: set[tuple] = set()
    table = (parsed(CMD_MAC).get("unicastTable") or {}).get("tableEntries") or []
    for e in table:
        mac, raw_port = mac_text(e.get("macAddress")), str(e.get("interface") or "")
        p = _l2_port(port_name(raw_port)) if not raw_port.lower().startswith("vx") else None
        vid = _int(e.get("vlanId"))
        if not mac or not p or int(mac[:2], 16) & 1 or (mac, vid, p) in seen:
            continue
        seen.add((mac, vid, p))
        kind = str(e.get("entryType") or "").lower()[:16] or None
        macs.append(NormalizedMac(device=name, mac=mac, port=p, vlan=vid, kind=kind))
    if len(macs) > MAX_MACS and notes is not None:
        notes.append(f"MAC-Tabelle: {len(macs) - MAX_MACS} Eintraege ueber {MAX_MACS} nicht "
                     "uebernommen")  # fmt: skip
    arps: list[NormalizedArp] = []
    for r in _neighbor_rows(parsed(CMD_ARP), "ipV4Neighbors"):
        mac, ip = mac_text(r["mac"]), _ip4(r["address"])
        if mac and ip:
            vrf = r["vrf"] if r["vrf"] not in (None, "default") else None
            arps.append(NormalizedArp(device=name, ip=ip, mac=mac, interface=r["interface"],
                                      vrf=vrf))  # fmt: skip
    arps += nd.entries(name, _neighbor_rows(parsed(CMD_ND), "ipV6Neighbors"))
    return NetworkInventory(devices=[device], interfaces=interfaces, vlans=vlans,
                            neighbors=neighbors, macs=macs[:MAX_MACS], arps=arps)  # fmt: skip


def _ip(value) -> str | None:
    try:
        return str(ipaddress.ip_address(str(value or "").strip())) if value else None
    except ValueError:
        return None


def _kind(name: str) -> str:
    low = name.lower()
    if low.startswith("po"):
        return "lag"
    if low.startswith("vl"):
        return "vlan"
    if low.startswith("lo"):
        return "loopback"
    if low.startswith("vx"):
        return "tunnel"
    return "physical"


def _interface(device: str, e: dict, nb: NormalizedNeighbor | None) -> NormalizedInterface:
    from collectors.cisco_switch.normalize import vlan_ranges, vlan_set

    mode = e.get("mode")
    untagged = native = trunk_text = None
    tagged: list[int] = []
    if mode == "access":
        untagged = e.get("access")
    elif mode == "tagged":
        native = untagged = e.get("native")
        vset = vlan_set(e["allowed"]) if e.get("allowed") else None
        if vset is not None:
            trunk_text = vlan_ranges(vset) or None
            tagged = sorted(vset) if len(vset) <= _TAGGED_MAX else []
    status = e.get("status")
    enabled = None
    if status is not None or e.get("admin_down"):
        enabled = not (status == "disabled" or e.get("admin_down"))
    return NormalizedInterface(
        device=device, name=e["name"], kind=_kind(e["name"]), enabled=enabled, mode=mode,
        untagged_vlan=untagged, tagged_vlans=tagged, lag=e.get("lag"),
        description=e.get("description"),
        neighbor=nb.remote_name if nb else None, neighbor_port=nb.remote_port if nb else None,
        status=status, speed=e.get("speed"), duplex=e.get("duplex"), native_vlan=native,
        trunk_vlans=trunk_text, attrs={"media": e["media"]} if e.get("media") else None,
    )  # fmt: skip
