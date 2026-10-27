"""Gelesene SNMP-Tabellen -> Netzinventar wie beim SSH-Collector (ADR 0033).

Dieselben DTOs wie `cisco_switch/normalize.py` (Geraet, Ports, VLANs, Nachbarn, MAC, ARP inkl.
IPv6-Nachbarn): Verkabelung, Endpunkte und L2-Sicht im Orchestrator bleiben unveraendert.

- **Geraet**: `sysName` (ohne Domaene, wie der Hostname per SSH), Hersteller grob aus
  `sysObjectID` (Enterprise-Nummer), Modell/Seriennummer/Software aus dem Chassis der
  ENTITY-MIB, sonst aus `sysDescr`. Juniper MX = Rolle `router` (wie per SSH).
- **Ports** (IF-MIB): Ethernet, LAG und VLAN-Interfaces; Name `ifName` (sonst `ifDescr`) in der
  Kurzform, Status aus Admin-/Oper-Status, Speed aus `ifHighSpeed`, Duplex aus der EtherLike-MIB,
  Beschreibung `ifAlias`, MAC `ifPhysAddress` (`attrs.mac`), Port-Channel aus der
  IEEE8023-LAG-MIB.
- **VLANs je Port** (Q-BRIDGE-MIB bzw. IEEE8021-Q-BRIDGE-MIB): Egress-/Untagged-Ports und PVID je
  Bridge-Port (`dot1dBasePortIfIndex`) -> Access (ein VLAN ungetaggt) bzw. Trunk (getaggte VLANs,
  Native VLAN = ungetaggtes), erlaubte VLANs als Bereiche wie per SSH.
- **MAC-Tabelle** (`dot1qTpFdbTable`, sonst `dot1dTpFdbTable`): nur gelernte bzw. statische
  Unicast-Eintraege an Ports, nicht die eigenen; FDB -> VLAN ueber `dot1qVlanFdbId`.
- **ARP und IPv6-Nachbarn** (`ipNetToPhysicalTable`, sonst `ipNetToMediaTable` fuer IPv4): ohne
  eigene Adressen (Typ `local` bzw. MAC eines eigenen Interfaces), ohne unvollstaendige Eintraege.
- **Nachbarn**: LLDP-MIB (Systemname, Port, Management-Adresse, Faehigkeiten) und CISCO-CDP-MIB;
  am Port gilt CDP vor LLDP wie per SSH.
"""

import ipaddress
import re

from collectors.cisco_switch import nd
from collectors.cisco_switch.normalize import (
    _DEFAULT_VLANS,
    TAGGED_MAX,
    _kind,
    device_name,
    mac_text,
    short_name,
    vlan_ranges,
)
from collectors.common.dto import (
    NetworkInventory,
    NormalizedArp,
    NormalizedDevice,
    NormalizedInterface,
    NormalizedMac,
    NormalizedNeighbor,
    NormalizedVlan,
)

# Enterprise-Nummer (IANA) aus sysObjectID -> Hersteller
VENDORS = {
    9: "Cisco", 2636: "Juniper", 30065: "Arista", 47196: "Aruba", 14823: "Aruba", 11: "HPE",
    25506: "HPE", 674: "Dell", 6027: "Dell", 1916: "Extreme", 1991: "Brocade", 4526: "Netgear",
    12356: "Fortinet", 6486: "Alcatel-Lucent", 2011: "Huawei", 890: "Zyxel", 14988: "MikroTik",
    41112: "Ubiquiti", 259: "Edgecore", 8072: "Net-SNMP",
}  # fmt: skip
# IANAifType: Ethernet (auch alte Typen), LAG, VLAN-Interfaces; propVirtual (53) nur mit Namen
# eines Port-Channels bzw. einer SVI (Cisco)
_ETHERNET = {6, 62, 69, 117}
_LAG, _VIRTUAL = 161, 53
_VLAN_TYPES = {135, 136}
_OPER = {1: "up", 2: "down", 3: "testing", 5: "dormant", 6: "notpresent", 7: "down"}
_DUPLEX = {2: "half", 3: "full"}
# LLDP-Faehigkeiten (BITS, erstes Bit = hoechstes Bit des ersten Bytes) und CDP (Bitmaske)
_LLDP_CAPS = ("other", "repeater", "bridge", "wlan-access-point", "router", "telephone",
              "docsis", "station-only", "c-vlan", "s-vlan", "two-port-mac-relay")  # fmt: skip
_CDP_CAPS = ((0x01, "router"), (0x02, "trans-bridge"), (0x04, "source-route-bridge"),
             (0x08, "switch"), (0x10, "host"), (0x20, "igmp"), (0x40, "repeater"),
             (0x80, "phone"), (0x400, "two-port-mac-relay"))  # fmt: skip
_FDB_SKIP = {2, 4}  # invalid, self (eigene Adresse)
_FDB_KIND = {3: "dynamic", 5: "static"}


# -- Werte ----------------------------------------------------------------------------------


def text(value) -> str | None:
    """OCTET STRING bzw. Zahl -> Text ohne NUL und Rand."""
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    out = str(value).replace("\x00", "").strip()
    return out or None


def number(value) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def mac(value) -> str | None:
    """6 Bytes bzw. Text (`00:50:56:…`, `0x005056…`) -> `00:50:56:01:00:01`."""
    if isinstance(value, bytes):
        if len(value) != 6:
            return None
        return ":".join(f"{b:02x}" for b in value)
    raw = text(value) or ""
    return mac_text(raw[2:] if raw.lower().startswith("0x") else raw)


def port_list(value) -> set[int]:
    """PortList (je Bit ein Bridge-Port, Port 1 = hoechstes Bit des ersten Bytes) -> Ports."""
    if not isinstance(value, bytes):
        return set()
    return {i * 8 + bit + 1 for i, byte in enumerate(value) for bit in range(8)
            if byte & (0x80 >> bit)}  # fmt: skip


def _parts(index: str) -> list[int]:
    try:
        return [int(p) for p in index.split(".") if p != ""]
    except ValueError:
        return []


_INET_SIZE = {1: 4, 2: 16, 3: 8, 4: 20}  # InetAddressType ipv4, ipv6, ipv4z, ipv6z (mit Zone)


def _address(family: int, rest: list[int]) -> str | None:
    """InetAddress aus dem Index (mit bzw. ohne Laenge davor): 1/3 IPv4, 2/4 IPv6 (z = Zone,
    faellt weg)."""
    total = _INET_SIZE.get(family)
    if total is None:
        return None
    if len(rest) == total + 1 and rest[0] == total:
        rest = rest[1:]
    if len(rest) != total or any(not 0 <= b <= 255 for b in rest):
        return None
    return str(ipaddress.ip_address(bytes(rest[: 4 if family in (1, 3) else 16])))


def _octets_ip(value) -> str | None:
    if isinstance(value, bytes) and len(value) in (4, 16):
        return str(ipaddress.ip_address(value))
    return None


def _bits(value, names: tuple[str, ...]) -> list[str]:
    if not isinstance(value, bytes):
        return []
    return [n for i, n in enumerate(names) if i // 8 < len(value)
            and value[i // 8] & (0x80 >> (i % 8))]  # fmt: skip


def _speed(value) -> str | None:
    """`ifHighSpeed` (Mbit/s) -> `100`, `1000`, `10G`, `25G` (wie per SSH); 0 = unbekannt."""
    mbps = number(value)
    if not mbps:
        return None
    return f"{mbps // 1000}G" if mbps >= 10_000 and not mbps % 1000 else str(mbps)


def _cdp_caps(value) -> list[str]:
    if isinstance(value, bytes):
        value = int.from_bytes(value[:4], "big") if value else 0
    mask = number(value) or 0
    return [name for bit, name in _CDP_CAPS if mask & bit]


# -- Geraet ---------------------------------------------------------------------------------


def vendor(sys_object_id) -> str | None:
    m = re.match(r"^\.?1\.3\.6\.1\.4\.1\.(\d+)", text(sys_object_id) or "")
    return VENDORS.get(int(m.group(1))) if m else None


def _version(descr: str) -> str | None:
    for pattern in (r"JUNOS ([\w.\-]+)", r"EOS version (\S+)", r"\b([A-Z]{2}\.\d+\.\d+\.\d+)\b",
                    r"[Vv]ersion:?\s+([\w.()\-]+)"):  # fmt: skip
        m = re.search(pattern, descr)
        if m:
            return m.group(1).rstrip(".,")
    return None


def _model(descr: str) -> str | None:
    for pattern in (r"Juniper Networks, Inc\. (\S+)", r"running on an? Arista Networks (\S+)"):
        m = re.search(pattern, descr)
        if m:
            return m.group(1).rstrip(",")
    return None


def _host_name(value, entry: str) -> str:
    """sysName ohne Domaene (wie der Hostname per SSH), sonst die Adresse."""
    from collectors.snmp.client import split_host

    name = text(value) or ""
    try:
        ipaddress.ip_address(name)
        return name
    except ValueError:
        return name.split(".", 1)[0] or split_host(entry)[0]


def _mgmt_ip(entry: str) -> str | None:
    """Management-IP aus der Adresse im Lesebereich (`host`, `host:port`, `[v6]:port`)."""
    from collectors.snmp.client import split_host

    try:
        return str(ipaddress.ip_address(split_host(entry)[0]))
    except ValueError:
        return None


# -- Inventar -------------------------------------------------------------------------------


class _Data:
    """Tabellen eines Switches: {Spalte: {Index: Wert}}."""

    def __init__(self, raw: dict):
        self.scalars = raw.get("scalars") or {}
        self.tables = raw.get("tables") or {}
        self.entity = raw.get("entity") or {}

    def col(self, name: str) -> dict[str, object]:
        return self.tables.get(name) or {}

    def inner(self, name: str, count: int = 1) -> dict[str, object]:
        """IEEE8021-Tabellen: Komponente vorne im Index weglassen (erste gewinnt)."""
        out: dict[str, object] = {}
        for idx, value in self.col(name).items():
            out.setdefault(".".join(idx.split(".")[count:]), value)
        return out


def _bridge_ports(d: _Data) -> dict[int, int]:
    """Bridge-Port -> ifIndex (Q-BRIDGE bzw. IEEE 802.1Q); fehlt die Tabelle: gleich ifIndex."""
    out = {}
    for table in (d.col("dot1dBasePortIfIndex"), d.inner("ieee8021BridgeBasePortIfIndex")):
        for idx, value in table.items():
            port, ifindex = number(idx), number(value)
            if port is not None and ifindex:
                out.setdefault(port, ifindex)
    return out


def _vlan_tables(d: _Data):
    """(Namen je VLAN, Egress-Ports je VLAN, Untagged-Ports je VLAN, PVID je Bridge-Port)."""
    names = {number(k): text(v) for k, v in d.col("dot1qVlanStaticName").items()}
    egress = {number(k): port_list(v) for k, v in d.col("dot1qVlanStaticEgressPorts").items()}
    untagged = {number(k): port_list(v)
                for k, v in d.col("dot1qVlanStaticUntaggedPorts").items()}  # fmt: skip
    if not egress:  # aktueller Stand (TimeMark.VLAN) statt der Konfiguration
        for k, v in d.col("dot1qVlanCurrentEgressPorts").items():
            egress.setdefault(number(k.split(".")[-1]), port_list(v))
        for k, v in d.col("dot1qVlanCurrentUntaggedPorts").items():
            untagged.setdefault(number(k.split(".")[-1]), port_list(v))
    pvid = {number(k): number(v) for k, v in d.col("dot1qPvid").items()}
    if not (names or egress or pvid):  # IEEE 802.1Q-2011 (Komponente vorne im Index)

        def ieee(column: str, convert) -> dict:
            return {number(k): convert(v) for k, v in d.inner(f"ieee8021QBridge{column}").items()}

        names = ieee("VlanStaticName", text)
        egress = ieee("VlanStaticEgressPorts", port_list)
        untagged = ieee("VlanStaticUntaggedPorts", port_list)
        pvid = ieee("Pvid", number)
    drop = {None, *_DEFAULT_VLANS}
    return ({k: v for k, v in names.items() if k not in drop},
            {k: v for k, v in egress.items() if k not in drop},
            {k: v for k, v in untagged.items() if k not in drop},
            {k: v for k, v in pvid.items() if k is not None})  # fmt: skip


def _include(if_type: int | None, name: str) -> str | None:
    """Art des Interfaces bzw. None (nicht uebernommen: Loopback, Tunnel, Units, CPU …)."""
    if if_type in _ETHERNET:
        return "physical"
    if if_type == _LAG:
        return "lag"
    if if_type in _VLAN_TYPES:
        return "vlan"
    if if_type == _VIRTUAL and _kind(name) in ("lag", "vlan"):
        return _kind(name)
    if if_type is None and _kind(name) in ("physical", "lag", "vlan"):
        return _kind(name)
    return None


def _port_mode(egress: set[int], untagged: set[int], pvid: int | None) -> dict:
    """VLANs eines Bridge-Ports -> Modus, ungetaggtes/Native VLAN, erlaubte VLANs."""
    if not egress:
        return {}
    tagged = egress - untagged
    native = pvid if pvid in untagged else (min(untagged) if untagged else None)
    if not tagged:
        return {"mode": "access", "untagged_vlan": native}
    return {"mode": "tagged", "untagged_vlan": native, "native_vlan": native,
            "trunk_vlans": vlan_ranges(egress),
            "tagged_vlans": sorted(egress) if len(egress) <= TAGGED_MAX else []}  # fmt: skip


def _lldp_ports(d: _Data, by_name: dict[str, int], bridge: dict[int, int],
                if_names: dict[int, str]) -> dict[str, int]:  # fmt: skip
    """lldpLocPortNum -> ifIndex: Port-ID bzw. Beschreibung = Name eines Interfaces, sonst die
    Nummer als ifIndex bzw. als Bridge-Port."""
    out: dict[str, int] = {}
    ids, descs = d.col("lldpLocPortId"), d.col("lldpLocPortDesc")
    kinds = d.col("lldpLocPortIdSubtype")
    for num in set(ids) | set(descs) | set(kinds):
        found = None
        port_id, kind = ids.get(num), number(kinds.get(num))
        for value in (port_id if kind != 3 else None, descs.get(num)):
            key = short_name(text(value) or "").lower()
            if key and key in by_name:
                found = by_name[key]
                break
        if found is None and kind == 7 and number(text(port_id)) in if_names:
            found = number(text(port_id))  # lokale Kennung = ifIndex (z. B. Junos)
        out[num] = found
    for num, found in list(out.items()):
        if found is None:
            n = number(num)
            out[num] = n if n in if_names else bridge.get(n)
    return {k: v for k, v in out.items() if v}


def _lldp(d: _Data, device: str, local: dict[str, int], if_names: dict[int, str]):
    rows: dict[tuple[str, str], dict] = {}
    fields = {"lldpRemChassisIdSubtype": "chassis_kind", "lldpRemChassisId": "chassis",
              "lldpRemPortIdSubtype": "port_kind", "lldpRemPortId": "port",
              "lldpRemPortDesc": "port_desc", "lldpRemSysName": "name",
              "lldpRemSysDesc": "descr", "lldpRemSysCapSupported": "caps_supported",
              "lldpRemSysCapEnabled": "caps"}  # fmt: skip
    for column, key in fields.items():
        for idx, value in d.col(column).items():
            parts = idx.split(".")
            if len(parts) >= 3:
                rows.setdefault((parts[1], parts[2]), {})[key] = value
    addrs: dict[tuple[str, str], list[str]] = {}
    for idx in d.col("lldpRemManAddrIfSubtype"):
        p = _parts(idx)
        if len(p) >= 5:
            ip = _address({1: 1, 2: 2}.get(p[3], 0), p[4:])
            if ip:
                addrs.setdefault((str(p[1]), str(p[2])), []).append(ip)
    out = []
    for (port_num, rem), r in sorted(rows.items(), key=lambda kv: (_parts(kv[0][0]), kv[0][1])):
        ifindex = local.get(port_num)
        port = if_names.get(ifindex) if ifindex else None
        if not port:
            continue
        chassis = mac(r.get("chassis")) if number(r.get("chassis_kind")) == 4 else None
        remote = device_name(text(r.get("name"))) or chassis or text(r.get("chassis")) or "?"
        port_kind, desc = number(r.get("port_kind")), text(r.get("port_desc"))
        remote_port = mac(r.get("port")) if port_kind == 3 else text(r.get("port"))
        if port_kind in (3, 7) and desc:
            remote_port = desc  # MAC bzw. lokale Nummer: Name steht in der Beschreibung
        ips = sorted(addrs.get((port_num, rem), []), key=lambda ip: (":" in ip, ip))
        caps = _bits(r.get("caps"), _LLDP_CAPS) or _bits(r.get("caps_supported"), _LLDP_CAPS)
        out.append(NormalizedNeighbor(
            device=device, port=port, protocol="lldp", remote_name=remote,
            remote_port=remote_port, remote_ip=ips[0] if ips else None, remote_caps=caps,
            remote_description=(text(r.get("descr")) or "")[:500] or None,
        ))  # fmt: skip
    return out


def _cdp(d: _Data, device: str, if_names: dict[int, str]):
    rows: dict[tuple[int, int], dict] = {}
    fields = {"cdpCacheAddressType": "addr_type", "cdpCacheAddress": "addr",
              "cdpCacheDeviceId": "name", "cdpCacheDevicePort": "port",
              "cdpCachePlatform": "platform", "cdpCacheCapabilities": "caps"}  # fmt: skip
    for column, key in fields.items():
        for idx, value in d.col(column).items():
            p = _parts(idx)
            if len(p) == 2:
                rows.setdefault((p[0], p[1]), {})[key] = value
    out = []
    for (ifindex, _), r in sorted(rows.items()):
        port = if_names.get(ifindex)
        if not port:
            continue
        ip = _octets_ip(r.get("addr")) if number(r.get("addr_type")) in (1, 20) else None
        out.append(NormalizedNeighbor(
            device=device, port=port, protocol="cdp",
            remote_name=device_name(text(r.get("name"))) or "?",
            remote_port=text(r.get("port")), remote_ip=ip,
            remote_platform=text(r.get("platform")), remote_caps=_cdp_caps(r.get("caps")),
        ))  # fmt: skip
    return out


def _fdb(d: _Data) -> list[tuple[int, str, int | None, int | None]]:
    """(Bridge-Port, MAC, VLAN, Status) aus Q-BRIDGE, IEEE 802.1Q bzw. BRIDGE-MIB."""
    fdb_vlans: dict[int, set[int]] = {}
    for idx, value in d.col("dot1qVlanFdbId").items():
        vid, fid = number(idx.split(".")[-1]), number(value)
        if vid is not None and fid is not None:
            fdb_vlans.setdefault(fid, set()).add(vid)

    def vlan_of(fid: int | None) -> int | None:
        if fid is None:
            return None
        if fdb_vlans:
            vids = fdb_vlans.get(fid) or set()
            return next(iter(vids)) if len(vids) == 1 else None
        return fid if 1 <= fid <= 4094 else None

    out = []
    for port_col, status_col, strip, with_fdb in (
        ("dot1qTpFdbPort", "dot1qTpFdbStatus", 0, True),
        ("ieee8021QBridgeTpFdbPort", "ieee8021QBridgeTpFdbStatus", 1, True),
        ("dot1dTpFdbPort", "dot1dTpFdbStatus", 0, False),
    ):
        ports = d.inner(port_col, strip) if strip else d.col(port_col)
        status = d.inner(status_col, strip) if strip else d.col(status_col)
        for idx, value in ports.items():
            p = _parts(idx)
            if len(p) != (7 if with_fdb else 6) or any(not 0 <= b <= 255 for b in p[-6:]):
                continue
            address = ":".join(f"{b:02x}" for b in p[-6:])
            out.append((number(value), address, vlan_of(p[0]) if with_fdb else None,
                        number(status.get(idx))))  # fmt: skip
        if out:
            break
    return out


def inventory(host: str, raw: dict, notes: list[str] | None = None) -> NetworkInventory:
    """Ein Switch: gelesene Tabellen (`tasks.read`) -> Netzinventar."""
    from collectors.cisco_switch import normalize as ssh

    d = _Data(raw)
    descr = text(d.scalars.get("sysDescr")) or ""
    made_by = vendor(d.scalars.get("sysObjectID"))
    name = _host_name(d.scalars.get("sysName"), host)
    model = text(d.entity.get("model")) or _model(descr)
    role = "switch"
    if made_by == "Juniper" and re.search(r"\bmx\d+", (model or descr).lower()):
        role = "router"  # wie per SSH (ADR 0025 Nr. 11)
    device = NormalizedDevice(
        name=name, role=role, model=model, serial=text(d.entity.get("serial")),
        version=text(d.entity.get("version")) or _version(descr), mgmt_ip=_mgmt_ip(host),
    )  # fmt: skip

    if_names: dict[int, str] = {}  # alle ifIndex (auch SVIs fuer ARP)
    by_name: dict[str, int] = {}
    ports: dict[int, dict] = {}
    names, descrs, types = d.col("ifName"), d.col("ifDescr"), d.col("ifType")
    for idx in sorted(set(names) | set(descrs), key=lambda i: _parts(i)):
        ifindex = number(idx)
        raw_name = text(names.get(idx)) or text(descrs.get(idx))
        if ifindex is None or not raw_name:
            continue
        pname = short_name(raw_name)
        if_names[ifindex] = pname
        for alias in (pname, short_name(text(descrs.get(idx)) or "")):
            if alias:
                by_name.setdefault(alias.lower(), ifindex)
        kind = _include(number(types.get(idx)), pname)
        if kind:
            ports[ifindex] = {"name": pname, "kind": kind}
    own_macs: set[str] = set()
    for idx, value in d.col("ifPhysAddress").items():
        m, ifindex = mac(value), number(idx)
        if m and m != "00:00:00:00:00:00":
            own_macs.add(m)
            if ifindex in ports:
                ports[ifindex]["mac"] = m
    for column, key in (("ifAdminStatus", "admin"), ("ifOperStatus", "oper"),
                        ("ifHighSpeed", "speed"), ("ifAlias", "alias"),
                        ("dot3StatsDuplexStatus", "duplex")):  # fmt: skip
        for idx, value in d.col(column).items():
            ifindex = number(idx)
            if ifindex in ports:
                ports[ifindex][key] = value
    for column in ("dot3adAggPortSelectedAggID", "dot3adAggPortAttachedAggID"):
        for idx, value in d.col(column).items():  # Attached zuletzt: gilt vor Selected
            member, agg = number(idx), number(value)
            if member in ports and agg and agg != member and agg in if_names:
                ports[member]["lag"] = if_names[agg]

    bridge = _bridge_ports(d)
    if_of_port = bridge or {i: i for i in if_names}  # ohne Zuordnung: Bridge-Port = ifIndex
    vlan_names, egress, untagged, pvid = _vlan_tables(d)
    for bport, ifindex in if_of_port.items():
        if ifindex not in ports:
            continue
        e = {vid for vid, members in egress.items() if bport in members}
        u = {vid for vid, members in untagged.items() if bport in members}
        ports[ifindex].update(_port_mode(e, u, pvid.get(bport)))

    neighbors = _cdp(d, name, if_names) + _lldp(
        d, name, _lldp_ports(d, by_name, bridge, if_names), if_names
    )
    first: dict[str, NormalizedNeighbor] = {}
    for n in neighbors:  # CDP vor LLDP (wie per SSH)
        first.setdefault(n.port, n)

    interfaces = []
    for e in ports.values():
        admin, oper = number(e.get("admin")), number(e.get("oper"))
        status = "disabled" if admin == 2 else _OPER.get(oper)
        nb = first.get(e["name"])
        interfaces.append(NormalizedInterface(
            device=name, name=e["name"], kind=e["kind"],
            enabled=None if admin is None else admin == 1, mode=e.get("mode"),
            untagged_vlan=e.get("untagged_vlan"), tagged_vlans=e.get("tagged_vlans") or [],
            lag=e.get("lag"), description=text(e.get("alias")),
            neighbor=nb.remote_name if nb else None, neighbor_port=nb.remote_port if nb else None,
            status=status, speed=_speed(e.get("speed")),
            duplex=_DUPLEX.get(number(e.get("duplex"))), native_vlan=e.get("native_vlan"),
            trunk_vlans=e.get("trunk_vlans"), attrs={"mac": e["mac"]} if e.get("mac") else None,
        ))  # fmt: skip

    vids = set(vlan_names) | set(egress) | set(untagged)
    vlans = [NormalizedVlan(vid=v, name=vlan_names.get(v) or str(v)) for v in sorted(vids)
             if 1 <= v <= 4094]  # fmt: skip

    macs: list[NormalizedMac] = []
    seen: set[tuple] = set()
    for bport, address, vlan, status in _fdb(d):
        ifindex = if_of_port.get(bport) if bport else None
        port = ports.get(ifindex) if ifindex else None
        if (not port or port["kind"] not in ("physical", "lag") or status in _FDB_SKIP
                or int(address[:2], 16) & 1 or address in own_macs):  # fmt: skip
            continue
        if (address, vlan, port["name"]) in seen:
            continue
        seen.add((address, vlan, port["name"]))
        macs.append(NormalizedMac(device=name, mac=address, port=port["name"], vlan=vlan,
                                  kind=_FDB_KIND.get(status)))  # fmt: skip
    if len(macs) > ssh.MAX_MACS:
        macs = macs[: ssh.MAX_MACS]

    arps, rows6 = _arp(d, name, if_names, own_macs)
    arps += nd.entries(name, rows6)
    return NetworkInventory(devices=[device], interfaces=interfaces, vlans=vlans,
                            neighbors=neighbors, macs=macs, arps=arps)  # fmt: skip


def _arp(d: _Data, device: str, if_names: dict[int, str], own: set[str]):
    """ARP (IPv4) und IPv6-Nachbarn aus der IP-MIB; Typ invalid/local und unvollstaendige weg."""
    v4: list[NormalizedArp] = []
    v6: list[dict] = []
    seen: set[tuple] = set()
    types, states = d.col("ipNetToPhysicalType"), d.col("ipNetToPhysicalState")
    for idx, value in d.col("ipNetToPhysicalPhysAddress").items():
        p = _parts(idx)
        if len(p) < 3 or number(types.get(idx)) in (2, 5) or number(states.get(idx)) in (5, 7):
            continue
        ip, m = _address(p[1], p[2:]), mac(value)
        if not ip or not m or m in own:
            continue
        iface = if_names.get(p[0])
        if ":" in ip:
            v6.append({"address": ip, "mac": m, "interface": iface})
        elif (ip, m, iface) not in seen:
            seen.add((ip, m, iface))
            v4.append(NormalizedArp(device=device, ip=ip, mac=m, interface=iface))
    if not v4:  # aeltere Agenten: nur ipNetToMediaTable (IPv4)
        media_types = d.col("ipNetToMediaType")
        for idx, value in d.col("ipNetToMediaPhysAddress").items():
            p = _parts(idx)
            if len(p) != 5 or number(media_types.get(idx)) == 2:
                continue
            ip, m = _address(1, p[1:]), mac(value)
            if ip and m and m not in own and (ip, m, if_names.get(p[0])) not in seen:
                seen.add((ip, m, if_names.get(p[0])))
                v4.append(NormalizedArp(device=device, ip=ip, mac=m, interface=if_names.get(p[0])))
    return v4, v6
