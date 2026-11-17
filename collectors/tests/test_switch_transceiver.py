"""Transceiver je Port (1.19.0, „Verkabelung im Detail“): Parser je Plattform mit Textbeispielen,
Zuordnung nur zu gelesenen Ports, Lesestatus je Switch (gelesen, nicht lesbar, nicht unterstuetzt -
SNMPv3), Positivliste der Befehle und die Meldung des Laufs."""

import json

import pytest
from sot_gateway.readonly import ReadOnlyViolation

from collectors.cisco_switch import client as sc
from collectors.cisco_switch import tasks, transceiver
from collectors.cisco_switch.normalize import normalize
from collectors.common.dto import (
    NetworkInventory,
    NormalizedDevice,
    NormalizedInterface,
    Target,
)

IOS = """NAME: "c93xx Stack", DESCR: "c93xx Stack"
PID: C9300-48P         , VID: V02  , SN: FOC2345X0AB

NAME: "Switch 1", DESCR: "C9300-48P"
PID: C9300-48P         , VID: V02  , SN: FOC2345X0AB

NAME: "Switch 1 - Power Supply A", DESCR: "Switch 1 - Power Supply A"
PID: PWR-C1-715WAC     , VID: V02  , SN: LIT2345A0CD

NAME: "TenGigabitEthernet1/1/1", DESCR: "SFP-10GBase-SR"
PID: SFP-10G-SR          , VID: V03  , SN: AVD2345A1B2

NAME: "GigabitEthernet1/1/2", DESCR: "1000BaseSX SFP"
PID: Unspecified         , VID:      , SN: FNS2345A1B3

NAME: "TenGigabitEthernet1/1/8", DESCR: "SFP-10GBase-LR"
PID: SFP-10G-LR          , VID: V02  , SN: ONT2345A1B4
"""
ISR = """NAME: "Chassis", DESCR: "Cisco ISR4331 Chassis"
PID: ISR4331/K9        , VID: V04  , SN: FDO2345A0AA

NAME: "subslot 0/0 transceiver 1", DESCR: "GE SX"
PID: GLC-SX-MMD          , VID: V01  , SN: AGM2345A0AB
"""
NXOS = """Ethernet1/1
    transceiver is present
    type is 10Gbase-SR
    name is CISCO-FINISAR
    part number is FTLX8571D3BCL-C2
    revision is A
    serial number is FNS2345A0AA
    nominal bitrate is 10300 MBit/sec
    Link length supported for 50/125um OM3 fiber is 300 m
    cisco id is 3
    cisco extended id number is 4
    cisco part number is 10-2415-03
    cisco product id is SFP-10G-SR
    cisco version id is V03

Ethernet1/2
    transceiver is not present

Ethernet1/3
    transceiver is present
    type is 10Gbase-LR
    name is OEM
    part number is SFP-10G-LR-OEM
    revision is 1.0
    serial number is OEM2345A0AC

Ethernet1/49
    transceiver is present
    type is QSFP-40G-SR4
    name is CISCO-AVAGO
    part number is AFBR-79EQPZ-CS1
    serial number is AVM2345A0AD
    cisco product id is QSFP-40G-SR4
"""
CBS = """NAME: "1"   DESCR: "C1300-24FP-4X 24-Port Gigabit PoE+ Managed Switch, 4x10G SFP+"
PID: C1300-24FP-4X      VID: V01      SN: PSZ2345A0AA

NAME: "te1/0/1"   DESCR: "SFP-10G-SR"
PID: SFP-10G-SR      VID: V03      SN: FNS2345A0BB
"""
AOSCX = """-------------------------------------------------------------------------------
                                         Product  Serial            Part
Port    Type                             Number   Number            Number
-------------------------------------------------------------------------------
1/1/49  SFP+SR                           J9150D   CN2345A0AA        1990-4634
1/1/50  SFP+DAC3                         J9283D   CN2345A0AB        8121-1302
1/1/51  --                               --       --                --
"""
EOS = json.dumps({
    "xcvrSlots": {
        "49": {"mfgName": "Arista Networks", "modelName": "QSFP-100G-SR4",
               "serialNum": "XCW2345A0A", "hardwareRev": "11"},
        "50": {"mfgName": "Not Present", "modelName": "", "serialNum": "", "hardwareRev": ""},
        "1": {"mfgName": "FINISAR CORP.", "modelName": "FTLX8571D3BCL", "serialNum": "AS2345A0B",
              "hardwareRev": "A"},
    },
    "systemInformation": {"name": "DCS-7050SX3-48YC8"},
})  # fmt: skip


def _d(value):
    return [{"data": value}]


JUNOS = json.dumps({"chassis-inventory": [{"chassis": [{
    "name": _d("Chassis"), "serial-number": _d("JN2345A0AA"), "description": _d("EX4300-48T"),
    "chassis-module": [
        {"name": _d("Routing Engine 0"), "description": _d("RE-EX4300")},
        {"name": _d("FPC 0"), "description": _d("EX4300-48T"), "chassis-sub-module": [
            {"name": _d("PIC 0"), "description": _d("48x 10/100/1000 Base-T")},
            {"name": _d("PIC 2"), "description": _d("4x 1G/10G SFP/SFP+"),
             "chassis-sub-sub-module": [
                 {"name": _d("Xcvr 0"), "version": _d("REV 01"), "part-number": _d("740-021308"),
                  "serial-number": _d("AS2345A0C"), "description": _d("SFP+-10G-SR")},
                 {"name": _d("Xcvr 3"), "part-number": _d("740-011613"),
                  "serial-number": _d("PNB2345A0D"), "description": _d("SFP-SX")}]}]},
        {"name": _d("Power Supply 0"), "serial-number": _d("1EDV2345A0")}]}]}]})  # fmt: skip


def _read(platform, cmd, text, ports):
    return transceiver.read(platform, {cmd: text}, "sw-x", ports)


def test_ios_show_inventory_nur_ports():
    items, status = _read("ios", "show inventory", IOS, ["Gi1/0/1", "Te1/1/1", "Gi1/1/2"])
    assert status.status == "read" and status.count == 2
    by = {t.port: t for t in items}
    assert set(by) == {"Te1/1/1", "Gi1/1/2"}  # Gehaeuse, Netzteil und ungelesener Port fallen weg
    te = by["Te1/1/1"]
    assert (te.type, te.part, te.serial, te.vendor) == ("SFP-10GBase-SR", "SFP-10G-SR",
                                                        "AVD2345A1B2", None)  # fmt: skip
    assert (by["Gi1/1/2"].part, by["Gi1/1/2"].type) == (None, "1000BaseSX SFP")  # Unspecified
    # Router: „subslot 0/0 transceiver 1“ -> Gi0/0/1
    items, _ = _read("ios", "show inventory", ISR, ["Gi0/0/0", "Gi0/0/1"])
    assert [(t.port, t.part) for t in items] == [("Gi0/0/1", "GLC-SX-MMD")]


def test_nxos_show_interface_transceiver():
    items, status = _read("nxos", "show interface transceiver", NXOS,
                          ["Eth1/1", "Eth1/2", "Eth1/3", "Eth1/49"])  # fmt: skip
    assert status.count == 3
    by = {t.port: t for t in items}
    assert (by["Eth1/1"].vendor, by["Eth1/1"].part, by["Eth1/1"].serial) == (
        "CISCO-FINISAR",
        "SFP-10G-SR",
        "FNS2345A0AA",
    )  # Cisco-PID vor Teilenummer
    assert (by["Eth1/3"].type, by["Eth1/3"].part) == ("10Gbase-LR", "SFP-10G-LR-OEM")
    assert by["Eth1/49"].type == "QSFP-40G-SR4"
    assert "Eth1/2" not in by  # nicht gesteckt


def test_cbs_aoscx_eos_junos():
    items, _ = _read("cbs", "show inventory", CBS, ["Gi1/0/1", "Te1/0/1"])
    assert [(t.port, t.part, t.serial) for t in items] == [("Te1/0/1", "SFP-10G-SR",
                                                            "FNS2345A0BB")]  # fmt: skip
    items, _ = _read("aoscx", "show interface transceiver", AOSCX, ["1/1/49", "1/1/50", "1/1/51"])
    assert [(t.port, t.type, t.part, t.serial) for t in items] == [
        ("1/1/49", "SFP+SR", "J9150D", "CN2345A0AA"), ("1/1/50", "SFP+DAC3", "J9283D",
                                                        "CN2345A0AB")]  # fmt: skip
    items, _ = _read("eos", "show inventory | json", EOS, ["Eth1", "Eth49/1", "Eth49/2", "Eth50"])
    by = {t.port: t for t in items}
    assert set(by) == {"Eth1", "Eth49/1"}  # QSFP: erste Lane; leerer Schacht fehlt
    assert (by["Eth49/1"].vendor, by["Eth49/1"].type) == ("Arista Networks", "QSFP-100G-SR4")
    cmd = "show chassis hardware | display json | no-more"
    items, _ = _read("junos", cmd, JUNOS, ["ge-0/0/0", "xe-0/2/0", "ge-0/2/3"])
    assert [(t.port, t.type, t.part) for t in items] == [
        ("xe-0/2/0", "SFP+-10G-SR", "740-021308"),
        ("ge-0/2/3", "SFP-SX", "740-011613"),
    ]


def test_status_nicht_lesbar_und_nicht_unterstuetzt():
    _, st = transceiver.read("ios", {}, "sw-x", ["Gi1/0/1"])
    assert (st.status, st.note) == ("unreadable", "„show inventory“ nicht gelesen")
    _, st = _read("ios", "show inventory", "% Invalid input detected at '^' marker.\n", [])
    assert st.status == "unreadable" and "Invalid input" in st.note
    _, st = _read("eos", "show inventory | json", '{"xcvrSlots": [', ["Eth1"])
    assert st.status == "unreadable" and "nicht auswertbar" in st.note
    _, st = transceiver.read("snmp", {}, "sw-x", ["Gi1/0/1"])
    assert st.status == "unsupported" and "SNMPv3" in st.note
    _, st = _read("ios", "show inventory", "", ["Gi1/0/1"])  # nur Kupfer: gelesen, keine Optik
    assert (st.status, st.count) == ("read", 0)


def test_hook_in_normalize_und_snmp():
    inv = NetworkInventory()
    part = NetworkInventory(
        devices=[NormalizedDevice(name="sw-snmp", role="switch")],
        interfaces=[NormalizedInterface(device="sw-snmp", name="Gi1/0/1")],
    )
    transceiver.add(inv, part, {"platform": "snmp", "outputs": {}})
    assert inv.transceivers == [] and inv.transceiver_reads[0].status == "unsupported"
    assert transceiver.summary(inv) == ("Transceiver: 0 an 0 Switches; nicht unterstützt auf 1 "
                                        "(über SNMPv3 nicht lesbar (nur per SSH))")  # fmt: skip
    # Beispieldaten: Catalyst und Nexus
    inv = normalize(tasks._sample())
    assert {(t.device, t.port) for t in inv.transceivers} == {
        ("sw-acc-01", "Te1/1/1"), ("sw-core-01", "Eth1/1"), ("sw-core-01", "Eth1/2"),
        ("sw-core-01", "Eth1/3"), ("sw-core-01", "Eth1/48")}  # fmt: skip
    assert [r.status for r in inv.transceiver_reads] == ["read", "read"]


def test_befehle_nur_lesend_und_lauf(monkeypatch):
    assert {"show inventory", "show interface transceiver", "show inventory | json"} <= sc.ALLOWED
    client = sc.SwitchClient("10.0.0.1", "ro", "pw")
    with pytest.raises(ReadOnlyViolation):
        client.run("show interface transceiver ; conf t")
    outputs = {"show version": "Cisco Nexus Operating System (NX-OS) Software\n  NXOS: version "
               "10.3(4a)\nDevice name: nx-01\n", "show interface transceiver": NXOS}  # fmt: skip
    asked: list[str] = []

    class Fake:
        def __init__(self, entry, username, password, known_key=None):
            self.host_key, self.mode = "ssh-ed25519 K", "exec"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def version(self):
            return outputs["show version"]

        def prepare(self, platform):
            pass

        def run(self, cmd):
            assert cmd in sc.ALLOWED
            asked.append(cmd)
            if cmd == "show interface transceiver":
                raise TimeoutError("keine Antwort")
            return outputs.get(cmd, "")

    monkeypatch.setattr(tasks, "SwitchClient", Fake)
    monkeypatch.setattr(tasks, "submit_result", lambda run_id, result, via_agent=False: None)
    monkeypatch.setattr(tasks, "confirmed", lambda result, _: result)
    target = Target(platform="cisco_switch", name="Netz", username="ro", password="pw",
                    read_scope="10.0.0.1")  # fmt: skip
    result = tasks.collect_once("run-t", target)
    assert result.status == "completed" and "show interface transceiver" in asked
    # Befehl gescheitert: Status „nicht lesbar“, gezaehlt in der Meldung und als Hinweis
    assert result.inventory.transceiver_reads[0].status == "unreadable"
    assert "Transceiver: 0 an 0 Switches; nicht lesbar auf 1" in result.message
    assert "show interface transceiver: TimeoutError" in result.message
