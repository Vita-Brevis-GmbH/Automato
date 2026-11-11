"""Cisco-Switches (ADR 0025): Nur-Lese-Schutz, SSH ohne Terminal mit Host-Schluessel,
Normalisierung der show-Ausgaben (IOS-XE und NX-OS), Lauf mit Teilfehlern."""

import re
import socket
import threading
import time
from unittest.mock import patch

import paramiko
import pytest
from sot_gateway.readonly import ReadOnlyViolation

from collectors.cisco_switch import client as sc
from collectors.cisco_switch import normalize as sn
from collectors.cisco_switch import routing, tasks
from collectors.cisco_switch.normalize import (
    cli_error,
    mac_text,
    normalize,
    short_name,
    vlan_ranges,
    vlan_set,
)
from collectors.common import diagnose
from collectors.common.dto import NormalizedInterface, Target
from collectors.common.i18n import use

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
    assert ports[("sw-core-01", "Po10")].attrs == {"vpc": "10", "vpc_status": "up",
                                                    "vpc_consistency": "success"}  # fmt: skip
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


def test_spanning_tree_und_vpc():
    """0.65.0: blockierte VLANs und Root-Port je Port (IOS-Vorlage auch fuer NX-OS), `show vpc`
    selbst ausgewertet (Domaene, Peer-Link, Status, Konsistenz)."""
    inv = normalize(SAMPLE)
    ports = {(i.device, i.name): i for i in inv.interfaces}
    assert ports[("sw-acc-01", "Te1/1/1")].attrs["stp_blocked"] == "30"
    assert ports[("sw-acc-01", "Po1")].attrs == {"stp_root": "10,30"}
    assert "stp_blocked" not in ports[("sw-core-01", "Eth1/3")].attrs
    peer = ports[("sw-core-01", "Po1")]
    assert peer.kind == "lag" and peer.attrs["vpc_peer_link"] == "1"
    assert peer.attrs["vpc_peer"] == "peer adjacency formed ok"
    info = sn.vpc_info("""vPC domain id : 7
Peer status : peer link is down
vPC Peer-link status
id    Port   Status Active vlans
1     Po1    down   -
vPC status
Id    Port   Status Consistency Reason   Active vlans
20    Po20   down*  failed      Compatibility check failed  -
""")
    assert (info["domain"], info["peer"], info["peer_link_status"]) == ("7", "peer link is down",
                                                                       "down")  # fmt: skip
    assert info["vpcs"] == [{"id": "20", "port": "Po20", "status": "down*",
                             "consistency": "failed"}]  # fmt: skip
    assert sn.vpc_info("% Invalid command")["domain"] is None
    # passt eine Vorlage nicht: der Teil bleibt leer, der Switch wird trotzdem eingelesen
    real = sn.parse

    def broken(platform, command, text):
        if command == "show spanning-tree":
            raise ValueError("Vorlage passt nicht")
        return real(platform, command, text)

    raw = {"switches": [dict(sw, notes=[]) for sw in SAMPLE["switches"]]}
    with patch.object(sn, "parse", broken):
        inv = normalize(raw)
    assert {d.name for d in inv.devices} == {"sw-acc-01", "sw-core-01"}
    assert not any("stp_blocked" in (i.attrs or {}) for i in inv.interfaces)
    assert (
        "show spanning-tree: Ausgabe nicht auswertbar (ValueError)" in raw["switches"][0]["notes"]
    )


def test_mac_tabelle_gekappt(monkeypatch):
    monkeypatch.setattr(sn, "MAX_MACS", 3)
    raw = {"switches": [dict(sw, notes=[]) for sw in SAMPLE["switches"]]}
    inv = normalize(raw)
    assert len([m for m in inv.macs if m.device == "sw-acc-01"]) == 3
    assert raw["switches"][0]["notes"] == ["MAC-Tabelle: 3 Eintraege ueber 3 nicht uebernommen"]


def test_ports_ohne_namen_nicht_uebernommen(monkeypatch):
    """1.7.0 (Anwender 2026-11-11: NetBox „name: This field is required“): eine Zeile der Ausgabe
    ohne Port ist kein Port - gezaehlt als Hinweis im Lauf."""
    real = sn.switch_inventory

    def with_blank(host, platform, outputs, notes=None):
        part = real(host, platform, outputs, notes)
        part.interfaces.append(NormalizedInterface(device=part.devices[0].name, name=" "))
        return part

    monkeypatch.setattr(sn, "switch_inventory", with_blank)
    raw = {"switches": [dict(sw, notes=[]) for sw in SAMPLE["switches"]]}
    inv = normalize(raw)
    assert inv.interfaces and all(i.name.strip() for i in inv.interfaces)
    assert "1 Ports ohne Namen nicht uebernommen" in raw["switches"][0]["notes"]


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

    mode = "exec"

    def version(self):
        return self.run("show version")

    def prepare(self, platform):
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


# -- 1.9.0: Pruefung parallel, kurze Probe, eine Sitzung statt Neuanmeldung je Befehl ----------


def test_pruefung_parallel_in_reihenfolge(monkeypatch):
    """Anwender: „das prüfen geht wesentlich länger … es bleibt vor allem bei den Switches lange
    hängen“ - die Switches werden gleichzeitig geprueft, die Zeilen bleiben in der Reihenfolge des
    Lesebereichs, die Sprache der Konsole gilt auch in den Threads."""

    class Slow(_FakeClient):
        def version(self):
            time.sleep(0.3)
            if self.entry == "10.0.0.2":
                self.mode = "shell"
            return super().version()

    monkeypatch.setattr(tasks, "SwitchClient", Slow)
    target = Target(platform="cisco_switch", name="Switches A", username="ro", password="pw",
                    read_scope="10.0.0.11\n10.0.0.99\n10.0.0.2")  # fmt: skip
    start = time.monotonic()
    ok, msg = tasks.check(target)
    assert time.monotonic() - start < 0.75  # seriell 0,9 s
    assert not ok  # 10.0.0.99: Anmeldung abgelehnt
    assert msg.startswith("10.0.0.11: erreichbar (IOS/IOS-XE, Host-Schluessel ")
    assert msg.index("; 10.0.0.99: Anmeldung abgelehnt (SSH") < msg.index(
        "; 10.0.0.2: erreichbar (NX-OS, Sitzung, Host-Schluessel "
    )
    with use("en"):
        ok, msg = tasks.check(target.model_copy(update={"read_scope": "10.0.0.11, 10.0.0.2"}))
    assert ok and msg.startswith("10.0.0.11: reachable (IOS/IOS-XE, host key ")
    assert "; 10.0.0.2: reachable (NX-OS, session, host key" in msg


def test_dauer_in_der_meldung(monkeypatch):
    """Anwender: „Der Switchrun geht auch sehr lange“ - die Meldung nennt die Dauer des Laufs und
    den laengsten Switch (Ausreisser), „Sitzung“ bei der begrenzten Sitzung."""
    assert tasks._span(0.4) == "0 s" and tasks._span(45.2) == "45 s"
    assert tasks._span(130) == "2 min 10 s" and tasks._span(120.3) == "2 min"
    switches = [{"host": "10.0.0.11", "name": "sw-01", "seconds": 45.2, "mode": "shell"},
                {"host": "10.0.0.12", "seconds": 3.0, "mode": "exec"}]  # fmt: skip
    assert tasks.durations(switches, 130) == (
        "Dauer 2 min 10 s, längster Switch sw-01 (45 s, Sitzung)"
    )
    assert tasks.durations([{"host": "10.0.0.12", "seconds": 61, "mode": "exec"}], 62) == (
        "Dauer 1 min 2 s"
    )
    assert tasks.durations([{"host": "10.0.0.12"}], 1) is None  # Beispieldaten: ungemessen

    class Slow(_FakeClient):
        def version(self):
            if self.entry == "10.0.0.2":
                time.sleep(1.2)
                self.mode = "shell"
            return super().version()

    monkeypatch.setattr(tasks, "SwitchClient", Slow)
    monkeypatch.setattr(tasks, "submit_result", lambda run_id, result, via_agent=False: None)
    monkeypatch.setattr(tasks, "confirmed", lambda result, _: result)
    target = Target(platform="cisco_switch", name="Switches A", username="ro", password="pw",
                    read_scope="10.0.0.11\n10.0.0.2")  # fmt: skip
    result = tasks.collect_once("run-1", target)
    assert result.status == "completed"
    assert re.search(r" · Dauer [12] s, längster Switch sw-core-01 \([12] s, Sitzung\)",
                     result.message)  # fmt: skip


class _Chan:
    """Exec-Kanal ohne Netz: `script` = Daten bzw. `None` (eine Wartezeit ohne Daten)."""

    def __init__(self, script, wait=0.05):
        self.script, self.wait, self.timeout = list(script), wait, None

    def settimeout(self, value):
        self.timeout = value

    def exec_command(self, _cmd):
        return None

    def recv(self, _n):
        step = self.script.pop(0) if self.script else None
        if step is None:
            time.sleep(self.wait)
            raise TimeoutError("timed out")  # wie socket.timeout
        return step

    def recv_stderr_ready(self):
        return False

    def close(self):
        return None


class _ChanTransport:
    def __init__(self, chan):
        self.chan = chan

    def open_session(self, timeout=None):  # noqa: ARG002
        return self.chan


def test_probe_wartet_nur_bis_zu_den_ersten_daten():
    """Exec-Versuch (`show version`): ohne Daten nach `first` Abbruch (dann die Sitzung); kommt
    schon ein Teil, wird nicht abgebrochen; fragt das Geraet nach der Anmeldung, sofort zurueck."""
    assert 6.0 <= sc.PROBE_TIMEOUT <= 8.0
    client = sc.SwitchClient("192.0.2.1", "ro", "pw")
    silent = _Chan([None] * 100)
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        client._exec(_ChanTransport(silent), "show version", 120, first=0.3)
    assert time.monotonic() - start < 1.0 and silent.timeout <= 0.3
    # erste Daten vor der Frist, danach eine laengere Pause: wird ganz gelesen
    slow = _Chan([b"Cisco IOS XE Software\n"] + [None] * 10 + [b"uptime is 1 week\n", b""])
    text = client._exec(_ChanTransport(slow), "show version", 120, first=0.3)
    assert text == "Cisco IOS XE Software\nuptime is 1 week\n"
    # Anmeldung im Exec (Business-CLI): sofort zurueck, `version` nimmt dann die Sitzung
    login = _Chan([b"\r\n\r\nUser Name:"] + [None] * 100)
    start = time.monotonic()
    text = client._exec(_ChanTransport(login), "show version", 120, first=0.3)
    assert sc.LOGIN.search(text) and time.monotonic() - start < 0.5
    # ohne Probe: Frist fuer die ganze Antwort wie bisher
    with pytest.raises(TimeoutError, match="keine vollstaendige Antwort"):
        client._exec(_ChanTransport(_Chan([b"x"] + [None] * 100)), "show vlan brief", 0.3)


# -- Geraet schliesst die Verbindung nach jedem Exec (viele IOS-Switches) ----------------------

_IOS = {sw["host"]: sw for sw in SAMPLE["switches"]}["10.0.0.11"]["outputs"]
_BIG_RIB = "".join(f"B        10.{i // 256}.{i % 256}.0/24 [20/0] via 198.51.100.1, 1w0d\n"
                   for i in range(400))  # fmt: skip


class _IosServer(paramiko.ServerInterface):
    """Wie ein Catalyst mit IOS-XE: Exec geht, danach schliesst das Geraet die ganze Verbindung;
    Terminal-Sitzung mit Prompt (Anmeldung schon per SSH), Echo, Seiten bis `terminal length 0`.
    `shell=False`: keine Sitzung (nur Exec)."""

    def __init__(self, log, transport, shell=True):
        self.log, self.transport, self.shell = log, transport, shell
        self.served = False  # nach dem Exec kein weiterer Kanal auf dieser Verbindung

    def check_auth_password(self, username, password):
        ok = (username, password) == ("ro", "pw")
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):  # noqa: ARG002
        return "password"

    def check_channel_request(self, kind, chanid):  # noqa: ARG002
        if kind == "session" and not self.served:
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_pty_request(self, *args):  # noqa: ARG002
        return self.shell

    def check_channel_shell_request(self, channel):
        if not self.shell:
            return False
        self.log.append("shell")
        threading.Thread(target=self._session, args=(channel,), daemon=True).start()
        return True

    def check_channel_exec_request(self, channel, command):
        cmd = command.decode()
        self.log.append(f"exec {cmd}")
        self.served = True

        def answer():
            time.sleep(0.05)
            channel.sendall(_IOS.get(cmd, "% Invalid input detected at '^' marker.\n").encode())
            channel.send_exit_status(0)
            channel.close()
            self.transport.close()  # wie IOS: die Verbindung endet mit dem Exec

        threading.Thread(target=answer, daemon=True).start()
        return True

    @staticmethod
    def _line(chan) -> str:
        buf = b""
        while not buf.endswith((b"\r", b"\n")):
            data = chan.recv(1)
            if not data:
                raise EOFError
            buf += data
        return buf.decode().strip("\r\n")

    def _session(self, chan):
        prompt = b"\r\nsw-acc-01#"
        try:
            chan.send(b"\r\nZugang nur fuer Berechtigte\r\n" + prompt)
            paging = True
            while True:
                line = self._line(chan)
                self.log.append(line)
                if line == "terminal length 0":
                    paging, out = False, ""
                elif line == "show ip route":
                    out = _BIG_RIB
                else:
                    out = _IOS.get(line, "% Invalid input detected at '^' marker.\n")
                chan.send(line.encode() + b"\r\n")
                out = out.replace("\n", "\r\n").encode()
                if paging and out.count(b"\r\n") > 3:
                    head, _, tail = out.partition(b"\r\n")
                    chan.send(head + b"\r\n --More-- ")
                    self.log.append(f"key:{chan.recv(1).decode()!r}")
                    chan.send(b"\x08" * 9 + tail)
                else:
                    for i in range(0, len(out), 1000):  # in Stuecken wie ueber das Netz
                        chan.send(out[i : i + 1000])
                chan.send(prompt)
        except (EOFError, OSError):
            return


def _serve_ios(log, shell=True, connections=30):
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
            log.append("connect")
            t = paramiko.Transport(conn)
            t.add_server_key(key)
            try:
                t.start_server(server=_IosServer(log, t, shell))
            except (OSError, EOFError, paramiko.SSHException):
                t.close()

    threading.Thread(target=loop, daemon=True).start()
    return sock, sock.getsockname()[1]


def test_geraet_schliesst_nach_exec_uebrige_befehle_in_einer_sitzung():
    """Anwender: „Der Switchrun geht auch sehr lange“ - ein IOS-Switch, der die Verbindung nach
    jedem Exec schliesst, wird nach `show version` mit **einer** weiteren Anmeldung in der
    begrenzten Sitzung gelesen (Seitenumbruch zuerst aus), statt je Befehl neu anzumelden."""
    log: list[str] = []
    sock, port = _serve_ios(log)
    try:
        entry = f"127.0.0.1:{port}"
        target = Target(platform="cisco_switch", name="Access", username="ro", password="pw",
                        read_scope=entry)  # fmt: skip
        sw = tasks.read_switch(entry, target)
    finally:
        sock.close()
    assert (sw["platform"], sw["mode"]) == ("ios", "shell")
    assert log.count("connect") == 2 and log.count("shell") == 1  # Exec-Probe + eine Sitzung
    assert [x for x in log if x.startswith("exec ")] == ["exec show version"]
    sent = [x for x in log if x not in ("connect", "shell") and not x.startswith("exec ")]
    assert sent[0] == "terminal length 0"  # vor dem ersten Befehl, ohne „More“
    assert sent[1:] == [*sc.COMMANDS["ios"][1:], routing.IOS_RIB, *routing.COMMANDS["ios"][1:]]
    assert isinstance(sw["seconds"], float)
    # Ausgaben wie per Exec: ohne Echo und Prompt, ohne „More“ und Wagenruecklauf
    for cmd in sc.COMMANDS["ios"]:
        if cmd in _IOS:
            assert sw["outputs"][cmd].strip() == _IOS[cmd].strip(), cmd
    assert sw["outputs"][routing.IOS_RIB] == _BIG_RIB
    inv = sn.normalize({"switches": [sw]})
    ref = sn.normalize({"switches": [dict(SAMPLE["switches"][0], notes=[])]})
    assert [d.name for d in inv.devices] == ["sw-acc-01"]
    assert {(i.name, i.mode, i.description) for i in inv.interfaces} == {
        (i.name, i.mode, i.description) for i in ref.interfaces if i.device == "sw-acc-01"
    }


def test_sitzung_kuerzt_routing_und_oeffnet_neu(monkeypatch):
    """Routing in der Sitzung (ADR 0032): eine zu grosse Text-Ausgabe wird wie per Exec bis zur
    letzten ganzen Zeile gekuerzt (`cut`); der Rest ist noch unterwegs - der naechste Befehl
    laeuft in einer neuen Sitzung und bekommt seine eigene Ausgabe."""
    monkeypatch.setattr(sc, "MAX_OUTPUT", 5000)
    log: list[str] = []
    sock, port = _serve_ios(log)
    try:
        with sc.SwitchClient(f"127.0.0.1:{port}", "ro", "pw") as client:
            client.version()
            client.prepare("ios")
            assert client.mode == "exec"
            assert client.run("show vlan brief").strip() == _IOS["show vlan brief"].strip()
            assert client.mode == "shell" and log.count("connect") == 2
            text = client.run(routing.IOS_RIB)
            assert client.cut == {routing.IOS_RIB}
            assert text.endswith("\n") and len(text) <= 5000 and _BIG_RIB.startswith(text)
            assert client.run("show ip arp").strip() == _IOS["show ip arp"].strip()
            assert log.count("connect") == 3 and log.count("shell") == 2
            # in der Sitzung gilt dieselbe Positivliste
            for bad in ("configure terminal", "show running-config", "terminal length 0"):
                with pytest.raises(ReadOnlyViolation):
                    client.run(bad)
    finally:
        sock.close()
    assert log.count("terminal length 0") == 2  # auch in der neuen Sitzung zuerst


def test_ohne_sitzung_weiter_je_befehl_neu_verbinden():
    """Laesst das Geraet keine Sitzung zu, bleibt es beim Exec mit Neuverbindung je Befehl."""
    log: list[str] = []
    sock, port = _serve_ios(log, shell=False)
    try:
        with sc.SwitchClient(f"127.0.0.1:{port}", "ro", "pw") as client:
            assert client.version().strip() == _IOS["show version"].strip()
            client.prepare("ios")
            for cmd in ("show vlan brief", "show ip arp", "show spanning-tree"):
                assert client.run(cmd).strip() == _IOS[cmd].strip()
            assert client.mode == "exec"
    finally:
        sock.close()
    execs = [x for x in log if x.startswith("exec ")]
    assert execs == ["exec show version", "exec show vlan brief", "exec show ip arp",
                     "exec show spanning-tree"]  # fmt: skip
    # Probe, ein Sitzungsversuch (abgelehnt), dann je Befehl eine Verbindung
    assert log.count("connect") == 5 and "shell" not in log
