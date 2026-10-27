"""Arista EOS (0.94.0, ADR 0025 Nr. 13): Erkennung nach `show version`, Positivliste mit `| json`,
Geraet, Ports (Status, Speed, Duplex, Modus, VLANs, Port-Channel), VLANs, LLDP, MAC-Tabelle, ARP
und IPv6-Nachbarn aller VRFs; kaputtes JSON = Hinweis; Lauf ueber den Collector."""

import pytest
import switch_vendors as sv
from sot_gateway.readonly import ReadOnlyViolation

from collectors.cisco_switch import arista, tasks
from collectors.cisco_switch import client as sc
from collectors.cisco_switch import normalize as sn
from collectors.common import diagnose
from collectors.common.dto import Target


def test_erkennung_und_positivliste():
    assert sn.detect_platform(sv.EOS["show version"]) == "eos"
    assert sn.detect_platform("Arista vEOS-lab\nSoftware image version: 4.30.1F\n") == "eos"
    assert sn.PLATFORM_LABEL["eos"] == "Arista EOS"
    cmds = sc.COMMANDS["eos"]
    assert cmds[0] == "show version"  # Text: erkennt die Plattform
    assert all(c.startswith("show ") and c.endswith(" | json") for c in cmds[1:])
    for name in ("CMD_VERSION", "CMD_HOSTNAME", "CMD_STATUS", "CMD_DESCRIPTION",
                 "CMD_SWITCHPORT", "CMD_VLAN", "CMD_PORT_CHANNEL", "CMD_LLDP", "CMD_MAC",
                 "CMD_ARP", "CMD_ND"):  # fmt: skip
        assert getattr(arista, name) in cmds
    assert "show running-config" not in sc.ALLOWED and sc.SESSION["eos"] not in sc.ALLOWED
    client = sc.SwitchClient("192.0.2.1", "ro", "pw")
    for bad in ("show running-config | json", "show version | json | redirect x",
                "bash timeout 5 ls", "configure session x"):  # fmt: skip
        with pytest.raises(ReadOnlyViolation):
            client.run(bad)
    assert "Arista-Rolle network-operator" in diagnose._HINTS["cisco_switch"][0]


def test_inventar():
    notes: list[str] = []
    inv = sn.switch_inventory("10.0.0.21", "eos", sv.EOS, notes)
    assert notes == []
    (dev,) = inv.devices
    assert (dev.name, dev.role, dev.model, dev.serial, dev.version, dev.mgmt_ip) == (
        "leaf-ar-01", "switch", "DCS-7050SX3-48YC8", "JPE20201234", "4.28.3M",
        "10.0.0.21")  # fmt: skip
    ports = {i.name: i for i in inv.interfaces}
    esx = ports["Eth1"]
    assert (esx.status, esx.speed, esx.duplex, esx.mode, esx.native_vlan) == (
        "connected", "10G", "full", "tagged", 1)  # fmt: skip
    assert (esx.trunk_vlans, esx.tagged_vlans) == ("10,20", [10, 20])
    assert esx.attrs == {"media": "10GBASE-T"} and esx.description == "srv-esx01 vmnic0"
    # Port-ID als MAC (ESXi): die Beschreibung des Ports gilt
    assert (esx.neighbor, esx.neighbor_port) == ("srv-esx01.example.ch", "vmnic0")
    printer = ports["Eth2"]
    assert (printer.mode, printer.untagged_vlan, printer.status, printer.duplex) == (
        "access", 20, "notconnect", None)  # fmt: skip
    assert printer.enabled is True
    assert ports["Eth3"].enabled is False and ports["Eth3"].status == "disabled"
    up = ports["Eth49/1"]
    assert (up.lag, up.speed, up.neighbor, up.neighbor_port) == ("Po100", "25G", "sw-core-01",
                                                                 "Ethernet1/49")  # fmt: skip
    assert ports["Eth50/1"].lag == "Po100"
    po = ports["Po100"]
    assert (po.kind, po.trunk_vlans, po.tagged_vlans) == ("lag", "1-4094", [])  # ALL: nur Bereich
    assert ports["Vl10"].kind == "vlan" and ports["Vl10"].description == "Server"
    assert ports["Management1"].attrs == {"media": "10/100/1000"}
    assert [(v.vid, v.name) for v in inv.vlans] == [(1, "default"), (10, "Server"), (20, "Clients")]
    nb = {n.port: n for n in inv.neighbors}
    core = nb["Eth49/1"]
    assert (core.remote_ip, core.remote_caps, core.protocol) == ("10.0.0.2", ["bridge", "router"],
                                                                 "lldp")  # fmt: skip
    assert nb["Eth1"].remote_platform.startswith("VMware ESX")  # ESXi erkennbar (kein Switch)
    assert "Management1" not in nb
    # MAC-Tabelle: nur Unicast an Ports - nicht Router, CPU, VXLAN, Multicast
    assert sorted((m.port, m.vlan, m.mac) for m in inv.macs) == [
        ("Eth1", 10, "00:50:56:01:00:21"), ("Eth1", 20, "00:50:56:01:00:22"),
        ("Po100", 20, "a0:b1:c2:d3:e4:f5")]  # fmt: skip
    arps = {(a.ip, a.interface, a.vrf) for a in inv.arps}
    assert arps == {
        ("10.10.0.21", "Vl10", None), ("10.20.0.15", "Vl20", None),
        ("10.255.0.1", "Management1", "MGMT"),
        ("2001:db8:10::21", "Vl10", None), ("fe80::250:56ff:fe01:21", "Vl10", None),
    }  # fmt: skip


def test_ohne_json_und_fehler():
    # ohne `show version | json` und Hostname: Werte aus dem Text bzw. die Adresse
    outputs = {"show version": sv.EOS["show version"]}
    inv = sn.switch_inventory("10.0.0.22", "eos", outputs)
    dev = inv.devices[0]
    assert (dev.name, dev.model, dev.version, dev.serial) == ("10.0.0.22", "DCS-7050SX3-48YC8",
                                                              "4.28.3M", "JPE20201234")  # fmt: skip
    # kaputtes JSON: Teil leer, Hinweis; Fehlermeldung des Geraets: leer
    notes: list[str] = []
    broken = dict(sv.EOS, **{arista.CMD_STATUS: '{"interfaceStatuses": {'})
    broken[arista.CMD_LLDP] = "% LLDP is not enabled\n"
    inv = sn.switch_inventory("10.0.0.21", "eos", broken, notes)
    assert notes == ["show interfaces status | json: Ausgabe nicht auswertbar (JSONDecodeError)"]
    assert inv.neighbors == [] and {i.name for i in inv.interfaces} >= {"Eth1", "Po100"}
    assert arista.load('{"errors": ["CLI command 1 of 1 failed: invalid command"]}') == {}
    assert arista.port_name("Et49/1") == "Eth49/1" and arista.port_name("Port-Channel7") == "Po7"
    assert arista._speed(100_000_000) == "100" and arista._speed(0) is None


def test_lauf(monkeypatch):
    class Fake:
        def __init__(self, entry, username, password, known_key=None):
            self.entry, self.host_key, self.mode = entry, "ssh-ed25519 K", "exec"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def version(self):
            return sv.EOS["show version"]

        def prepare(self, platform):
            return None

        def run(self, cmd):
            assert cmd in sc.ALLOWED
            return sv.EOS.get(cmd, "% Invalid input\n")

    monkeypatch.setattr(tasks, "SwitchClient", Fake)
    monkeypatch.setattr(tasks, "submit_result", lambda run_id, result, via_agent=False: None)
    monkeypatch.setattr(tasks, "confirmed", lambda result, _: result)
    target = Target(platform="cisco_switch", name="Leafs", username="ro", password="pw",
                    read_scope="10.0.0.21")  # fmt: skip
    result = tasks.collect_once("run-eos", target)
    assert result.status == "completed"
    assert result.message == "Switches: 1 gelesen (Arista EOS)"
    assert result.inventory.devices[0].name == "leaf-ar-01"
    assert any(a.ip == "2001:db8:10::21" for a in result.inventory.arps)
