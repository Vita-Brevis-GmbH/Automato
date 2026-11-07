"""IPv6-Nachbarn (NDP) der Switches und Router (0.94.0, ADR 0025 Nr. 13): Befehl je Plattform in
der Positivliste, Auswertung (IOS mit ntc-Vorlage, NX-OS mit umgebrochenen Adressen und VRFs,
Junos JSON, Catalyst 1200/1300 als Tabelle), nur Unicast mit MAC, Link-local erhalten, Zone weg,
fehlender Befehl (alte Software) ohne Hinweis."""

import pytest
import switch_products as sp
import switch_vendors as sv
from sot_gateway.readonly import ReadOnlyViolation

from collectors.cisco_switch import cbs, junos, nd, tasks
from collectors.cisco_switch import client as sc
from collectors.cisco_switch import normalize as sn
from collectors.common.dto import Target


def _nd(inv) -> set[tuple]:
    return {(a.ip, a.mac, a.interface, a.vrf) for a in inv.arps if ":" in a.ip}


def test_befehle_in_der_positivliste():
    assert nd.COMMANDS <= sc.ALLOWED
    for platform, cmd in (("ios", nd.IOS), ("nxos", nd.NXOS), ("cbs", nd.CBS),
                          ("junos", nd.JUNOS), ("eos", nd.EOS), ("aoscx", nd.AOSCX)):  # fmt: skip
        assert cmd in sc.COMMANDS[platform]
    assert junos.CMD_ND == nd.JUNOS
    client = sc.SwitchClient("192.0.2.1", "ro", "pw")
    for bad in ("clear ipv6 neighbors", "show ipv6 neighbors | redirect x",
                "show ipv6 neighbors\nclear ipv6 neighbors"):  # fmt: skip
        with pytest.raises(ReadOnlyViolation):
            client.run(bad)


def test_adressen():
    assert nd.address("FE80::250:56FF:FE01:21%Vlan10") == "fe80::250:56ff:fe01:21"
    assert nd.address("2001:DB8:10::21/64") == "2001:db8:10::21"
    for bad in ("ff02::1", "::", "::1", "10.0.0.1", "INCOMPLETE", None):
        assert nd.address(bad) is None
    assert nd.link_local("fe80::1") and not nd.link_local("2001:db8::1")
    assert not nd.link_local("10.0.0.1") and not nd.link_local("kaputt")


def test_ios_mit_vorlage():
    inv = sn.switch_inventory("10.0.0.11", "ios", {nd.IOS: sv.IOS_ND})
    assert _nd(inv) == {
        ("2001:db8:10::21", "00:50:56:01:00:21", "Vl10", None),
        ("fe80::250:56ff:fe01:21", "00:50:56:01:00:21", "Vl10", None),  # Link-local bleibt
        ("2001:db8:20::15", "a0:b1:c2:d3:e4:f5", "Vl20", None),
    }  # unvollstaendig (INCMP, ohne MAC) faellt weg
    # passt die Vorlage nicht (z. B. zusaetzliche Zeile), dieselben Spalten selbst gelesen
    odd = "Load average: 1%\n" + sv.IOS_ND.replace("Vl10", "Vlan10")
    assert {a.ip for a in sn.switch_inventory("x", "ios", {nd.IOS: odd}).arps} == {
        "2001:db8:10::21", "fe80::250:56ff:fe01:21", "2001:db8:20::15"}  # fmt: skip


def test_nxos_tabelle_und_detail():
    inv = sn.switch_inventory("10.0.0.2", "nxos", {nd.NXOS: sv.NXOS_ND})
    assert _nd(inv) == {
        ("2001:db8:10::33", "00:50:56:02:00:03", "Vl10", None),  # umgebrochene Adresse
        ("fe80::250:56ff:fe02:3", "00:50:56:02:00:03", "Vl10", None),
        ("2001:db8:5::7", "00:50:56:02:00:07", "Vl5", None),
        ("2001:db8:a:10::9", "00:50:56:02:00:09", "Vl110", "TENANT-A"),  # VRF aus dem Abschnitt
    }
    rows = nd.nxos(sv.NXOS_ND_DETAIL)
    assert rows == [{"address": "2001:db8:10::33", "vrf": "default", "mac": "0050.5602.0003",
                     "interface": "Vlan10"}]  # fmt: skip


def test_junos_und_catalyst_1300():
    outputs = dict(sp.MX, **{nd.JUNOS: sv.JUNOS_ND})
    inv = sn.switch_inventory("10.0.0.40", "junos", outputs)
    assert _nd(inv) == {
        ("2001:db8:100::21", "00:50:56:01:01:21", "ae0.100", None),
        ("fe80::250:56ff:fe01:121", "00:50:56:01:01:21", "ae0.100", None),
    }
    assert {a.ip for a in inv.arps} >= {"10.100.0.21", "198.51.100.1"}  # ARP wie bisher
    c1300 = sn.switch_inventory("10.0.1.30", "cbs", dict(sp.C1300, **{nd.CBS: sv.CBS_ND}))
    assert _nd(c1300) == {("2001:db8:10::41", "00:50:56:01:00:41", "Vl10", None),
                          ("fe80::250:56ff:fe01:41", "00:50:56:01:00:41", "Vl10", None),
                          ("2001:db8:1::1", "e0:5f:b9:00:00:fe", "Vl1", None)}  # fmt: skip
    assert cbs.tables(sv.CBS_ND)[0][0]["ipv6 address"] == "2001:db8:10::41"


@pytest.mark.parametrize(("platform", "cmd", "error"), [
    ("ios", nd.IOS, "% Invalid input detected at '^' marker.\n"),
    ("nxos", nd.NXOS, "% Invalid command at '^' marker.\n"),
    ("cbs", nd.CBS, "% Unrecognized command\n"),
    ("junos", nd.JUNOS, "error: syntax error, expecting <command>: ipv6\n"),
    ("eos", nd.EOS, "% Invalid input\n"),
    ("aoscx", nd.AOSCX, "Invalid input: all-vrfs\n"),
])  # fmt: skip
def test_alte_software_ohne_hinweis(platform, cmd, error):
    err = sn.cli_error(error)
    assert err and sn.quiet(platform, cmd, err)
    assert not sn.quiet(platform, "show vlan brief", err)
    notes: list[str] = []
    inv = sn.switch_inventory("10.0.0.9", platform, {"show version": "", cmd: error}, notes)
    assert not [a for a in inv.arps if ":" in a.ip] and notes == []


def test_lauf_ohne_ipv6_befehl(monkeypatch):
    """Lauf: IOS mit IPv6-Nachbarn, Junos ohne den Befehl - kein Hinweis, Eintraege im Ergebnis."""
    outputs = {"10.0.0.11": {"show version": sp.C9200L, nd.IOS: sv.IOS_ND}, "10.0.0.40": sp.MX}

    class Fake:
        def __init__(self, entry, username, password, known_key=None):
            self.entry, self.host_key, self.mode = entry, "ssh-ed25519 K", "exec"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def version(self):
            return outputs[self.entry]["show version"]

        def prepare(self, platform):
            return None

        def run(self, cmd):
            assert cmd in sc.ALLOWED
            if cmd == nd.JUNOS:
                return "error: syntax error, expecting <command>: ipv6\n"
            return outputs[self.entry].get(cmd, "")

    monkeypatch.setattr(tasks, "SwitchClient", Fake)
    monkeypatch.setattr(tasks, "submit_result", lambda run_id, result, via_agent=False: None)
    monkeypatch.setattr(tasks, "confirmed", lambda result, _: result)
    target = Target(platform="cisco_switch", name="Netz", username="ro", password="pw",
                    read_scope="10.0.0.11\n10.0.0.40")  # fmt: skip
    result = tasks.collect_once("run-v6", target)
    assert result.status == "completed" and "Hinweise" not in result.message
    v6 = {(a.device, a.ip) for a in result.inventory.arps if ":" in a.ip}
    assert ("acc-9200l-01", "2001:db8:10::21") in v6
    assert not any(d == "mx-edge-01" for d, _ in v6)
