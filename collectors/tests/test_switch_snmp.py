"""Switches & Router ueber SNMPv3 (ADR 0033): Positivliste nur GET/GETBULK, kein SET, nur
authPriv, Abbildung je MIB auf dieselben DTOs wie per SSH, fehlende Tabellen als Hinweis, Lauf und
Pruefung mit Teilfehler. Ohne Geraete: Walk-Antworten als Testdaten (anonymisiert)."""

import ast
import ipaddress
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from sot_gateway.readonly import ReadOnlyViolation

from collectors.cisco_switch import normalize as ssh_normalize
from collectors.cisco_switch import tasks
from collectors.common import diagnose
from collectors.common.dto import Target
from collectors.common.i18n import use
from collectors.snmp import client as sc
from collectors.snmp import normalize as sn
from collectors.snmp import oids
from collectors.snmp import tasks as st

W = oids.WALKS
SECRET_AUTH, SECRET_PRIV = "auth-geheim-1", "priv-geheim-2"  # noqa: S105 - Testwerte


def _pl(*ports: int, size: int = 4) -> bytes:
    """PortList: Port 1 = hoechstes Bit des ersten Bytes."""
    out = bytearray(size)
    for p in ports:
        out[(p - 1) // 8] |= 0x80 >> ((p - 1) % 8)
    return bytes(out)


def _mac(text: str) -> bytes:
    return bytes(int(x, 16) for x in text.split(":"))


def _mac_index(text: str) -> str:
    return ".".join(str(int(x, 16)) for x in text.split(":"))


def _ip_index(ip: str, zone: bytes = b"") -> str:
    addr = ipaddress.ip_address(ip).packed + zone
    family = (1 if len(addr) == 4 else 2) + (2 if zone else 0)
    return f"{family}.{len(addr)}." + ".".join(str(b) for b in addr)


def _c(name: str, index, value) -> tuple[str, object]:
    return f"{W[name]}.{index}", value


# Switch A (Cisco Catalyst): Q-BRIDGE, Bridge-Port != ifIndex, LAG, LLDP + CDP, ENTITY, ARP/NDP
SELF = "00:aa:bb:00:02:00"
LINK_LOCAL = _ip_index("fe80::a2b1:c2ff:fed3:e4f5", b"\x00\x00\x00\xc8")  # mit Zone (ipv6z)
HOST_A = dict([
    ("1.3.6.1.2.1.1.1.0", b"Cisco IOS Software [Cupertino], Catalyst L3 Switch Software "
                          b"(CAT9K_IOSXE), Version 17.9.4a, RELEASE SOFTWARE (fc3)"),
    ("1.3.6.1.2.1.1.2.0", "1.3.6.1.4.1.9.1.2494"),
    ("1.3.6.1.2.1.1.5.0", b"sw-acc-01.example.ch"),
    # IF-MIB
    *[_c("ifName", i, n) for i, n in ((8, b"Gi1/0/1"), (9, b"Gi1/0/2"), (55, b"Gi1/0/47"),
                                      (56, b"Gi1/0/48"), (60, b"Te1/1/1"), (100, b"Po1"),
                                      (200, b"Vl20"), (300, b"Nu0"), (400, b"Lo0"))],
    *[_c("ifDescr", i, n) for i, n in ((8, b"GigabitEthernet1/0/1"),
                                       (60, b"TenGigabitEthernet1/1/1"),
                                       (100, b"Port-channel1"), (200, b"Vlan20"))],
    *[_c("ifType", i, t) for i, t in ((8, 6), (9, 6), (55, 6), (56, 6), (60, 6), (100, 53),
                                      (200, 53), (300, 1), (400, 24))],
    *[_c("ifAdminStatus", i, 2 if i == 9 else 1) for i in (8, 9, 55, 56, 60, 100, 200)],
    *[_c("ifOperStatus", i, 2 if i == 9 else 1) for i in (8, 9, 55, 56, 60, 100, 200)],
    *[_c("ifHighSpeed", i, s) for i, s in ((8, 1000), (9, 0), (55, 1000), (56, 1000), (60, 10000),
                                           (100, 2000))],
    _c("ifAlias", 8, b"Drucker 2.OG"),
    _c("ifAlias", 55, b"Uplink sw-core-01"),
    _c("ifAlias", 9, b""),
    _c("ifPhysAddress", 8, _mac("00:aa:bb:00:00:08")),
    _c("ifPhysAddress", 200, _mac(SELF)),
    _c("ifPhysAddress", 400, b""),
    *[_c("dot3StatsDuplexStatus", i, 3) for i in (8, 55, 56, 60)],
    _c("dot3adAggPortAttachedAggID", 8, 8),  # einzeln
    _c("dot3adAggPortAttachedAggID", 55, 100),
    _c("dot3adAggPortAttachedAggID", 56, 0),  # down: Selected gilt
    _c("dot3adAggPortSelectedAggID", 56, 100),
    # BRIDGE-MIB: Bridge-Port -> ifIndex
    *[_c("dot1dBasePortIfIndex", bp, i) for bp, i in ((1, 8), (2, 9), (3, 60), (10, 100))],
    # Q-BRIDGE-MIB
    *[_c("dot1qVlanStaticName", v, n) for v, n in ((1, b"default"), (10, b"Server"),
                                                   (20, b"Clients"), (30, b"DMZ"),
                                                   (1002, b"fddi-default"))],
    _c("dot1qVlanStaticEgressPorts", 1, _pl(2, 10)),
    _c("dot1qVlanStaticEgressPorts", 10, _pl(10)),
    _c("dot1qVlanStaticEgressPorts", 20, _pl(1, 10)),
    _c("dot1qVlanStaticEgressPorts", 30, _pl(3, 10)),
    _c("dot1qVlanStaticUntaggedPorts", 1, _pl(2, 10)),
    _c("dot1qVlanStaticUntaggedPorts", 20, _pl(1)),
    _c("dot1qVlanStaticUntaggedPorts", 30, _pl(3)),
    *[_c("dot1qPvid", bp, v) for bp, v in ((1, 20), (2, 1), (3, 30), (10, 1))],
    *[_c("dot1qVlanFdbId", f"0.{v}", v) for v in (1, 10, 20, 30)],
    # MAC-Tabelle: FDB.MAC -> Bridge-Port, Status (3 gelernt, 4 eigene, 5 statisch)
    _c("dot1qTpFdbPort", f"20.{_mac_index('a0:b1:c2:d3:e4:f5')}", 1),
    _c("dot1qTpFdbStatus", f"20.{_mac_index('a0:b1:c2:d3:e4:f5')}", 3),
    _c("dot1qTpFdbPort", f"10.{_mac_index('00:50:56:aa:00:01')}", 10),
    _c("dot1qTpFdbStatus", f"10.{_mac_index('00:50:56:aa:00:01')}", 3),
    _c("dot1qTpFdbPort", f"20.{_mac_index(SELF)}", 0),
    _c("dot1qTpFdbStatus", f"20.{_mac_index(SELF)}", 4),
    _c("dot1qTpFdbPort", f"30.{_mac_index('01:00:5e:00:00:01')}", 3),
    _c("dot1qTpFdbStatus", f"30.{_mac_index('01:00:5e:00:00:01')}", 5),
    _c("dot1qTpFdbPort", f"30.{_mac_index('a0:b1:c2:00:30:10')}", 3),
    _c("dot1qTpFdbStatus", f"30.{_mac_index('a0:b1:c2:00:30:10')}", 5),
    # IP-MIB: ARP und IPv6-Nachbarn (eigene, unvollstaendige weg)
    _c("ipNetToPhysicalPhysAddress", f"200.{_ip_index('10.20.0.15')}", _mac("a0:b1:c2:d3:e4:f5")),
    _c("ipNetToPhysicalType", f"200.{_ip_index('10.20.0.15')}", 3),
    _c("ipNetToPhysicalState", f"200.{_ip_index('10.20.0.15')}", 1),
    _c("ipNetToPhysicalPhysAddress", f"200.{_ip_index('10.20.0.1')}", _mac(SELF)),
    _c("ipNetToPhysicalType", f"200.{_ip_index('10.20.0.1')}", 5),
    _c("ipNetToPhysicalPhysAddress", f"200.{_ip_index('10.20.0.99')}", b""),
    _c("ipNetToPhysicalState", f"200.{_ip_index('10.20.0.99')}", 7),
    _c("ipNetToPhysicalPhysAddress", f"200.{_ip_index('2001:db8:20::15')}",
       _mac("a0:b1:c2:d3:e4:f5")),
    _c("ipNetToPhysicalState", f"200.{_ip_index('2001:db8:20::15')}", 2),
    _c("ipNetToPhysicalPhysAddress", f"200.{LINK_LOCAL}", _mac("a0:b1:c2:d3:e4:f5")),
    # LLDP-MIB: lokale Nummer != ifIndex (Zuordnung ueber den Namen)
    *[_c("lldpLocPortIdSubtype", n, 5) for n in (1, 47, 48, 61)],
    *[_c("lldpLocPortId", n, p) for n, p in ((1, b"Gi1/0/1"), (47, b"Gi1/0/47"), (48, b"Gi1/0/48"),
                                             (61, b"Te1/1/1"))],
    _c("lldpRemChassisIdSubtype", "0.47.1", 4),
    _c("lldpRemChassisId", "0.47.1", _mac("00:11:22:33:44:55")),
    _c("lldpRemPortIdSubtype", "0.47.1", 5),
    _c("lldpRemPortId", "0.47.1", b"Ethernet1/1"),
    _c("lldpRemSysName", "0.47.1", b"sw-core-01.example.ch"),
    _c("lldpRemSysCapEnabled", "0.47.1", bytes([0x28, 0x00])),  # bridge, router
    _c("lldpRemPortIdSubtype", "0.48.1", 5),
    _c("lldpRemPortId", "0.48.1", b"Ethernet1/2"),
    _c("lldpRemSysName", "0.48.1", b"sw-core-01"),
    _c("lldpRemPortIdSubtype", "0.61.3", 7),
    _c("lldpRemPortId", "0.61.3", b"5"),
    _c("lldpRemPortDesc", "0.61.3", b"dmz"),
    _c("lldpRemSysName", "0.61.3", b"fw-08"),
    _c("lldpRemSysDesc", "0.61.3", b"FortiGate-200F v7.4.4"),
    _c("lldpRemChassisIdSubtype", "0.1.2", 4),
    _c("lldpRemChassisId", "0.1.2", _mac("a0:b1:c2:d3:e4:f5")),
    _c("lldpRemPortIdSubtype", "0.1.2", 3),
    _c("lldpRemPortId", "0.1.2", _mac("a0:b1:c2:d3:e4:f5")),
    _c("lldpRemSysCapSupported", "0.1.2", bytes([0x01, 0x00])),  # station-only
    _c("lldpRemManAddrIfSubtype", "0.47.1.1.4.10.0.0.2", 2),
    _c("lldpRemManAddrIfSubtype", "0.61.3.2.16." + ".".join(
        str(b) for b in ipaddress.ip_address("2001:db8::8").packed), 2),
    _c("lldpRemManAddrIfSubtype", "0.61.3.1.4.10.0.30.1", 2),
    # CISCO-CDP-MIB: gilt am Port vor LLDP
    _c("cdpCacheAddressType", "55.1", 1),
    _c("cdpCacheAddress", "55.1", bytes([10, 0, 0, 2])),
    _c("cdpCacheDeviceId", "55.1", b"sw-core-01(FDO1234ABCD)"),
    _c("cdpCacheDevicePort", "55.1", b"Ethernet1/1"),
    _c("cdpCachePlatform", "55.1", b"N9K-C93180YC-FX"),
    _c("cdpCacheCapabilities", "55.1", bytes([0, 0, 0, 0x29])),
    # ENTITY-MIB: Chassis 1
    _c("entPhysicalClass", 1, 3),
    _c("entPhysicalClass", 1000, 9),
    ("1.3.6.1.2.1.47.1.1.1.1.13.1", b"C9300-48P"),
    ("1.3.6.1.2.1.47.1.1.1.1.11.1", b"FOC2301ABCD"),
    ("1.3.6.1.2.1.47.1.1.1.1.10.1", b"17.09.04a"),
])  # fmt: skip

# Switch B (Aruba CX): IEEE8021-Q-BRIDGE-MIB, nur ipNetToMediaTable, ohne LLDP und ENTITY
HOST_B = dict([
    ("1.3.6.1.2.1.1.1.0", b"Aruba JL658A 6300M 24SFP+ 4SFP56 Swch FL.10.10.1020"),
    ("1.3.6.1.2.1.1.2.0", "1.3.6.1.4.1.47196.4.1.1.1.304"),
    ("1.3.6.1.2.1.1.5.0", b"sw-dist-02"),
    *[_c("ifName", i, n) for i, n in ((1, b"1/1/1"), (2, b"1/1/2"), (1001, b"lag1"))],
    *[_c("ifType", i, t) for i, t in ((1, 6), (2, 6), (1001, 161))],
    *[_c("ifOperStatus", i, 1) for i in (1, 2, 1001)],
    _c("ifHighSpeed", 1, 25000),
    _c("dot3adAggPortAttachedAggID", 2, 1001),
    *[_c("ieee8021BridgeBasePortIfIndex", f"1.{bp}", i) for bp, i in ((1, 1), (2, 2), (100, 1001))],
    _c("ieee8021QBridgeVlanStaticName", "1.10", b"Server"),
    _c("ieee8021QBridgeVlanStaticEgressPorts", "1.10", _pl(1, 100, size=13)),
    _c("ieee8021QBridgeVlanStaticUntaggedPorts", "1.10", _pl(1, size=13)),
    _c("ieee8021QBridgePvid", "1.1", 10),
    _c("ieee8021QBridgePvid", "1.100", 1),
    _c("ieee8021QBridgeTpFdbPort", f"1.10.{_mac_index('a0:b1:c2:00:10:05')}", 1),
    _c("ieee8021QBridgeTpFdbStatus", f"1.10.{_mac_index('a0:b1:c2:00:10:05')}", 3),
    _c("ipNetToMediaPhysAddress", "1.10.10.0.5", _mac("a0:b1:c2:00:10:05")),
    _c("ipNetToMediaType", "1.10.10.0.5", 3),
])  # fmt: skip

DATA = {"10.0.0.11": HOST_A, "10.0.0.12": HOST_B}


def _key(oid: str) -> tuple[int, ...]:
    return tuple(int(p) for p in oid.split("."))


class FakeTransport:
    """SNMP-Agent ohne Netz: GET und GETBULK-Walk aus den Testdaten, Aufrufe im Log."""

    def __init__(self, data: dict, log: list | None = None, fail: Exception | None = None):
        self.data, self.log, self.fail = data, log if log is not None else [], fail

    def open(self):
        if self.fail is not None:
            raise self.fail
        self.log.append(("open",))

    def close(self):
        self.log.append(("close",))

    def get(self, oid_list):
        self.log.append(("get", tuple(oid_list)))
        return {o: self.data.get(o) for o in oid_list}

    def walk(self, base, limit, seconds):
        self.log.append(("walk", base))
        rows = sorted(((o, v) for o, v in self.data.items() if o.startswith(base + ".")),
                      key=lambda r: _key(r[0]))  # fmt: skip
        return rows[:limit], len(rows) > limit


def _target(**kw) -> Target:
    base = {"platform": "cisco_switch", "name": "Switches A", "access": "snmp",
            "username": "ro-automato", "auth_protocol": "sha256", "auth_password": SECRET_AUTH,
            "priv_protocol": "aes128", "priv_password": SECRET_PRIV,
            "read_scope": "10.0.0.11\n10.0.0.12"}  # fmt: skip
    return Target(**{**base, **kw})


def _transports(monkeypatch, log=None, fail=None):
    def factory(host, port, creds, timeout, retries):
        assert port == 161 and creds.user == "ro-automato"
        if host in (fail or {}):
            return FakeTransport({}, log, fail[host])
        return FakeTransport(DATA.get(host, {}), log)

    monkeypatch.setattr(sc, "PysnmpTransport", factory)


def _read(data: dict, host: str = "10.0.0.11"):
    notes: list[str] = []
    creds = sc.credentials(_target())
    with sc.SnmpClient(host, creds, transport=FakeTransport(data)) as client:
        raw = st.read(client, notes)
    return sn.inventory(host, raw, notes), notes


# -- Nur lesen ------------------------------------------------------------------------------


def test_positivliste_nur_get_und_getbulk():
    assert oids.allowed("get", "1.3.6.1.2.1.1.5.0")
    assert oids.allowed("get", "1.3.6.1.2.1.47.1.1.1.1.13.1")  # Zeile des Chassis
    assert oids.allowed("walk", W["ifName"])
    assert oids.allowed("next", W["ifName"] + ".12", W["ifName"])
    for op, oid, base in (
        ("get", "1.3.6.1.2.1.1.4.0", None),  # sysContact: nicht in der Liste
        ("get", "1.3.6.1.6.3.15.1.2.2.1.1", None),  # usmUserTable
        ("walk", "1.3.6.1.2.1", None),  # ganze MIB-2
        ("walk", "1.3.6.1.6.3.16", None),  # VACM
        ("next", "1.3.6.1.2.1.2.2.1.7.1", W["ifName"]),  # Fortsetzung ausserhalb der Spalte
        ("set", "1.3.6.1.2.1.1.5.0", None),  # SET gibt es nicht
    ):
        assert not oids.allowed(op, oid, base)
        with pytest.raises(ReadOnlyViolation):
            oids.check(op, oid, base)
    creds = sc.credentials(_target())
    log: list = []
    with sc.SnmpClient("10.0.0.11", creds, transport=FakeTransport(HOST_A, log)) as client:
        with pytest.raises(ReadOnlyViolation):
            client.walk("1.3.6.1.6.3.15")
        with pytest.raises(ReadOnlyViolation):
            client.get("sysContact")
        with pytest.raises(ReadOnlyViolation):
            client.get_row("1.3.6.1.6.3.15.1.2.2.1", "1")  # Zeile der usmUserTable
        assert not any(hasattr(client, n) for n in ("set", "set_cmd", "write"))
    assert log == [("open",), ("close",)]  # nichts gesendet


def test_kein_set_aus_pysnmp():
    """Quelltext: aus pysnmp nur einzelne lesende Namen, nie SET, nie das ganze Modul."""
    allowed = {
        "config",
        "ContextData",
        "ObjectIdentity",
        "ObjectType",
        "SnmpEngine",
        "Udp6TransportTarget",
        "UdpTransportTarget",
        "UsmUserData",
        "bulk_cmd",
        "get_cmd",
    }
    folder = Path(sc.__file__).parent
    imported = set()
    for path in folder.glob("*.py"):
        source = path.read_text()
        assert not re.search(r"set_?cmd|SetCommand|setCmd", source, re.I), path.name
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                assert not any(a.name.startswith("pysnmp") for a in node.names), path.name
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("pysnmp"):
                imported |= {a.name for a in node.names}
    assert imported and imported <= allowed


def test_nur_snmpv3_authpriv():
    creds = sc.credentials(_target(auth_protocol=None, priv_protocol=None, context=" vlan-10 "))
    assert (creds.auth_protocol, creds.priv_protocol, creds.context) == ("sha256", "aes128",
                                                                         "vlan-10")  # fmt: skip
    assert SECRET_AUTH not in repr(creds) and SECRET_PRIV not in repr(creds)
    assert SECRET_AUTH not in repr(_target()) and SECRET_PRIV not in repr(_target())
    for bad in ({"priv_password": None}, {"auth_password": ""}, {"username": " "},
                {"auth_protocol": "md5"}, {"priv_protocol": "des"}, {"priv_protocol": "3des"},
                {"auth_password": "kurz"}, {"priv_password": "1234567"}):  # fmt: skip
        with pytest.raises(sc.SnmpConfigError):
            sc.credentials(_target(**bad))
    for p in ("sha", "sha224", "sha384", "sha512"):
        assert sc.credentials(_target(auth_protocol=p.upper())).auth_protocol == p
    assert sc.split_host("sw1") == ("sw1", 161) and sc.split_host("sw1:1161") == ("sw1", 1161)
    assert sc.split_host("[2001:db8::1]:161") == ("2001:db8::1", 161)
    assert sc.split_host("2001:db8::1") == ("2001:db8::1", 161)


# -- Abbildung je MIB -----------------------------------------------------------------------


def test_abbildung_system_if_mib_und_lag():
    inv, notes = _read(HOST_A)
    assert notes == []
    (dev,) = inv.devices
    assert (dev.name, dev.role, dev.model, dev.serial, dev.version, dev.mgmt_ip) == (
        "sw-acc-01", "switch", "C9300-48P", "FOC2301ABCD", "17.09.04a", "10.0.0.11")  # fmt: skip
    ports = {i.name: i for i in inv.interfaces}
    assert set(ports) == {"Gi1/0/1", "Gi1/0/2", "Gi1/0/47", "Gi1/0/48", "Te1/1/1", "Po1", "Vl20"}
    printer = ports["Gi1/0/1"]
    got = (printer.status, printer.enabled, printer.speed, printer.duplex, printer.description)
    assert got == ("up", True, "1000", "full", "Drucker 2.OG")
    assert printer.attrs == {"mac": "00:aa:bb:00:00:08"} and printer.kind == "physical"
    off = ports["Gi1/0/2"]
    assert (off.status, off.enabled, off.speed, off.description) == ("disabled", False, None, None)
    assert ports["Te1/1/1"].speed == "10G"
    assert (ports["Po1"].kind, ports["Vl20"].kind) == ("lag", "vlan")
    assert ports["Gi1/0/47"].lag == "Po1" and ports["Gi1/0/48"].lag == "Po1"  # Selected
    assert ports["Gi1/0/1"].lag is None  # Aggregator = eigener Port


def test_abbildung_vlans_je_port():
    inv, _ = _read(HOST_A)
    ports = {i.name: i for i in inv.interfaces}
    assert (ports["Gi1/0/1"].mode, ports["Gi1/0/1"].untagged_vlan) == ("access", 20)
    assert (ports["Gi1/0/2"].mode, ports["Gi1/0/2"].untagged_vlan) == ("access", 1)
    assert (ports["Te1/1/1"].mode, ports["Te1/1/1"].untagged_vlan) == ("access", 30)
    po = ports["Po1"]
    assert (po.mode, po.native_vlan, po.untagged_vlan, po.trunk_vlans, po.tagged_vlans) == (
        "tagged", 1, 1, "1,10,20,30", [1, 10, 20, 30])  # fmt: skip
    assert ports["Gi1/0/47"].mode is None  # Mitglied: VLANs am Port-Channel
    assert [(v.vid, v.name) for v in inv.vlans] == [(1, "default"), (10, "Server"),
                                                     (20, "Clients"), (30, "DMZ")]  # fmt: skip


def test_abbildung_nachbarn_lldp_und_cdp():
    inv, _ = _read(HOST_A)
    by = {(n.protocol, n.port): n for n in inv.neighbors}
    assert set(by) == {("cdp", "Gi1/0/47"), ("lldp", "Gi1/0/47"), ("lldp", "Gi1/0/48"),
                       ("lldp", "Te1/1/1"), ("lldp", "Gi1/0/1")}  # fmt: skip
    cdp = by[("cdp", "Gi1/0/47")]
    assert (cdp.remote_name, cdp.remote_port, cdp.remote_ip, cdp.remote_platform) == (
        "sw-core-01", "Ethernet1/1", "10.0.0.2", "N9K-C93180YC-FX")  # fmt: skip
    assert cdp.remote_caps == ["router", "switch", "igmp"]
    core = by[("lldp", "Gi1/0/47")]
    assert (core.remote_name, core.remote_port, core.remote_ip, core.remote_caps) == (
        "sw-core-01.example.ch", "Ethernet1/1", "10.0.0.2", ["bridge", "router"])  # fmt: skip
    fw = by[("lldp", "Te1/1/1")]
    assert (fw.remote_name, fw.remote_port, fw.remote_ip) == ("fw-08", "dmz", "10.0.30.1")
    assert fw.remote_description == "FortiGate-200F v7.4.4"
    host = by[("lldp", "Gi1/0/1")]
    assert (host.remote_name, host.remote_port, host.remote_caps) == (
        "a0:b1:c2:d3:e4:f5", "a0:b1:c2:d3:e4:f5", ["station-only"])  # fmt: skip
    ports = {i.name: i for i in inv.interfaces}
    assert (ports["Gi1/0/47"].neighbor, ports["Gi1/0/47"].neighbor_port) == (
        "sw-core-01", "Ethernet1/1")  # CDP vor LLDP  # fmt: skip
    assert ports["Gi1/0/48"].neighbor == "sw-core-01"


def test_abbildung_mac_arp_und_ipv6():
    inv, _ = _read(HOST_A)
    macs = {(m.mac, m.port, m.vlan, m.kind) for m in inv.macs}
    assert macs == {
        ("a0:b1:c2:d3:e4:f5", "Gi1/0/1", 20, "dynamic"),
        ("00:50:56:aa:00:01", "Po1", 10, "dynamic"),
        ("a0:b1:c2:00:30:10", "Te1/1/1", 30, "static"),
    }  # eigene/Multicast weg
    arps = {(a.ip, a.mac, a.interface) for a in inv.arps}
    assert arps == {("10.20.0.15", "a0:b1:c2:d3:e4:f5", "Vl20"),
                    ("2001:db8:20::15", "a0:b1:c2:d3:e4:f5", "Vl20"),
                    ("fe80::a2b1:c2ff:fed3:e4f5", "a0:b1:c2:d3:e4:f5", "Vl20")}  # fmt: skip


def test_ieee8021_rueckfall_und_fehlende_tabellen():
    inv, notes = _read(HOST_B, "10.0.0.12")
    (dev,) = inv.devices
    assert (dev.name, dev.model, dev.version) == ("sw-dist-02", None, "FL.10.10.1020")
    ports = {i.name: i for i in inv.interfaces}
    assert (ports["1/1/1"].mode, ports["1/1/1"].untagged_vlan, ports["1/1/1"].speed) == (
        "access", 10, "25G")  # fmt: skip
    lag = ports["lag1"]
    assert (lag.kind, lag.mode, lag.native_vlan, lag.trunk_vlans) == ("lag", "tagged", None, "10")
    assert ports["1/1/2"].lag == "lag1"
    assert [(m.mac, m.port, m.vlan) for m in inv.macs] == [("a0:b1:c2:00:10:05", "1/1/1", 10)]
    assert [(a.ip, a.interface) for a in inv.arps] == [("10.10.0.5", "1/1/1")]  # ipNetToMedia
    assert inv.neighbors == []
    # LLDP fehlt: Hinweis, kein Fehler; optionale MIBs (CDP, ENTITY, EtherLike) ohne Hinweis
    assert notes == ["ohne Antwort (Tabelle fehlt bzw. nicht in der View): LLDP-MIB"]


def test_nur_system_und_router():
    data = {"1.3.6.1.2.1.1.1.0": b"Juniper Networks, Inc. mx480 internet router, kernel JUNOS "
                                 b"21.4R3-S4.9, Build date: 2023-06-01",
            "1.3.6.1.2.1.1.2.0": "1.3.6.1.4.1.2636.1.1.1.2.25",
            "1.3.6.1.2.1.1.5.0": b"rt-edge-01"}  # fmt: skip
    inv, notes = _read(data, "[2001:db8::1]:161")
    (dev,) = inv.devices
    assert (dev.name, dev.role, dev.model, dev.version, dev.mgmt_ip) == (
        "rt-edge-01", "router", "mx480", "21.4R3-S4.9", "2001:db8::1")  # fmt: skip
    assert notes == ["ohne Antwort (Tabelle fehlt bzw. nicht in der View): IF-MIB, Q-BRIDGE-MIB, "
                     "BRIDGE-MIB, LLDP-MIB"]  # fmt: skip


def test_mac_tabelle_gekappt(monkeypatch):
    monkeypatch.setattr(ssh_normalize, "MAX_MACS", 2)
    inv, notes = _read(HOST_A)
    assert len(inv.macs) <= 2
    assert "MAC-Tabelle: mehr als 2 Eintraege, nur die ersten uebernommen" in notes


def test_hilfen():
    assert sn.port_list(_pl(1, 8, 9, 32)) == {1, 8, 9, 32} and sn.port_list(None) == set()
    assert sn.mac(b"\x00\x50\x56\x01\x00\x01") == "00:50:56:01:00:01"
    assert sn.mac("0x005056010001") == "00:50:56:01:00:01" and sn.mac(b"\x00") is None
    assert sn.text(b"sw-01\x00 ") == "sw-01" and sn.text(b"") is None and sn.text(7) == "7"
    assert sn.vendor("1.3.6.1.4.1.30065.1.3011") == "Arista" and sn.vendor("1.3.6.1.4.1.1") is None
    assert sn._speed(100) == "100" and sn._speed(40000) == "40G" and sn._speed(0) is None
    assert sn._address(1, [4, 10, 0, 0, 1]) == "10.0.0.1" and sn._address(1, [10, 0, 0, 1])
    assert sn._address(1, [3, 1, 2, 3]) == "3.1.2.3"  # ohne Laenge, erstes Byte 3
    assert sn._address(9, [1, 2]) is None and sn._address(2, [16, 1, 2]) is None


# -- Lauf und Pruefung ----------------------------------------------------------------------


def test_lauf_snmp_mit_teilfehler(monkeypatch):
    log: list = []
    _transports(monkeypatch, log, {"10.0.0.99": sc.SnmpTimeout("No SNMP response")})
    monkeypatch.setattr(tasks, "submit_result", lambda run_id, result, via_agent=False: None)
    monkeypatch.setattr(tasks, "confirmed", lambda result, _: result)
    monkeypatch.setattr(tasks, "SwitchClient", None)  # SSH wird nicht benutzt
    target = _target(read_scope="10.0.0.11\n10.0.0.12\n10.0.0.99")
    with use("en"):  # Lauf: Meldung trotzdem deutsch (gespeichert)
        result = tasks.collect_once("run-1", target)
    assert result.status == "completed" and result.host_keys == {}
    assert {d.name for d in result.inventory.devices} == {"sw-acc-01", "sw-dist-02"}
    assert result.message.startswith("Switches: 2 gelesen (SNMPv3: Aruba, Cisco)")
    assert "nicht gelesen: 10.0.0.99: keine Antwort per SNMPv3 (UDP)" in result.message
    assert "Hinweise: 10.0.0.12: ohne Antwort (Tabelle fehlt bzw. nicht in der View): LLDP-MIB" in (
        result.message
    )
    assert SECRET_AUTH not in result.model_dump_json() and SECRET_PRIV not in result.message
    assert all(entry[0] in ("open", "close", "get", "walk") for entry in log)
    # Zugangsdaten ohne Priv-Passwort: kein Paket, Lauf fehlgeschlagen (Agent, kein Lab)
    log.clear()
    monkeypatch.setattr(tasks, "get_settings", lambda: SimpleNamespace(agent_token="x"))
    failed = tasks.collect_once("run-2", _target(priv_password=None))
    assert failed.status == "failed" and "keine Zugangsdaten" in failed.message and log == []
    weak = tasks.collect_once("run-3", _target(priv_protocol="des"))
    assert weak.status == "failed" and "Priv-Protokoll „des“ nicht erlaubt" in weak.message
    assert log == []


def test_pruefung_snmp_nur_get_sysname(monkeypatch):
    log: list = []
    auth = sc.SnmpAuthError("Unknown USM user")
    _transports(monkeypatch, log, {"10.0.0.99": auth})
    ok, msg = tasks.check(_target(read_scope="10.0.0.11, 10.0.0.99"))
    assert not ok
    assert msg.startswith("10.0.0.11: erreichbar (SNMPv3 authPriv, sysName sw-acc-01.example.ch)")
    assert "10.0.0.99: Anmeldung abgelehnt (SNMPv3" in msg and "Unknown USM user" in msg
    assert "Benutzer: 'ro-automato'" in msg  # nur lokal (Konsole), nie Passwoerter
    assert SECRET_AUTH not in msg and SECRET_PRIV not in msg
    assert [e for e in log if e[0] in ("get", "walk")] == [("get", ("1.3.6.1.2.1.1.5.0",))]
    with use("en"):
        ok, msg = tasks.check(_target(read_scope="10.0.0.11"))
    assert ok and msg == "10.0.0.11: reachable (SNMPv3 authPriv, sysName sw-acc-01.example.ch)"


def test_diagnose_snmp():
    msg = diagnose.device_error(sc.SnmpAuthError("Wrong SNMP PDU digest"), "cisco_switch")
    assert msg.startswith("Anmeldung abgelehnt (SNMPv3") and "Lese-View" in msg
    assert "Geraet meldet: Wrong SNMP PDU digest" in msg
    msg = diagnose.device_error(sc.SnmpTimeout("timeout"), "cisco_switch")
    assert msg.startswith("keine Antwort per SNMPv3 (UDP)")
    assert diagnose.device_error(sc.SnmpError("genErr"), "cisco_switch") == "SNMP-Fehler: genErr"


# -- pysnmp-Transport mit Attrappe der Bibliothek -----------------------------------------------


class _Name(str):
    def asTuple(self):  # noqa: N802 - wie pysnmp
        return tuple(int(p) for p in self.split("."))


class _Octets:
    def __init__(self, raw: bytes):
        self.raw = raw

    def asOctets(self):  # noqa: N802 - wie pysnmp
        return self.raw


class EndOfMibView:
    pass


def _fake_api(data: dict, calls: list):
    def wrap(v):
        return _Octets(v) if isinstance(v, bytes) else v

    class Udp:
        @staticmethod
        async def create(address, timeout, retries):
            calls.append(("target", address, timeout, retries))
            return "target"

    async def get_cmd(engine, auth, target, ctx, *binds, lookupMib):
        calls.append(("get", tuple(binds)))
        return None, 0, 0, [(_Name(o), wrap(data.get(o, EndOfMibView()))) for o in binds]

    async def bulk_cmd(engine, auth, target, ctx, non_repeaters, repetitions, var, lookupMib):
        calls.append(("bulk", var))
        after = sorted((o for o in data if _key(o) > _key(var)), key=_key)[:repetitions]
        rows = [[(_Name(o), wrap(data[o]))] for o in after]  # Tabelle (aeltere Form)
        return None, 0, 0, rows or [[(_Name(var), EndOfMibView())]]

    usm = SimpleNamespace(usmHMAC192SHA256AuthProtocol="sha256-id", USM_PRIV_CFB128_AES="aes-id")
    return {
        "usm": usm, "ContextData": lambda **kw: ("ctx", kw), "ObjectIdentity": lambda oid: oid,
        "ObjectType": lambda ident: ident, "SnmpEngine": lambda: SimpleNamespace(
            close_dispatcher=lambda: calls.append(("closed",))),
        "Udp6": Udp, "Udp": Udp,
        "UsmUserData": lambda user, **kw: calls.append(("usm", user, kw)) or "usm",
        "bulk": bulk_cmd, "get": get_cmd,
    }  # fmt: skip


def test_pysnmp_transport_mit_attrappe(monkeypatch):
    calls: list = []
    data = {**HOST_A, "1.3.6.1.2.1.31.1.1.1.15.8": 1000}
    monkeypatch.setattr(sc, "_load", lambda: _fake_api(data, calls))
    monkeypatch.setattr(sc, "MAX_REPETITIONS", 3)
    creds = sc.credentials(_target(context="ctx-a"))
    with sc.SnmpClient("10.0.0.11:1161", creds) as client:
        assert client.get("sysName") == {"sysName": b"sw-acc-01.example.ch"}
        rows, cut = client.walk("ifName")
        with pytest.raises(ReadOnlyViolation):
            client._transport.walk("1.3.6.1.6.3.15", 10, 1.0)  # auch der Transport prueft
        with pytest.raises(ReadOnlyViolation):
            client._transport.get(["1.3.6.1.6.3.15.1.2.2.1.1.0"])
    assert not cut and rows == {"8": b"Gi1/0/1", "9": b"Gi1/0/2", "55": b"Gi1/0/47",
                                "56": b"Gi1/0/48", "60": b"Te1/1/1", "100": b"Po1",
                                "200": b"Vl20", "300": b"Nu0", "400": b"Lo0"}  # fmt: skip
    (usm,) = (c for c in calls if c[0] == "usm")
    assert usm[1] == "ro-automato" and usm[2] == {
        "authKey": SECRET_AUTH, "privKey": SECRET_PRIV, "authProtocol": "sha256-id",
        "privProtocol": "aes-id"}  # authPriv: beide Schluessel  # fmt: skip
    assert ("target", ("10.0.0.11", 1161), sc.TIMEOUT, sc.RETRIES) in calls
    bulks = [c[1] for c in calls if c[0] == "bulk"]
    assert bulks[0] == W["ifName"] and all(b.startswith(W["ifName"]) for b in bulks)
    assert calls[-1] == ("closed",)
    # gekappt nach `limit` Zeilen
    calls.clear()
    with sc.SnmpClient("10.0.0.11", creds) as client:
        rows, cut = client.walk("ifName", limit=4)
    assert cut and list(rows) == ["8", "9", "55", "56"]


def test_pysnmp_werte_und_fehler():
    class ObjectName(str):
        def asTuple(self):  # noqa: N802
            return ()

    class NoSuchObject:
        pass

    class RequestTimedOut:
        def __str__(self):
            return "No SNMP response received before timeout"

    class UnknownUserName:
        def __str__(self):
            return "Unknown USM user"

    assert sc.native(NoSuchObject()) is None and sc.native(5) == 5
    assert sc.native(_Octets(b"\x01")) == b"\x01" and sc.native(ObjectName("1.3.6")) == "1.3.6"
    flat = [(ObjectName("1.3.6.1"), 1), (ObjectName("1.3.6.2"), 2)]
    assert list(sc._binds(flat)) == [("1.3.6.1", 1), ("1.3.6.2", 2)]
    assert list(sc._binds([flat[:1], flat[1:]])) == [("1.3.6.1", 1), ("1.3.6.2", 2)]
    assert isinstance(sc._error(RequestTimedOut()), sc.SnmpTimeout)
    assert isinstance(sc._error(UnknownUserName()), sc.SnmpAuthError)
    assert isinstance(sc._error("genErr"), sc.SnmpError)


def test_ohne_pysnmp_klare_meldung(monkeypatch):
    import builtins

    real = builtins.__import__

    def no_pysnmp(name, *args, **kwargs):
        if name.startswith("pysnmp"):
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pysnmp)
    with pytest.raises(sc.SnmpUnavailable, match="pysnmp fehlt"):
        sc._load()


def test_pysnmp_echt_gegen_lokalen_agenten():
    """Mit der echten pysnmp (nicht der Attrappe): ein SNMPv3-Agent aus pysnmp auf 127.0.0.1 -
    GET sysName und GETBULK-Walk gehen, falsches Passwort = SnmpAuthError (nur lokal)."""
    pytest.importorskip("pysnmp")
    import asyncio
    import socket
    import threading

    from pysnmp.carrier.asyncio.dgram import udp
    from pysnmp.entity import config, engine
    from pysnmp.entity.rfc3413 import cmdrsp, context

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:  # freier UDP-Port
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    ready, stop = threading.Event(), threading.Event()

    def agent():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        eng = engine.SnmpEngine()
        server = udp.UdpTransport().open_server_mode(("127.0.0.1", port))
        config.add_transport(eng, udp.DOMAIN_NAME, server)
        config.add_v3_user(eng, "ro-user", config.USM_AUTH_HMAC192_SHA256, SECRET_AUTH,
                           config.USM_PRIV_CFB128_AES, SECRET_PRIV)  # fmt: skip
        config.add_vacm_user(eng, 3, "ro-user", "authPriv", (1, 3, 6, 1, 2, 1), ())
        ctx = context.SnmpContext(eng)
        cmdrsp.GetCommandResponder(eng, ctx)
        cmdrsp.BulkCommandResponder(eng, ctx)
        eng.transport_dispatcher.job_started(1)
        ready.set()

        async def wait():
            while not stop.is_set():
                await asyncio.sleep(0.05)

        loop.run_until_complete(wait())
        eng.transport_dispatcher.close_dispatcher()
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()

    thread = threading.Thread(target=agent, daemon=True)
    thread.start()
    assert ready.wait(10)
    try:
        good = sc.PysnmpTransport("127.0.0.1", port, sc.SnmpCredentials("ro-user", SECRET_AUTH,
                                  SECRET_PRIV), 3.0, 1)  # fmt: skip
        good.open()
        try:
            assert "1.3.6.1.2.1.1.5.0" in good.get(["1.3.6.1.2.1.1.5.0"])
            rows, cut = good.walk("1.3.6.1.2.1.2.2.1.2", 50, 10.0)  # ifDescr: hier leer
            assert rows == [] and cut is False
        finally:
            good.close()
        bad = sc.PysnmpTransport("127.0.0.1", port, sc.SnmpCredentials("ro-user", "falsch-123",
                                 SECRET_PRIV), 3.0, 0)  # fmt: skip
        bad.open()
        try:
            with pytest.raises(sc.SnmpAuthError):
                bad.get(["1.3.6.1.2.1.1.5.0"])
        finally:
            bad.close()
    finally:
        stop.set()
        thread.join(5)
