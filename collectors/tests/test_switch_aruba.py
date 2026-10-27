"""HPE Aruba CX (AOS-CX, 0.88.x, ADR 0025 Nr. 13): Erkennung, Positivliste, Auswertung mit den
ntc-Vorlagen `aruba_aoscx` (erlaubte VLANs und LAG-Mitglieder ueber die Grenze der Vorlage hinaus
aus dem Block), LLDP, MAC, ARP und IPv6-Nachbarn aller VRFs; ArubaOS-Switch (ProCurve) wird
erkannt und nicht gelesen."""

import pytest
import switch_vendors as sv
from sot_gateway.readonly import ReadOnlyViolation

from collectors.cisco_switch import aruba, tasks
from collectors.cisco_switch import client as sc
from collectors.cisco_switch import normalize as sn
from collectors.common import diagnose
from collectors.common.dto import Target


def test_erkennung_und_positivliste():
    assert sn.detect_platform(sv.AOSCX["show version"]) == "aoscx"
    assert sn.detect_platform("AOS-CX\nVersion      : ML.10.13.1000\n") == "aoscx"
    assert sn.detect_platform(sv.EOS_VERSION_TEXT) == "eos"
    assert sn.PLATFORM_LABEL["aoscx"] == "Aruba CX (AOS-CX)"
    cmds = sc.COMMANDS["aoscx"]
    assert cmds[0] == "show version" and all(c.startswith("show ") for c in cmds)
    assert set(aruba._TEMPLATES) | {aruba.CMD_ND} <= set(cmds)
    assert sc.SESSION["aoscx"] == "no page" and "no page" not in sc.ALLOWED
    client = sc.SwitchClient("192.0.2.1", "ro", "pw")
    for bad in ("show running-config", "configure terminal", "no page", "show interface\nreload"):
        with pytest.raises(ReadOnlyViolation):
            client.run(bad)
    assert "AOS-CX-Gruppe operators" in diagnose._HINTS["cisco_switch"]


def test_inventar():
    notes: list[str] = []
    inv = sn.switch_inventory("10.0.3.1", "aoscx", sv.AOSCX, notes)
    assert notes == []
    (dev,) = inv.devices
    assert (dev.name, dev.role, dev.model, dev.serial, dev.version, dev.mgmt_ip) == (
        "acc-cx-01", "switch", "JL658A 6300M 24SFP+ 4SFP56 Swch", "SG30ABC123", "FL.10.10.1060",
        "10.0.3.1")  # fmt: skip
    ports = {i.name: i for i in inv.interfaces}
    esx = ports["1/1/1"]
    assert (esx.status, esx.speed, esx.duplex, esx.mode, esx.native_vlan) == (
        "up", "1000", "full", "tagged", 1)  # fmt: skip
    # die Vorlage nimmt nur sechs Eintraege - alle acht aus dem Block
    assert esx.trunk_vlans == "1,10,20,30,40,50,60,70" and 70 in esx.tagged_vlans
    assert (esx.neighbor, esx.neighbor_port) == ("srv-esx02.example.ch", "vmnic0")  # Port-ID MAC
    printer = ports["1/1/2"]
    assert (printer.mode, printer.untagged_vlan, printer.enabled, printer.status) == (
        "access", 20, False, "disabled")  # fmt: skip
    lag = ports["lag1"]
    assert (lag.kind, lag.trunk_vlans, lag.speed) == ("lag", "1-4094", "20000")
    assert lag.description == "Uplink sw-core-01"
    assert ports["1/1/49"].lag == ports["1/1/50"].lag == "lag1"
    assert (ports["1/1/49"].speed, ports["1/1/49"].attrs) == ("10G", {"media": "SFP+SR"})
    up = ports["1/1/49"]
    assert (up.neighbor, up.neighbor_port) == ("sw-core-01", "Ethernet1/51")
    nb = {n.port: n for n in inv.neighbors}
    assert nb["1/1/49"].remote_caps == ["bridge", "router"] and nb["1/1/49"].remote_ip == "10.0.0.2"
    assert nb["1/1/1"].remote_platform.startswith("VMware ESX")
    assert [(v.vid, v.name) for v in inv.vlans] == [(1, "DEFAULT_VLAN_1"), (10, "Server"),
                                                    (20, "Clients")]  # fmt: skip
    # MAC nur an Ports bzw. LAGs (nicht die eigene am VLAN-Interface)
    assert [(m.port, m.vlan, m.mac) for m in inv.macs] == [
        ("1/1/1", 10, "00:50:56:01:00:31"), ("1/1/1", 20, "00:50:56:01:00:32"),
        ("lag1", 20, "a0:b1:c2:d3:e4:f5")]  # fmt: skip
    assert {(a.ip, a.interface, a.vrf) for a in inv.arps} == {
        ("10.10.0.31", "vlan10", None), ("10.20.0.15", "vlan20", None),
        ("10.99.0.9", "vlan99", "TENANT-B"),
        ("2001:db8:10::31", "vlan10", None), ("fe80::250:56ff:fe01:31", "vlan10", None),
        ("2001:db8:99::9", "vlan99", "TENANT-B"),
    }  # fmt: skip


def test_vorlage_passt_nicht():
    notes: list[str] = []
    broken = dict(sv.AOSCX, **{"show mac-address-table": "MAC Tabelle neu\nunbekannte Zeile\n"})
    inv = sn.switch_inventory("10.0.3.1", "aoscx", broken, notes)
    assert inv.macs == [] and {i.name for i in inv.interfaces} >= {"1/1/1", "lag1"}
    assert notes and notes[0].startswith("show mac-address-table: Ausgabe nicht auswertbar")
    assert aruba._speed("25 Gb/s") == "25G" and aruba._speed("auto") is None
    assert sn.cli_error("Invalid input: all-vrfs\n") == "Invalid input: all-vrfs"


class _Chan:
    """Kanal einer Sitzung wie bei ArubaOS-Switch: Banner mit „Press any key to continue“."""

    def __init__(self):
        self.sent: list[str] = []
        banner = b"\x1b[2J\x1b[?7l\x1b[3;23rHPE Aruba 2930F\r\n\r\nPress any key to continue"
        self.out = [banner, b""]

    def get_pty(self, **kw):
        return None

    def invoke_shell(self):
        return None

    def settimeout(self, value):
        return None

    def recv(self, n):
        return self.out.pop(0) if self.out else b""

    def send(self, data):
        self.sent.append(data)

    def close(self):
        return None


class _Transport:
    def __init__(self):
        self.chan = _Chan()

    def open_session(self, timeout=None):
        return self.chan


def test_arubaos_switch_nicht_gelesen():
    """ProCurve/ArubaOS-Switch: Abbruch beim Tastendruck-Banner, ohne etwas zu senden."""
    transport = _Transport()
    with pytest.raises(sc.UnsupportedDevice, match="ArubaOS-Switch/ProCurve"):
        sc._Shell(transport, "ro", "pw", 5)
    assert transport.chan.sent == []
    msg = diagnose.device_error(sc.UnsupportedDevice("ArubaOS-Switch/ProCurve wird nicht gelesen"),
                                "cisco_switch")  # fmt: skip
    assert msg == "ArubaOS-Switch/ProCurve wird nicht gelesen"


def test_lauf(monkeypatch):
    class Fake:
        def __init__(self, entry, username, password, known_key=None):
            self.entry, self.host_key, self.mode = entry, "ssh-ed25519 K", "shell"
            self.prepared = None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def version(self):
            return sv.AOSCX["show version"]

        def prepare(self, platform):
            assert platform == "aoscx"

        def run(self, cmd):
            assert cmd in sc.ALLOWED
            return sv.AOSCX.get(cmd, "Invalid input: x\n")

    monkeypatch.setattr(tasks, "SwitchClient", Fake)
    monkeypatch.setattr(tasks, "submit_result", lambda run_id, result, via_agent=False: None)
    monkeypatch.setattr(tasks, "confirmed", lambda result, _: result)
    target = Target(platform="cisco_switch", name="Access", username="ro", password="pw",
                    read_scope="10.0.3.1")  # fmt: skip
    result = tasks.collect_once("run-cx", target)
    assert result.status == "completed"
    assert result.message == "Switches: 1 gelesen (Aruba CX (AOS-CX))"
    assert {i.name for i in result.inventory.interfaces} >= {"1/1/1", "lag1"}
