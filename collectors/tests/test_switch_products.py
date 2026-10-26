"""Weitere Switch- und Router-Produkte (0.68.0, ADR 0025 Nr. 11): Erkennung nach `show version`,
Catalyst 1300 (Business-CLI, begrenzte Sitzung ohne Exec), Juniper MX/EX (Junos, JSON),
Catalyst 9200L/3650/4500 (IOS/IOS-XE); Nexus 9000 deckt `test_switches` (NX-OS) ab."""

import socket
import threading

import paramiko
import pytest
import switch_products as sp
from sot_gateway.readonly import ReadOnlyViolation

from collectors.cisco_switch import cbs, junos, tasks
from collectors.cisco_switch import client as sc
from collectors.cisco_switch import normalize as sn
from collectors.common import diagnose
from collectors.common.dto import Target


def test_erkennung_nach_show_version():
    assert sn.detect_platform(sp.C1300["show version"]) == "cbs"
    assert sn.detect_platform(sp.MX["show version"]) == "junos"
    assert sn.detect_platform(sp.EX["show version"]) == "junos"
    for text in (sp.C9200L, sp.C3650, sp.C4500):
        assert sn.detect_platform(text) == "ios"
    # Positivliste: nur show-Befehle, Junos mit JSON ohne Seiten, Sitzungsbefehle getrennt
    assert all(c.startswith("show ") for c in sc.ALLOWED)
    assert {junos.CMD_INTERFACES, junos.CMD_LLDP, junos.CMD_ARP} <= set(sc.COMMANDS["junos"])
    assert set(sc.SESSION.values()).isdisjoint(sc.ALLOWED)
    with pytest.raises(ReadOnlyViolation):
        sc.SwitchClient("192.0.2.1", "ro", "pw").run("terminal datadump")


@pytest.mark.parametrize(("text", "name", "model", "version", "serial"), [
    (sp.C9200L, "acc-9200l-01", "C9200L-48P-4X", "17.9.4a", "JAE25110ABC"),
    (sp.C3650, "dist-3650-01", "WS-C3650-48PS", "16.12.10a", "FDO2124ABCD"),
    (sp.C4500, "core-4506-01", "WS-C4506-E", "15.2(2)E9", "FOX1234ABCD"),
])  # fmt: skip
def test_catalyst_ios_modelle(text, name, model, version, serial):
    inv = sn.switch_inventory("10.0.2.1", "ios", {"show version": text})
    (dev,) = inv.devices
    assert (dev.name, dev.model, dev.version, dev.serial, dev.role) == (
        name, model, version, serial, "switch")  # fmt: skip


def test_catalyst_4500_mac_tabelle():
    outputs = {"show version": sp.C4500, "show mac address-table": sp.C4500_MAC}
    inv = sn.switch_inventory("10.0.2.4", "ios", outputs)
    assert [(m.port, m.vlan, m.mac) for m in inv.macs] == [
        ("Gi3/1", 10, "00:50:56:01:00:11"), ("Gi3/2", 20, "00:50:56:01:00:12")]  # fmt: skip


def test_catalyst_1300():
    notes: list[str] = []
    inv = sn.switch_inventory("10.0.1.30", "cbs", sp.C1300, notes)
    (dev,) = inv.devices
    assert (dev.name, dev.model, dev.serial, dev.version, dev.role, dev.mgmt_ip) == (
        "acc-1300-01", "C1300-24T-4X", "PSZ27161ABC", "4.1.3.36", "switch",
        "10.0.1.30")  # fmt: skip
    ports = {i.name: i for i in inv.interfaces}
    srv = ports["Gi1/0/1"]
    assert (srv.status, srv.speed, srv.duplex, srv.mode, srv.untagged_vlan) == (
        "up", "1000", "Full", "access", 10)  # fmt: skip
    assert srv.description == "srv-esx01 vmnic0" and srv.neighbor == "srv-esx01.example.ch"
    assert ports["Gi1/0/2"].status == "down" and ports["Gi1/0/2"].speed is None
    # VLANs aus `show vlan`, auch aus der umgebrochenen Zeile
    up = ports["Te1/0/1"]
    assert (up.mode, up.native_vlan, up.tagged_vlans, up.trunk_vlans) == ("tagged", 1, [10, 20],
                                                                          "10,20")  # fmt: skip
    assert (up.neighbor, up.neighbor_port) == ("nx-core-01", "Ethernet1/49")
    assert ports["Po1"].kind == "lag" and ports["Po1"].tagged_vlans == [10, 20]
    assert ports["Gi1/0/5"].lag == ports["Gi1/0/6"].lag == "Po1"
    nb = {n.port: n for n in inv.neighbors}
    assert nb["Te1/0/2"].remote_caps == ["B", "R"]
    # MAC-Tabelle ohne eigene (self) und Multicast, ARP mit VLAN-Interface
    assert [(m.port, m.vlan) for m in inv.macs] == [("Gi1/0/1", 10), ("Gi1/0/3", 1),
                                                    ("Te1/0/1", 10)]  # fmt: skip
    assert {(a.ip, a.interface) for a in inv.arps} == {("10.0.1.1", "Vl1"),
                                                       ("10.10.0.21", "Vl10")}  # fmt: skip
    assert [(v.vid, v.name) for v in inv.vlans] == [(1, "1"), (10, "Server"), (20, "Clients")]
    assert notes == []
    assert cbs.expand_ports("gi1/0/2-4,Po2-3, te1/0/1") == [
        "Gi1/0/2", "Gi1/0/3", "Gi1/0/4", "Po2", "Po3", "Te1/0/1"]  # fmt: skip


def test_juniper_mx():
    notes: list[str] = []
    inv = sn.switch_inventory("10.0.0.40", "junos", sp.MX, notes)
    (dev,) = inv.devices
    assert (dev.name, dev.role, dev.model, dev.serial, dev.version) == (
        "mx-edge-01", "router", "mx240", "JN12A3B4CAFC", "21.4R3-S5.4")  # fmt: skip
    ports = {i.name: i for i in inv.interfaces}
    assert not {"pfe-0/0/0", "cbp0"} & set(ports)  # interne Interfaces fallen weg
    member = ports["xe-0/0/0"]
    assert (member.lag, member.speed, member.duplex, member.status) == ("ae0", "10gbps", "full",
                                                                        "up")  # fmt: skip
    assert (member.neighbor, member.neighbor_port) == ("nx-core-01", "Ethernet1/1")
    # Port-ID als MAC: die Beschreibung des Ports gilt
    assert ports["xe-0/0/1"].neighbor_port == "Ethernet1/1"
    assert ports["ae0"].kind == "lag" and ports["ae0"].tagged_vlans == [100, 200]
    unit = ports["ae0.100"]
    assert (unit.kind, unit.parent, unit.tagged_vlans, unit.ips) == (
        "vlan", "ae0", [100], ["10.100.0.1/24", "2001:db8:100::1/64"])  # fmt: skip
    assert ports["ge-0/0/9"].ips == ["198.51.100.2/30"]  # Unit 0 ohne Tag: am Port
    assert ports["ge-0/0/8"].enabled is False and ports["ge-0/0/8"].status == "disabled"
    assert ports["lo0"].ips == ["10.255.0.1/32"] and ports["lo0"].kind == "loopback"
    nets = {(s.prefix, s.context_name, s.vlan) for s in inv.subnets}
    assert ("10.100.0.0/24", "ae0.100", 100) in nets and ("198.51.100.0/30", "ge-0/0/9",
                                                          None) in nets  # fmt: skip
    assert not any(s.prefix.startswith("fe80") for s in inv.subnets)
    assert [(m.port, m.vlan, m.kind) for m in inv.macs] == [("ae0", 100, "dynamic"),
                                                            ("ae0", 100, "static")]  # fmt: skip
    assert {a.ip for a in inv.arps} == {"10.100.0.21", "198.51.100.1"}
    assert notes == []
    # Befehle, die es auf dem MX nicht gibt, ohne Hinweis
    err = sn.cli_error(sp.MX[junos.CMD_VLANS])
    assert err == "syntax error, expecting <command>: vlans"
    assert sn.quiet("junos", junos.CMD_VLANS, err)
    assert not sn.quiet("junos", junos.CMD_INTERFACES, err)


def test_juniper_ex_vlans_und_mac():
    inv = sn.switch_inventory("10.0.0.41", "junos", sp.EX)
    assert inv.devices[0].role == "switch"
    ports = {i.name: i for i in inv.interfaces}
    assert (ports["ge-0/0/1"].mode, ports["ge-0/0/1"].untagged_vlan) == ("access", 10)
    assert (ports["xe-0/2/0"].mode, ports["xe-0/2/0"].trunk_vlans) == ("tagged", "10,20")
    assert "irb" not in ports and ports["irb.10"].ips == ["10.10.0.2/24"]
    assert [(m.port, m.vlan) for m in inv.macs] == [("ge-0/0/1", 10)]
    assert [(v.vid, v.name) for v in inv.vlans] == [(10, "Server"), (20, "Clients")]
    # nicht lesbares JSON: Teil leer, Hinweis
    notes: list[str] = []
    broken = dict(sp.EX, **{junos.CMD_INTERFACES: '{"interface-information": ['})
    assert sn.switch_inventory("10.0.0.41", "junos", broken, notes).interfaces == []
    assert notes and "Ausgabe nicht auswertbar" in notes[0]


def test_lauf_mit_mehreren_produkten(monkeypatch):
    outputs = {"10.0.1.30": sp.C1300, "10.0.0.40": sp.MX}

    class Fake:
        def __init__(self, entry, username, password, known_key=None):
            self.entry, self.host_key, self.mode = entry, "ssh-ed25519 K", "exec"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def version(self):
            if self.entry == "10.0.1.30":
                self.mode = "shell"
            return outputs[self.entry]["show version"]

        def prepare(self, platform):
            self.prepared = platform

        def run(self, cmd):
            assert cmd in sc.ALLOWED
            return outputs[self.entry].get(cmd, "% Invalid input\n")

    monkeypatch.setattr(tasks, "SwitchClient", Fake)
    monkeypatch.setattr(tasks, "submit_result", lambda run_id, result, via_agent=False: None)
    monkeypatch.setattr(tasks, "confirmed", lambda result, _: result)
    target = Target(platform="cisco_switch", name="Netz", username="ro", password="pw",
                    read_scope="10.0.1.30\n10.0.0.40")  # fmt: skip
    result = tasks.collect_once("run-p", target)
    assert result.status == "completed"
    assert "Switches: 2 gelesen (Catalyst 1200/1300 (Business-CLI), Junos)" in result.message
    assert "Hinweise" not in result.message  # MX ohne ELS-Befehle: kein Hinweis
    assert {d.role for d in result.inventory.devices} == {"switch", "router"}
    assert any(s.prefix == "10.100.0.0/24" for s in result.inventory.subnets)


# -- begrenzte Sitzung: Catalyst 1300 ohne Exec (echter SSH-Server im Test) -----------------------


class _C1300(paramiko.ServerInterface):
    """Wie ein Catalyst 1300: Exec abgelehnt, Terminal mit Anmeldung in der Sitzung, Seiten mit
    „More:“ bis `terminal datadump`."""

    def __init__(self, log):
        self.log = log

    def check_auth_password(self, username, password):  # noqa: ARG002 - fragt in der Sitzung
        return paramiko.AUTH_SUCCESSFUL

    def get_allowed_auths(self, username):  # noqa: ARG002
        return "password"

    def check_channel_request(self, kind, chanid):  # noqa: ARG002
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel, command):  # noqa: ARG002
        self.log.append("exec")
        return False

    def check_channel_pty_request(self, *args):  # noqa: ARG002
        return True

    def check_channel_shell_request(self, channel):
        threading.Thread(target=self._session, args=(channel,), daemon=True).start()
        return True

    def _line(self, chan) -> str:
        buf = b""
        while not buf.endswith(b"\n"):
            data = chan.recv(1)
            if not data:
                raise EOFError
            buf += data
        return buf.decode().strip("\r\n")

    def _session(self, chan):
        try:
            chan.send(b"\r\n\r\nUser Name:")
            user = self._line(chan)
            chan.send(b"\r\nPassword:")
            password = self._line(chan)
            self.log.append(f"login {user}/{'*' * len(password)}")
            if (user, password) != ("ro", "pw"):
                chan.send(b"\r\n\r\nUser Name:")
                self._line(chan)
                return
            prompt = b"\r\nacc-1300-01#"
            chan.send(prompt)
            paging = True
            while True:
                line = self._line(chan)
                self.log.append(line)
                if line == "terminal datadump":
                    paging, out = False, b""
                elif line in sp.C1300:
                    out = sp.C1300[line].replace("\n", "\r\n").encode()
                else:
                    out = b"% Unrecognized command\r\n"
                chan.send(line.encode() + b"\r\n")
                if paging and out.count(b"\r\n") > 3:
                    head, _, tail = out.partition(b"\r\n")
                    chan.send(head + b"\r\nMore: <space>,  Quit: q or CTRL+Z, One line: <return> ")
                    self.log.append(f"key:{chan.recv(1).decode()!r}")
                    chan.send(b"\x08" * 10 + tail)
                else:
                    chan.send(out)
                chan.send(prompt)
        except (EOFError, OSError):
            return


def _serve(log, connections=12):
    key = paramiko.RSAKey.generate(2048)
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
            try:
                t.start_server(server=_C1300(log))
            except (OSError, EOFError, paramiko.SSHException):
                t.close()

    threading.Thread(target=loop, daemon=True).start()
    return sock, sock.getsockname()[1]


def test_catalyst_1300_ueber_begrenzte_sitzung():
    log: list[str] = []
    sock, port = _serve(log)
    try:
        target = Target(platform="cisco_switch", name="Access", username="ro", password="pw",
                        read_scope=f"127.0.0.1:{port}")  # fmt: skip
        sw = tasks.read_switch(f"127.0.0.1:{port}", target)
        assert (sw["platform"], sw["mode"]) == ("cbs", "shell")
        # Exec abgelehnt -> Sitzung; danach nur Anmeldung, die Befehle der Positivliste und das
        # Leerzeichen bei „More“ (vor `terminal datadump`)
        sent = [x for x in log if x != "exec"]
        assert sent[0] == "login ro/**" and sent[1] == "show version"
        assert sent[2] == "key:' '" and sent[3] == "terminal datadump"
        assert sent[4:] == list(sc.COMMANDS["cbs"][1:])
        assert sw["outputs"]["show version"].startswith("Active-image:")
        assert "More:" not in sw["outputs"]["show version"]
        assert sw["notes"] == []
        inv = sn.normalize({"switches": [sw]})
        assert inv.devices[0].name == "acc-1300-01"
        assert {i.name for i in inv.interfaces} >= {"Gi1/0/1", "Te1/0/1", "Po1"}
        # in der Sitzung gilt dieselbe Positivliste
        with sc.SwitchClient(f"127.0.0.1:{port}", "ro", "pw") as client:
            client.version()
            assert client.mode == "shell"
            for bad in ("configure terminal", "show running-config", "show version\nreload",
                        "terminal datadump"):  # fmt: skip
                with pytest.raises(ReadOnlyViolation):
                    client.run(bad)
        # falsches Passwort: Anmeldung in der Sitzung abgelehnt
        with pytest.raises(sc.SessionLoginFailed):
            with sc.SwitchClient(f"127.0.0.1:{port}", "ro", "falsch") as client:
                client.version()
        msg = diagnose.device_error(sc.SessionLoginFailed("x"), "cisco_switch")
        assert msg.startswith("Anmeldung abgelehnt (SSH-Sitzung") and "Stufe 1" in msg
    finally:
        sock.close()
