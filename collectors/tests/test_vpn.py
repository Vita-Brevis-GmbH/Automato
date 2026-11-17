# ruff: noqa: S104 - 0.0.0.0 sind Selektoren bzw. „nicht gesetzt“, keine Bind-Adressen
"""IPsec-Tunnel der FortiGate lesen (1.19.0, ADR 0063) - nur lesend, ohne Secrets.

Phase 1 im Interface-Modus (Gegenstelle, Interface, Art, IKE-Version, Proposals als Text),
Phase 2 mit Selektoren (Subnetz, Bereich, Adresse, Namen aufgeloest) und der Zustand aus dem
Monitor; Dial-up-Tunnel mit ihren verbundenen Gegenstellen. SSL-VPN wird nicht gelesen.
"""

import json
from importlib import resources

import httpx

from collectors.fortigate import routing as fgt
from collectors.fortigate import vpn
from collectors.fortigate.client import FortiGateClient

PSK = "ENC geheimer-psk-wert"

ADDRESSES = [
    {"name": "N_srv", "type": "ipmask", "subnet": "10.20.0.0 255.255.255.0"},
    {"name": "H_app", "type": "ipmask", "subnet": "10.20.1.5 255.255.255.255"},
    {"name": "H-EXT_partner", "type": "fqdn", "fqdn": "vpn.partner.example.ch"},
]
GROUPS = [{"name": "G_lokal", "member": [{"name": "N_srv"}, {"name": "H_app"}]}]

PHASE1 = [
    {"name": "vpn-b", "type": "static", "interface": "wan1", "ike-version": "2",
     "remote-gw": "203.0.113.2", "local-gw": "0.0.0.0", "proposal": "aes256gcm-prfsha384",
     "dhgrp": "20", "psksecret": PSK, "psksecret-remote": PSK, "ppk-secret": PSK,
     "comments": "Standort B"},
    {"name": "vpn-partner", "type": "ddns", "interface": "wan1", "ike-version": "1",
     "remotegw-ddns": "vpn.partner.example.ch", "proposal": "aes256-sha256", "psksecret": PSK},
    {"name": "dialup", "type": "dynamic", "interface": "wan1", "ike-version": "2",
     "add-route": "enable", "psksecret": PSK},
    {"name": "vpn-v6", "type": "static", "interface": "wan1", "ip-version": "6",
     "remote-gw6": "2001:db8:ff::2", "psksecret": PSK},
]  # fmt: skip
PHASE2 = [
    {"name": "vpn-b-p2", "phase1name": "vpn-b", "proposal": "aes256gcm",
     "src-addr-type": "name", "src-name": "G_lokal",
     "dst-addr-type": "subnet", "dst-subnet": "10.60.0.0 255.255.0.0"},
    {"name": "vpn-b-p2b", "phase1name": "vpn-b",
     "src-addr-type": "range", "src-start-ip": "10.20.2.0", "src-end-ip": "10.20.2.255",
     "dst-addr-type": "ip", "dst-start-ip": "10.61.0.9"},
    {"name": "vpn-partner-p2", "phase1name": "vpn-partner",
     "src-addr-type": "subnet", "src-subnet": "10.20.0.0 255.255.255.0",
     "dst-addr-type": "name", "dst-name": "H-EXT_partner"},
    {"name": "dialup-p2", "phase1name": "dialup",
     "src-addr-type": "subnet", "src-subnet": "0.0.0.0 0.0.0.0",
     "dst-addr-type": "subnet", "dst-subnet": "0.0.0.0 0.0.0.0"},
    {"name": "vpn-v6-p2", "phase1name": "vpn-v6",
     "src-addr-type": "subnet6", "src-subnet6": "2001:db8:20::/48",
     "dst-addr-type": "range6", "dst-start-ip6": "2001:db8:60::", "dst-end-ip6": "2001:db8:60::ff"},
]  # fmt: skip
MONITOR = [
    {"name": "vpn-b", "rgwy": "203.0.113.2", "proxyid": [
        {"p2name": "vpn-b-p2", "status": "up"}, {"p2name": "vpn-b-p2b", "status": "down"}]},
    {"name": "vpn-partner", "rgwy": "198.51.100.77", "proxyid": [
        {"p2name": "vpn-partner-p2", "status": "down"}]},
    {"name": "dialup_0", "parent": "dialup", "rgwy": "192.0.2.150", "proxyid": [
        {"p2name": "dialup-p2", "status": "up",
         "proxy_dst": [{"subnet": "10.77.0.0/255.255.255.0"}]}]},
]  # fmt: skip


def _client(denied: frozenset[str] = frozenset(), seen: list | None = None) -> FortiGateClient:
    cmdb = {
        "vpn.ipsec/phase1-interface": PHASE1,
        "vpn.ipsec/phase2-interface": PHASE2,
        "firewall/address": ADDRESSES,
        "firewall/addrgrp": GROUPS,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"  # nur lesend
        path = request.url.path
        if seen is not None:
            seen.append(path)
        key = path.split("/api/v2/", 1)[1].split("/", 1)[1]
        if key in denied:
            return httpx.Response(403, json={})
        if key == "system/global":
            return httpx.Response(200, json={"results": {"hostname": "fw-a"}})
        if key == "system/vdom":
            return httpx.Response(200, json={"results": [{"name": "root"}]})
        if path.startswith("/api/v2/monitor/"):
            if key == "vpn/ipsec":
                return httpx.Response(200, json={"results": MONITOR})
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"results": cmdb.get(key, [])})

    return FortiGateClient(
        "https://fw", "t", client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def _tunnels(denied: frozenset[str] = frozenset()) -> dict:
    d = _client(denied).gather()
    inv = fgt.normalize(d["vdoms"], d["ha"], "fw-a")
    return {t.name: t for t in inv.vpn}, inv


def test_phase1_phase2_und_zustand_ohne_secrets():
    seen: list = []
    _client(seen=seen).gather()
    assert "/api/v2/cmdb/vpn.ipsec/phase1-interface" in seen
    assert "/api/v2/monitor/vpn/ipsec" in seen
    assert not any("ssl" in p for p in seen)  # SSL-VPN wird nicht gelesen

    tunnels, inv = _tunnels()
    assert inv.contexts[0].missing == []
    b = tunnels["vpn-b"]
    assert (b.node, b.context, b.kind, b.interface, b.remote_gw, b.local_gw, b.ike_version) == (
        "fw-a", "root", "static", "wan1", "203.0.113.2", None, 2)  # fmt: skip
    assert b.status == "partial"  # eine Phase 2 up, eine down
    assert b.attrs == {"proposal": "aes256gcm-prfsha384", "dhgrp": "20", "comments": "Standort B"}
    p2 = {p.name: p for p in b.phase2}
    # Gruppe aufgeloest: Netz und Host; Name bleibt stehen
    assert (p2["vpn-b-p2"].src, p2["vpn-b-p2"].src_names) == (
        ["10.20.0.0/24", "10.20.1.5/32"], ["G_lokal"])  # fmt: skip
    assert p2["vpn-b-p2"].dst == ["10.60.0.0/16"] and p2["vpn-b-p2"].status == "up"
    # Bereich, der ein Netz ist, als Netz; einzelne Adresse als /32
    assert (p2["vpn-b-p2b"].src, p2["vpn-b-p2b"].dst) == (["10.20.2.0/24"], ["10.61.0.9/32"])
    assert p2["vpn-b-p2b"].status == "down"
    assert p2["vpn-b-p2"].attrs == {"proposal": "aes256gcm"}

    partner = tunnels["vpn-partner"]  # DDNS: Adresse aus dem Monitor, Name in den Attributen
    assert (partner.kind, partner.remote_gw, partner.status) == ("ddns", "198.51.100.77", "down")
    assert partner.attrs["remotegw-ddns"] == "vpn.partner.example.ch"
    (pp2,) = partner.phase2
    assert (pp2.dst, pp2.dst_names, pp2.unresolved) == (
        [], ["H-EXT_partner"], ["fqdn:vpn.partner.example.ch"])  # fmt: skip

    v6 = tunnels["vpn-v6"]
    assert v6.remote_gw == "2001:db8:ff::2" and v6.status == "down"  # nicht im Monitor
    assert (v6.phase2[0].src, v6.phase2[0].dst) == (["2001:db8:20::/48"], ["2001:db8:60::/120"])

    dump = json.dumps(inv.model_dump())
    assert PSK not in dump and "psksecret" not in dump and "ppk" not in dump


def test_dialup_gekennzeichnet_mit_verbundenen_gegenstellen():
    tunnels, _inv = _tunnels()
    d = tunnels["dialup"]
    assert (d.kind, d.remote_gw, d.status) == ("dialup", None, "up")
    assert d.attrs == {"add-route": "enable"}
    assert d.instances == [{"name": "dialup_0", "remote_gw": "192.0.2.150", "status": "up",
                            "phase2": {"dialup-p2": "up"}, "nets": ["10.77.0.0/24"]}]  # fmt: skip
    assert (d.phase2[0].src, d.phase2[0].dst, d.phase2[0].status) == (
        ["0.0.0.0/0"], ["0.0.0.0/0"], "up")  # fmt: skip
    # ohne verbundene Gegenstelle: „idle“, nicht „down“
    _states, dialups = vpn._monitor([m for m in MONITOR if not m.get("parent")])
    assert dialups == {}


def test_ohne_recht_auf_den_monitor_zustand_unbekannt_und_in_der_meldung():
    import collectors.fortigate.tasks as t

    tunnels, inv = _tunnels(frozenset({"vpn/ipsec"}))
    assert inv.contexts[0].missing == ["IPsec-Zustand (HTTP 403)"]
    assert {n: x.status for n, x in tunnels.items()} == {
        "vpn-b": None, "vpn-partner": None, "dialup": None, "vpn-v6": None}  # fmt: skip
    assert tunnels["vpn-partner"].remote_gw == "vpn.partner.example.ch"  # Name ohne Monitor
    d = _client(frozenset({"vpn.ipsec/phase1-interface"})).gather()
    assert t._vdom_message(d) == "Routing nicht lesbar: IPsec Phase 1 (HTTP 403)"
    assert fgt.normalize(d["vdoms"], d["ha"], "fw-a").vpn == []


def test_beispieldaten_im_labor(monkeypatch):
    """Zentraler Worker im Labor (AUTOMATO_ENVIRONMENT=dev, ohne Zugangsdaten): Beispiel-Tunnel
    im Ergebnis - statisch, zur fremden Gegenstelle und Dial-up."""
    import collectors.fortigate.tasks as t

    sample = json.loads(
        resources.files("collectors.fortigate").joinpath("sample_vpn.json").read_text()
    )
    assert {p["name"] for p in sample["phase1"]} == {"vpn-kunde-b", "vpn-partner", "dialup-aussen"}
    monkeypatch.setattr(t, "samples_allowed", lambda: True)
    sent = {}
    monkeypatch.setattr(
        t, "submit_result", lambda run_id, result, via_agent=False: sent.update(result=result)
    )
    t.collect_once("r1")
    result = sent["result"]
    assert result.status == "completed", result.message
    tunnels = {x.name: x for x in result.routing.vpn}
    assert {n: (x.kind, x.status) for n, x in tunnels.items()} == {
        "vpn-kunde-b": ("static", "up"),
        "vpn-partner": ("static", "down"),
        "dialup-aussen": ("dialup", "up"),
    }
    # Selektor ueber eine Gruppe der Beispiel-Objekte
    assert tunnels["vpn-partner"].phase2[0].src == ["10.10.10.5/32", "10.10.10.0/24"]
    assert [c.context for c in result.routing.contexts] == ["root"]
    assert "beispiel-nicht-uebernommen" not in json.dumps(result.model_dump())
