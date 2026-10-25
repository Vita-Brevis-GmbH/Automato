"""Cisco-Switches (ADR 0025): Nur-Lese-Schutz, SSH ohne Terminal mit Host-Schluessel,
Normalisierung der show-Ausgaben (IOS-XE und NX-OS), Lauf mit Teilfehlern."""

import socket
import threading
import time

import paramiko
import pytest
from sot_gateway.readonly import ReadOnlyViolation

from collectors.cisco_switch import client as sc
from collectors.cisco_switch import normalize as sn
from collectors.cisco_switch import tasks
from collectors.cisco_switch.normalize import (
    cli_error,
    mac_text,
    normalize,
    short_name,
    vlan_ranges,
    vlan_set,
)
from collectors.common import diagnose
from collectors.common.dto import Target

SAMPLE = tasks._sample()


def test_nur_erlaubte_show_befehle():
    client = sc.SwitchClient("192.0.2.1", "ro", "pw")
    for cmd in ("configure terminal", "show running-config", "show version | redirect x",
                "terminal length 0", "reload", "show version\nreload"):  # fmt: skip
        with pytest.raises(ReadOnlyViolation):
            client.run(cmd)
    assert all(c.startswith("show ") for c in sc.ALLOWED)
    assert sc.split_host("sw1:2222") == ("sw1", 2222)
    assert sc.split_host("[2001:db8::1]:22") == ("2001:db8::1", 22)
    assert sc.split_host("2001:db8::1") == ("2001:db8::1", 22)
    assert tasks.hosts("sw1, sw2\nsw3;sw1  ") == ["sw1", "sw2", "sw3"]


def test_normalisierung_ios_und_nxos():
    inv = normalize(SAMPLE)
    devs = {d.name: d for d in inv.devices}
    assert set(devs) == {"sw-acc-01", "sw-core-01"}
    acc, core = devs["sw-acc-01"], devs["sw-core-01"]
    assert (acc.role, acc.model, acc.serial, acc.version, acc.mgmt_ip) == (
        "switch", "C9300-48P", "FOC2301ABCD", "17.9.4a", "10.0.0.11")  # fmt: skip
    assert (core.model, core.version) == ("C93180YC-FX", "10.3(4a)")
    ports = {(i.device, i.name): i for i in inv.interfaces}
    up = ports[("sw-acc-01", "Gi1/0/47")]
    assert (up.status, up.mode, up.native_vlan, up.trunk_vlans, up.lag) == (
        "connected", "tagged", 1, "10,20,30", "Po1")  # fmt: skip
    assert (up.speed, up.duplex, up.tagged_vlans) == ("a-1000", "a-full", [10, 20, 30])
    assert up.description == "Uplink sw-core-01 Eth1/1"  # voller Text statt des gekuerzten
    assert (up.neighbor, up.neighbor_port) == ("sw-core-01", "Ethernet1/1")  # ohne Seriennummer
    printer = ports[("sw-acc-01", "Gi1/0/2")]
    assert (printer.mode, printer.untagged_vlan, printer.status) == ("access", 20, "notconnect")
    assert ports[("sw-acc-01", "Gi1/0/3")].enabled is False
    assert ports[("sw-acc-01", "Po1")].kind == "lag"
    vl1 = ports[("sw-acc-01", "Vl1")]
    assert vl1.enabled is False and vl1.kind == "vlan"
    peer = ports[("sw-core-01", "Eth1/48")]
    assert peer.trunk_vlans == "1-4094" and peer.tagged_vlans == []  # alle VLANs: nur Bereich
    assert ports[("sw-core-01", "Po10")].attrs == {"vpc": "10"}
    assert ports[("sw-core-01", "Eth1/1")].lag == "Po10"
    assert ports[("sw-core-01", "mgmt0")].description is None  # "--" ist kein Text
    nbs = {(n.device, n.port, n.protocol): n for n in inv.neighbors}
    fw = nbs[("sw-acc-01", "Te1/1/1", "lldp")]
    assert (fw.remote_name, fw.remote_port, fw.remote_ip) == ("fw-08", "port5", "10.0.30.1")
    acc_seen = nbs[("sw-core-01", "Eth1/1", "cdp")]
    assert (acc_seen.remote_name, acc_seen.remote_port) == ("sw-acc-01", "GigabitEthernet1/0/47")
    assert nbs[("sw-core-01", "Eth1/3", "lldp")].remote_name == "esx02.example.ch"
    assert [(v.vid, v.name) for v in inv.vlans] == [(1, "default"), (10, "Server"),
                                                    (20, "Clients"), (30, "DMZ")]  # fmt: skip


def test_mac_tabelle_und_arp():
    """0.63.0: nur Unicast an Ports (nicht CPU, Supervisor, Peer-Link), MACs einheitlich, ARP
    ohne die eigenen Adressen."""
    inv = normalize(SAMPLE)
    macs = {(m.device, m.port, m.mac): m for m in inv.macs}
    assert ("sw-acc-01", "Gi1/0/1", "00:50:56:01:00:01") in macs
    assert macs[("sw-core-01", "Eth1/3", "00:50:56:02:00:03")].vlan == 10
    assert macs[("sw-core-01", "Po10", "a0:b1:c2:d3:e4:f5")].kind == "dynamic"
    assert not [m for m in inv.macs if m.mac.startswith(("01:", "ff:"))]  # Multicast/Broadcast
    assert not [m for m in inv.macs if m.mac == "00:50:56:03:00:04"]  # nur am vPC-Peer-Link
    assert not [m for m in inv.macs if m.port.lower().startswith(("cpu", "sup"))]
    assert len(inv.macs) == 10
    arps = {(a.device, a.ip): a for a in inv.arps}
    assert set(arps) == {("sw-acc-01", "10.0.0.2"), ("sw-core-01", "10.10.0.21"),
                         ("sw-core-01", "10.10.0.33"), ("sw-core-01", "10.20.0.15")}  # fmt: skip
    assert arps[("sw-core-01", "10.20.0.15")].interface == "Vl20"
    assert mac_text("00-50-56-AA-BB-CC") == "00:50:56:aa:bb:cc" and mac_text("Incomplete") is None
    assert sn._l2_port(["Gi1/0/1", "Gi1/0/2"]) is None and sn._l2_port("Router") is None
    assert sn._l2_port("port-channel10") == "Po10"


def test_mac_tabelle_gekappt(monkeypatch):
    monkeypatch.setattr(sn, "MAX_MACS", 3)
    raw = {"switches": [dict(sw, notes=[]) for sw in SAMPLE["switches"]]}
    inv = normalize(raw)
    assert len([m for m in inv.macs if m.device == "sw-acc-01"]) == 3
    assert raw["switches"][0]["notes"] == ["MAC-Tabelle: 3 Eintraege ueber 3 nicht uebernommen"]


def test_hilfen():
    assert short_name("GigabitEthernet1/0/47") == "Gi1/0/47"
    assert short_name("Ethernet1/1") == "Eth1/1"
    assert short_name("port-channel10") == "Po10"
    assert short_name("TenGigabitEthernet1/1/1") == "Te1/1/1"
    assert short_name("Gi1/0/47(P)") == "Gi1/0/47"
    assert vlan_set("ALL") == set(range(1, 4095))
    assert vlan_set("10,20,30-32") == {10, 20, 30, 31, 32}
    assert vlan_set("none") == set() and vlan_set("x-y") is None
    assert vlan_ranges({1, 2, 3, 10, 20, 21}) == "1-3,10,20-21"
    assert cli_error("% LLDP is not enabled\n") == "LLDP is not enabled"
    assert cli_error("% Invalid input detected at '^' marker.") == (
        "Invalid input detected at '^' marker.")  # fmt: skip
    assert cli_error("VLAN Name\n----") is None


class _FakeClient:
    """Switch ohne Netz: Ausgaben aus den Beispieldaten je Adresse."""

    by_host = {sw["host"]: sw for sw in SAMPLE["switches"]}

    def __init__(self, entry, username, password, known_key=None):
        self.entry, self.host_key = entry, known_key or f"ssh-ed25519 KEY{entry}"
        if entry == "10.0.0.99":
            raise paramiko.AuthenticationException("auth failed")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def run(self, cmd):
        if cmd not in sc.ALLOWED:
            raise ReadOnlyViolation(cmd)
        return self.by_host[self.entry]["outputs"].get(cmd, "% Invalid input detected\n")


def test_lauf_mit_teilfehler(monkeypatch):
    monkeypatch.setattr(tasks, "SwitchClient", _FakeClient)
    monkeypatch.setattr(tasks, "submit_result", lambda run_id, result, via_agent=False: None)
    monkeypatch.setattr(tasks, "confirmed", lambda result, _: result)
    target = Target(platform="cisco_switch", name="Switches A", username="ro", password="pw",
                    read_scope="10.0.0.11\n10.0.0.2\n10.0.0.99",
                    known_hosts={"10.0.0.2": "ssh-ed25519 PINNED"})  # fmt: skip
    result = tasks.collect_once("run-1", target)
    assert result.status == "completed"
    assert {d.name for d in result.inventory.devices} == {"sw-acc-01", "sw-core-01"}
    assert result.host_keys == {"10.0.0.11": "ssh-ed25519 KEY10.0.0.11",
                                "10.0.0.2": "ssh-ed25519 PINNED"}  # fmt: skip
    assert "Switches: 2 gelesen" in result.message
    assert "10.0.0.99: Anmeldung abgelehnt (SSH" in result.message
    only_bad = target.model_copy(update={"read_scope": "10.0.0.99"})
    failed = tasks.collect_once("run-2", only_bad)
    assert failed.status == "failed" and "Anmeldung abgelehnt" in failed.message
    empty = target.model_copy(update={"read_scope": ""})
    assert "keine Switch-Adressen" in tasks.collect_once("run-3", empty).message


def test_diagnose_ssh():
    msg = diagnose.device_error(paramiko.AuthenticationException("x"), "cisco_switch")
    assert msg.startswith("Anmeldung abgelehnt (SSH") and "network-operator" in msg
    err = paramiko.ssh_exception.NoValidConnectionsError({("192.0.2.1", 22): OSError("refused")})
    assert "Port 22 abgelehnt" in diagnose.device_error(err, "cisco_switch")


# -- echter SSH-Server (paramiko) im Test: Exec ohne PTY, Host-Schluessel -----------------------


class _Server(paramiko.ServerInterface):
    def __init__(self, log):
        self.log = log
        self.event = threading.Event()

    def check_auth_password(self, username, password):
        ok = (username, password) == ("ro", "pw")
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):  # noqa: ARG002
        return "password"

    def check_channel_request(self, kind, chanid):  # noqa: ARG002
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_pty_request(self, *args):  # noqa: ARG002
        self.log.append("pty")
        return False

    def check_channel_shell_request(self, channel):  # noqa: ARG002
        self.log.append("shell")
        return False

    def check_channel_exec_request(self, channel, command):
        cmd = command.decode()
        self.log.append(cmd)

        def answer():
            time.sleep(0.05)  # erst nach der Zusage an den Client antworten
            channel.sendall(f"output of {cmd}\n".encode())
            channel.send_exit_status(0)
            channel.close()

        threading.Thread(target=answer, daemon=True).start()
        return True


def _serve(key, log, connections=4):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(connections)

    def loop():
        for _ in range(connections):
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            t = paramiko.Transport(conn)
            t.add_server_key(key)
            try:  # bricht der Client ab (fremder Schluessel), weiter mit der naechsten
                t.start_server(server=_Server(log))
            except (OSError, EOFError, paramiko.SSHException):
                t.close()

    threading.Thread(target=loop, daemon=True).start()
    return sock, sock.getsockname()[1]


def test_ssh_exec_ohne_terminal_und_host_schluessel():
    key = paramiko.RSAKey.generate(2048)
    log: list[str] = []
    sock, port = _serve(key, log)
    try:
        entry = f"127.0.0.1:{port}"
        with sc.SwitchClient(entry, "ro", "pw") as client:
            assert client.run("show version") == "output of show version\n"
            assert client.run("show vlan brief").startswith("output of show vlan brief")
        assert log == ["show version", "show vlan brief"]  # kein PTY, keine Shell
        learned = client.host_key
        assert learned == f"{key.get_name()} {key.get_base64()}"
        assert sc.fingerprint(learned).startswith("SHA256:")
        # gespeicherter Schluessel passt: weiter; anderer Schluessel: Abbruch vor der Anmeldung
        with sc.SwitchClient(entry, "ro", "pw", known_key=learned) as again:
            again.run("show version")
        other = paramiko.RSAKey.generate(2048)
        with pytest.raises(sc.HostKeyChanged, match="Host-Schluessel hat sich geaendert"):
            changed = f"{other.get_name()} {other.get_base64()}"
            sc.SwitchClient(entry, "ro", "pw", known_key=changed).__enter__()
        with pytest.raises(paramiko.AuthenticationException):
            sc.SwitchClient(entry, "ro", "falsch", known_key=learned).__enter__()
    finally:
        sock.close()
