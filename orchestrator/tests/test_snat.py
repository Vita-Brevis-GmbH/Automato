"""SNAT der FortiGate genauer (0.47.0): Pool-Typen, IPv6-Pools, Interface-IP je Familie,
`nat-source-vip` und der Befund fuer einen fehlenden Pool.

Eine Firewall fw-n mit LAN 10.10.0.0/24 (und 2001:db8:1::/64) und WAN 203.0.113.0/24 (und
2001:db8:ff::/64). Pools: IPP_fpr (fixed-port-range, 2 externe IPs fuer 10.10.0.0/24), IPP_one
(overload, eine IP), IPP_121 (one-to-one, drei IPs), IPP6_x (IPv6). Die VIP VIP_srv mit
nat-source-vip fuer den Server 10.10.0.50.
"""

import ipaddress

from app.models import Rule
from app.store import rule_view
from app.store.policy_check import Checker
from test_policy_check import ADMIN, TOKEN, _fw, _iface, _policy, _route


def _pool(name, ext, kind, source=None, family="v4"):
    attrs = {"type": kind} | ({"source": source} if source else {})
    return {"source_platform": "fortigate", "external_id": name, "name": name,
            "nat_type": "snat_pool", "ext_ip": ext, "family": family, "attrs": attrs,
            "vdom": "root", "raw": {"type": kind}}  # fmt: skip


FW = _fw(
    "fw-n",
    [_policy(1, "lan-out-fpr", "lan", "wan", ["10.10.0.0/24"], ["all"], ["ALL"],
             nat="enable", ippool="enable", poolname=[{"name": "IPP_fpr"}]),
     _policy(2, "lan-out-gone", "lan", "wan", ["10.10.1.0/24"], ["all"], ["ALL"],
             nat="enable", ippool="enable", poolname=[{"name": "IPP_gone"}]),
     _policy(3, "lan-out-v6", "lan", "wan", ["all"], ["all"], ["ALL"],
             nat="enable", nat6="enable", ippool6="enable", poolname6=[{"name": "IPP6_x"}])],
    [_iface("lan", "10.10.0.1/24", "lan") | {"ips": ["10.10.0.1/24", "2001:db8:1::1/64"]},
     _iface("wan", "203.0.113.1/24", "wan") | {"ips": ["203.0.113.1/24", "2001:db8:ff::1/64"]}],
    [_route("10.10.0.0/24", [{"interface": "lan"}], "connected"),
     _route("203.0.113.0/24", [{"interface": "wan"}], "connected")],
    nat=[_pool("IPP_fpr", "198.51.100.10-198.51.100.11", "fixed-port-range",
               "10.10.0.0-10.10.0.255"),
         _pool("IPP_one", "198.51.100.30", "overload"),
         _pool("IPP_121", "198.51.100.20-198.51.100.22", "one-to-one"),
         _pool("IPP6_x", "2001:db8:ff::100-2001:db8:ff::1ff", "overload", family="v6"),
         {"source_platform": "fortigate", "external_id": "VIP_srv", "name": "VIP_srv",
          "ext_ip": "198.51.100.50", "mapped_ip": "10.10.0.50", "vdom": "root",
          "raw": {"extintf": "any", "nat-source-vip": "enable"}}],
)  # fmt: skip


def _seed(client) -> tuple[int, int]:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    body = {"tenant_id": tid, "name": "fw-n", "platform": "fortigate", "base_url": "https://x"}
    sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
    run = {"platform": "fortigate", "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=run).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=FW)
    assert r.json()["status"] == "completed", r.text
    return tid, sid


def test_pool_typen_familie_und_vip(client, session_factory):
    tid, sid = _seed(client)
    ip = ipaddress.ip_address
    wan = {"interface": "wan"}
    with session_factory() as s:
        c = Checker(s, tid)
        # fixed-port-range: 256 interne auf 2 externe IPs - je 128 eine feste
        low = c.snat(sid, "root", ["IPP_fpr"], wan, ip("10.10.0.5"))
        assert (low["to"], low["sure"], low["type"]) == ("198.51.100.10", True, "fixed-port-range")
        assert c.snat(sid, "root", ["IPP_fpr"], wan, ip("10.10.0.200"))["to"] == "198.51.100.11"
        # overload mit einer IP: genau diese
        one = c.snat(sid, "root", ["IPP_one"], wan, ip("10.10.0.5"))
        assert (one["to"], one["sure"]) == ("198.51.100.30", True)
        # one-to-one ueber drei IPs: kein Netz - erste IP, nicht sicher, mit Bereich
        many = c.snat(sid, "root", ["IPP_121"], wan, ip("10.10.0.5"))
        assert (many["to"], many["sure"], many["range"]) == (
            "198.51.100.20",
            False,
            "198.51.100.20-198.51.100.22",
        )
        # IPv6: der Pool der Familie, sonst die IPv6-Adresse des Ausgangs
        v6 = c.snat(sid, "root", ["IPP_one", "IPP6_x"], wan, ip("2001:db8:1::5"))
        assert (v6["pool"], v6["to"]) == ("IPP6_x", "2001:db8:ff::100/120")
        assert c.snat(sid, "root", [], wan, ip("2001:db8:1::5"))["to"] == "2001:db8:ff::1"
        assert c.snat(sid, "root", [], wan, ip("10.10.0.5"))["to"] == "203.0.113.1"
        # nat-source-vip: der Server geht mit der externen IP der VIP hinaus
        vip = c._vip_source(sid, "root", ip("10.10.0.50"), wan)
        assert vip == {"vip": "VIP_srv", "to": "198.51.100.50", "sure": True}
        # Pool, den die Firewall nicht hat
        gone = c.snat(sid, "root", ["IPP_gone"], wan, ip("10.10.1.5"))
        assert gone == {"pool": "IPP_gone", "to": None, "sure": False, "missing": True}
        # Pools je Familie aus der Policy (FortiOS 7.x: nat6/poolname6)
        rules = {r.name: r for r in s.query(Rule).filter(Rule.tenant_id == tid)}
        assert rule_view.snat_pools(rules["lan-out-v6"], "v6") == ["IPP6_x"]
        assert rule_view.snat_pools(rules["lan-out-v6"], "v4") == []  # IPv4: Interface-IP


def test_weg_mit_snat_und_befund(client):
    tid, _sid = _seed(client)
    trace = client.get("/routing/trace", params={"tenant_id": tid, "src": "10.10.0.5",
                                                  "dst": "203.0.113.99"}).json()  # fmt: skip
    (hop,) = trace["paths"][0]["hops"]
    assert hop["snat"]["to"] == "198.51.100.10" and hop["snat"]["pool"] == "IPP_fpr"
    # nat-source-vip vor dem Pool der Policy
    trace = client.get("/routing/trace", params={"tenant_id": tid, "src": "10.10.0.50",
                                                  "dst": "203.0.113.99"}).json()  # fmt: skip
    assert trace["paths"][0]["hops"][0]["snat"]["vip"] == "VIP_srv"
    found = client.get("/findings", params={"tenant_id": tid, "type": "snat_pool_missing"}).json()
    (f,) = found
    assert f["detail"]["pools"] == ["IPP_gone"] and f["detail"]["external_id"] == "2"
    assert "IP-Pool IPP_gone" in f["message"]
