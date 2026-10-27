"""Routing der Router und L3-Switches (ADR 0032): Positivliste nur mit `show`, Auswertung je
Plattform (IOS-XE, NX-OS, Junos, Arista EOS) in Kontexte je VRF, Routen und BGP-Nachbarn,
„nicht lesbar“ statt Fehler, Obergrenze fuer grosse Tabellen und der Lauf ueber den Collector."""

import pytest
import switch_routing as sr
from sot_gateway.readonly import ReadOnlyViolation

from collectors.cisco_switch import client as sc
from collectors.cisco_switch import routing, tasks
from collectors.common.dto import Target


def _by(inv, context):
    return {(r.table, r.prefix, r.protocol): r for r in inv.routes if r.context == context}


def _hops(route):
    return [(h.ip, h.interface) for h in route.next_hops]


def test_positivliste_nur_show():
    for platform, cmds in routing.COMMANDS.items():
        assert cmds and cmds[0] in (routing.IOS_RIB, routing.NXOS_RIB, routing.JUNOS_ROUTE,
                                    routing.EOS_RIB), platform  # fmt: skip
        for cmd in cmds:
            assert cmd.startswith("show ") and cmd in sc.ALLOWED
            assert "running" not in cmd and "config" not in cmd
    assert set(routing.PARTS) == routing.ALL and set(routing.PARSERS) == routing.ALL
    assert routing.PARTIAL <= routing.ALL  # nur Text-Ausgaben werden gekuerzt ausgewertet
    client = sc.SwitchClient("192.0.2.1", "ro", "pw")
    for bad in (
        "show running-config | section router bgp",
        "show ip bgp neighbors",
        "show ip route | redirect flash:x",
        "clear ip bgp *",
        "show route\nrequest",
    ):
        with pytest.raises(ReadOnlyViolation):
            client.run(bad)


def test_ios_xe_aussenrouter():
    notes: list[str] = []
    inv = routing.device("rt-edge-01", "ios", sr.IOS, {}, set(), notes)
    assert notes == []
    # Kontexte je VRF (Standard = das Geraet), die Management-VRF faellt weg
    assert [(c.context, c.kind, c.missing) for c in inv.contexts] == [
        ("rt-edge-01", "l3", []), ("rt-edge-01:CUST-A", "l3", [])]  # fmt: skip
    rib = _by(inv, "rt-edge-01")
    default = rib[("rib", "0.0.0.0/0", "bgp")]
    # Interface des Next-Hops aus dem angeschlossenen Netz, Kurzform wie im Inventar
    assert (default.distance, _hops(default)) == (20, [("198.51.100.1", "Gi0/0")])
    ecmp = rib[("rib", "10.10.0.0/16", "ospf")]
    assert _hops(ecmp) == [("10.0.0.2", "Gi0/1"), ("10.0.0.6", "Gi0/2")]
    assert rib[("rib", "10.30.0.0/16", "ospf")].attrs == {"route_type": "E2"}
    assert _hops(rib[("rib", "10.0.0.1/32", "local")]) == [(None, "Gi0/1")]  # eigene Adresse
    assert _hops(rib[("rib", "10.0.0.0/30", "connected")]) == [(None, "Gi0/1")]
    assert ("rib", "172.16.0.0/16", "bgp") in rib  # klassisch ohne Laenge (subnetted)
    assert ("rib", "203.0.113.128/25", "bgp") in rib  # umgebrochene Zeile
    # IPv6: Link-local-Next-Hop bleibt, Multicast faellt weg
    assert _hops(rib[("rib", "::/0", "bgp")]) == [("fe80::1", "Gi0/0")]
    assert ("rib", "ff00::/8", "local") not in rib
    # BGP-Pfade: bester und Backup ueber den zweiten Provider, AS-Pfad, intern
    paths = [r for r in inv.routes if r.table == "bgp" and r.prefix == "0.0.0.0/0"]
    assert [(r.best, _hops(r), r.attrs["as_path"]) for r in paths] == [
        (True, [("198.51.100.1", "Gi0/0")], "64500"),
        (False, [("198.51.100.9", "Gi0/3")], "64511 64500")]  # fmt: skip
    internal = [r for r in inv.routes if r.table == "bgp" and r.prefix == "172.16.0.0/16"]
    assert internal[0].attrs["internal"] is True and internal[0].attrs["local_pref"] == 100
    # Nachbarn: lokales AS, Typ, Zustand; „Idle (Admin)“ = abgeschaltet
    peers = {p.peer_ip: p for p in inv.peers}
    assert (peers["198.51.100.1"].remote_as, peers["198.51.100.1"].state) == (64500, "Established")
    assert peers["198.51.100.1"].attrs == {"local_as": 65001, "type": "ebgp"}
    assert peers["10.255.0.2"].attrs["type"] == "ibgp"
    assert (peers["198.51.100.13"].state, peers["198.51.100.13"].attrs["admin_status"]) == (
        "Idle", "down")  # fmt: skip
    vrf = _by(inv, "rt-edge-01:CUST-A")
    assert _hops(vrf[("rib", "0.0.0.0/0", "static")]) == [("10.50.0.254", "Gi0/4")]
    assert not any("192.168.99." in r.prefix for r in inv.routes)  # Mgmt-vrf


def test_nxos_core_mit_vrfs_und_hsrp():
    inv = routing.device("sw-core-01", "nxos", sr.NXOS, {}, set(), [])
    assert [c.context for c in inv.contexts] == ["sw-core-01", "sw-core-01:CUST-A"]
    rib = _by(inv, "sw-core-01")
    default = rib[("rib", "0.0.0.0/0", "bgp")]
    assert (_hops(default), default.attrs) == ([("10.0.0.1", "Eth1/49")], {"bgp": "ebgp"})
    # direkt angeschlossen ohne die eigene Adresse als Next-Hop; HSRP-Adresse gehoert dem Geraet
    assert _hops(rib[("rib", "10.0.0.0/30", "connected")]) == [(None, "Eth1/49")]
    assert rib[("rib", "10.10.0.1/32", "local")].attrs == {"virtual": True}  # auch beim Partner
    assert rib[("rib", "10.10.0.2/32", "local")].attrs == {}
    # nur die besten Next-Hops (`*via`)
    assert _hops(rib[("rib", "10.40.0.0/16", "ospf")]) == [("10.10.0.5", "Vl10"),
                                                           ("10.10.0.6", "Vl10")]  # fmt: skip
    assert ("rib", "::/0", "static") in rib and ("rib", "2001:db8:10::2/128", "local") in rib
    assert not any(r.prefix.startswith("fe80") for r in inv.routes)
    leaked = _by(inv, "sw-core-01:CUST-A")[("rib", "0.0.0.0/0", "bgp")]
    assert leaked.attrs["nexthop_vrf"] == "default"
    peers = {(p.context, p.peer_ip): p for p in inv.peers}
    assert peers[("sw-core-01", "10.0.0.1")].attrs == {"local_as": 65010, "type": "ebgp"}
    assert peers[("sw-core-01", "10.0.0.9")].state == "Idle"
    assert peers[("sw-core-01:CUST-A", "10.60.0.254")].remote_as == 65060
    assert not any("192.168.99" in r.prefix or "management" in r.context for r in inv.routes)


def test_junos_mit_routing_instanz():
    inv = routing.device("mx-edge-01", "junos", sr.JUNOS, {}, set(), [])
    assert [c.context for c in inv.contexts] == ["mx-edge-01", "mx-edge-01:CUST-A"]
    rib = _by(inv, "mx-edge-01")
    # aktiver Eintrag = RIB; Unit 0 heisst wie der Port (wie im Inventar)
    assert _hops(rib[("rib", "0.0.0.0/0", "bgp")]) == [("203.0.113.1", "xe-0/0/0")]
    assert rib[("rib", "0.0.0.0/0", "bgp")].distance == 170
    assert _hops(rib[("rib", "10.100.0.1/32", "local")]) == [(None, "ge-0/0/2.100")]
    assert len(rib[("rib", "10.200.0.0/16", "ospf")].next_hops) == 2
    # alle BGP-Pfade (inaktiver = Backup) und die schwebende statische Route
    paths = [r for r in inv.routes if r.table == "bgp"]
    assert [(r.best, r.attrs["as_path"], r.attrs["learned_from"]) for r in paths] == [
        (True, "64511", "203.0.113.1"), (False, "64512 64511", "203.0.113.5")]  # fmt: skip
    (static,) = (r for r in inv.routes if r.table == "static" and r.context == "mx-edge-01")
    assert (static.distance, _hops(static)) == (200, [("203.0.113.5", "xe-0/0/1")])
    assert ("rib", "2001:db8:100::/64", "connected") in rib
    # inet.3, private Tabellen und mgmt_junos fallen weg
    assert not any(r.prefix in ("10.255.0.9/32", "10.0.0.1/32") for r in inv.routes)
    assert not any("192.168.99" in str(_hops(r)) for r in inv.routes)
    peers = {p.peer_ip: p for p in inv.peers}
    assert (peers["203.0.113.1"].local_ip, peers["203.0.113.1"].attrs) == (
        "203.0.113.2", {"local_as": 65100, "type": "ebgp"})  # fmt: skip
    assert peers["203.0.113.5"].state == "Active"
    assert peers["10.255.0.1"].attrs["type"] == "ibgp"
    assert peers["10.150.0.254"].context == "mx-edge-01:CUST-A"


def test_arista_mit_eigenen_adressen():
    inv = routing.device("leaf-ar-01", "eos", sr.EOS, {}, set(), [])
    assert [c.context for c in inv.contexts] == ["leaf-ar-01"]  # MGMT faellt weg
    rib = _by(inv, "leaf-ar-01")
    assert _hops(rib[("rib", "10.40.0.0/16", "bgp")]) == [("10.255.0.31", "Eth50/1"),
                                                          ("10.255.0.32", "Eth51/1")]  # fmt: skip
    assert rib[("rib", "0.0.0.0/0", "bgp")].attrs == {"bgp": "ebgp"}
    # Adressen aus `show ip interface`: primaer, sekundaer, virtuell (VARP) als local
    local = {p for (t, p, proto) in rib if proto == "local"}
    assert local == {"10.0.0.0/32", "10.10.0.2/32", "10.11.0.2/32", "10.10.0.1/32"}
    assert rib[("rib", "10.10.0.1/32", "local")].attrs == {"virtual": True}  # VARP
    peers = {p.peer_ip: p for p in inv.peers}
    assert peers["10.255.0.40"].remote_as == 65546 and peers["10.255.0.40"].state == "Connect"
    assert peers["10.255.0.31"].attrs == {"local_as": 65021, "type": "ibgp"}


def test_nicht_lesbar_statt_fehler():
    outputs = {**sr.IOS, routing.IOS_BGP: "% Authorization failed.\n",
               routing.IOS_RIB6: "% IPv6 routing not enabled\n"}  # fmt: skip
    notes: list[str] = []
    errors = {routing.IOS_BGP_SUMMARY: "TimeoutError"}
    inv = routing.device("rt-edge-01", "ios", outputs, errors, set(), notes)
    ctx = {c.context: c for c in inv.contexts}
    # fehlende Rechte bzw. Abbruch: Teil nicht lesbar (nur global), IPv6 aus: ohne Hinweis
    assert ctx["rt-edge-01"].missing == ["BGP-Nachbarn", "BGP-Pfade IPv4"]
    assert ctx["rt-edge-01:CUST-A"].missing == []
    assert notes == ["Routing: show ip bgp summary: TimeoutError",
                     "Routing: show bgp ipv4 unicast: Authorization failed."]  # fmt: skip
    assert not inv.peers and not any(r.table == "bgp" for r in inv.routes)
    # RIB nicht lesbar: Kontext mit `missing`, keine Routen
    denied = routing.device("rt-x", "nxos", {routing.NXOS_RIB: "% Permission denied for the role"},
                            {}, set(), [])  # fmt: skip
    assert [(c.context, c.missing) for c in denied.contexts] == [("rt-x", ["Routing-Tabelle IPv4"])]
    # unlesbare Ausgabe: Hinweis und nicht lesbar
    broken = routing.device("leaf-ar-01", "eos", {**sr.EOS, routing.EOS_RIB: '{"vrfs": ['},
                            {}, set(), notes := [])  # fmt: skip
    assert broken.contexts[0].missing == ["Routing-Tabelle IPv4"]
    assert notes == ["Routing: show ip route vrf all | json: Ausgabe nicht auswertbar "
                     "(JSONDecodeError)"]  # fmt: skip


def test_l2_switch_ohne_routing():
    assert routing.not_routing(sr.IOS_L2_RIB) and routing.not_routing("% Invalid input\n")
    assert routing.not_routing("") and not routing.not_routing(sr.IOS_RIB)
    assert not routing.not_routing("% Authorization failed.\n")  # Rechte: weiter lesen
    inv = routing.device("sw-acc-01", "ios", {routing.IOS_RIB: sr.IOS_L2_RIB,
                                              routing.IOS_BGP: "% BGP not active\n"}, {}, set(),
                         notes := [])  # fmt: skip
    assert inv.contexts == [] and notes == []
    assert routing.device("acc-1300-01", "cbs", {}, {}, set(), []).contexts == []


def test_obergrenze_fullfeed():
    lines = [sr.IOS_RIB.split("B        203.0.113.128/25")[0]]
    for i in range(40):  # 40 oeffentliche und 5 private BGP-Routen
        lines.append(f"B        {11 + i}.0.0.0/8 [20/0] via 198.51.100.1, 1w0d\n")
    for i in range(5):
        lines.append(f"B        10.{100 + i}.0.0/16 [200/0] via 10.255.0.2, 1d02h\n")
    outputs = {routing.IOS_RIB: "".join(lines)}
    notes: list[str] = []
    inv = routing.device("rt-edge-01", "ios", outputs, {}, {routing.IOS_RIB}, notes, limit=25)
    rib = [r for r in inv.routes if r.table == "rib"]
    assert len(rib) == 25
    kept = {r.prefix for r in rib}
    # Default-Route und alle Routen ausser BGP bleiben, private BGP-Routen vor oeffentlichen
    assert "0.0.0.0/0" in kept and "10.0.0.1/32" in kept and "10.20.0.0/16" in kept
    assert {f"10.{100 + i}.0.0/16" for i in range(5)} <= kept
    (ctx,) = inv.contexts
    assert ctx.missing == ["Routing-Tabelle IPv4 (gekuerzt: Ausgabe zu gross)",
                           "Routing-Tabelle IPv4 (gekuerzt: 25 von 58)"]  # fmt: skip
    assert notes[-1] == ("Routing: rt-edge-01: Routing-Tabelle IPv4 gekuerzt - 33 von 58 "
                         "Routen ueber 25 nicht uebernommen")  # fmt: skip


class _Channel:
    """Exec-Kanal ohne Netz: gibt die Ausgabe in Stuecken."""

    def __init__(self, data: bytes):
        self.chunks = [data[i : i + 1000] for i in range(0, len(data), 1000)] + [b""]

    def settimeout(self, _t):
        return None

    def exec_command(self, _cmd):
        return None

    def recv(self, _n):
        return self.chunks.pop(0)

    def recv_stderr_ready(self):
        return False

    def close(self):
        return None


def test_zu_grosse_ausgabe_gekuerzt(monkeypatch):
    monkeypatch.setattr(sc, "MAX_OUTPUT", 5000)
    data = ("B        11.0.0.0/8 [20/0] via 198.51.100.1, 1w0d\n" * 400).encode()

    class Transport:
        def open_session(self, timeout=None):  # noqa: ARG002
            return _Channel(data)

    client = sc.SwitchClient("192.0.2.1", "ro", "pw")
    text = client._exec(Transport(), routing.IOS_RIB, 10)
    assert client.cut == {routing.IOS_RIB} and text.endswith("\n") and len(text) <= 5000
    with pytest.raises(RuntimeError, match="Ausgabe zu gross"):  # JSON: nicht kuerzbar
        client._exec(Transport(), routing.EOS_RIB, 10)


def test_lauf(monkeypatch):
    outputs = {"10.0.0.1": sr.IOS,
               "10.0.0.2": {"show version": sr.IOS_VERSION.replace("rt-edge-01", "sw-acc-01"),
                            routing.IOS_RIB: sr.IOS_L2_RIB}}  # fmt: skip
    sent: dict[str, list[str]] = {}

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
            sent.setdefault(self.entry, []).append(cmd)
            return outputs[self.entry].get(cmd, "")

    monkeypatch.setattr(tasks, "SwitchClient", Fake)
    monkeypatch.setattr(tasks, "submit_result", lambda run_id, result, via_agent=False: None)
    monkeypatch.setattr(tasks, "confirmed", lambda result, _: result)
    target = Target(platform="cisco_switch", name="Router", username="ro", password="pw",
                    read_scope="10.0.0.1\n10.0.0.2")  # fmt: skip
    result = tasks.collect_once("run-rt", target)
    assert result.status == "completed"
    assert result.message == ("Switches: 2 gelesen (IOS/IOS-XE) · Routing: 1 L3-Geraete, "
                              "2 Kontexte, 27 Routen")  # fmt: skip
    assert {c.context for c in result.routing.contexts} == {"rt-edge-01", "rt-edge-01:CUST-A"}
    assert {p.peer_ip for p in result.routing.peers} >= {"198.51.100.1", "198.51.100.9"}
    # Switch ohne Routing: nach dem ersten Routing-Befehl keine weiteren
    routing_sent = [c for c in sent["10.0.0.2"] if c in routing.ALL]
    assert routing_sent == [routing.IOS_RIB]
    assert set(routing.COMMANDS["ios"]) <= set(sent["10.0.0.1"])
    # auch ohne L3-Geraet geht ein (leeres) Routing mit: der Orchestrator ersetzt den Stand
    only_l2 = tasks.collect_once("run-l2", target.model_copy(update={"read_scope": "10.0.0.2"}))
    assert only_l2.routing is not None and only_l2.routing.contexts == []
    assert only_l2.message == "Switches: 1 gelesen (IOS/IOS-XE)"
