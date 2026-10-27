"""Externe Adressen ohne IPAM-Befund (0.76.1): öffentliche Hosts/Netze ausserhalb gemanagter Netze
erwartet niemand im IPAM; im Interface-Netz am WAN-Anschluss der Firewall bleibt es ein Befund."""

from app.store.external import Managed
from test_reconcile_references import DDI, _findings, _refs, _result, _system, _tenant

# öffentliches Interface-Netz am WAN der Firewall (gemanagt) - Testdaten
WAN = "45.10.10.0/29"
FW = {
    "rules": [
        {
            "source_platform": "fortigate",
            "external_id": "7",
            "action": "allow",
            "sources": ["10.20.0.5", "10.40.0.5"],
            "destinations": ["8.8.8.8", "9.9.9.0/24", "45.10.10.5"],
            "services": ["tcp/443"],
        }
    ],
    "inventory": {
        "subnets": [
            {"prefix": WAN, "gateway": "45.10.10.1/29", "context": "interface",
             "context_name": "wan1"},
        ]
    },
}  # fmt: skip


def test_externe_adressen_ohne_befund(client):
    tid = _tenant(client)
    fw = _system(client, tid, name="fw", platform="fortigate")
    ddi = _system(client, tid, name="ddi", platform="infoblox", username="u", password="p")
    _refs(client, tid, ddi, "infoblox", DDI)
    _result(client, tid, fw, "fortigate", FW)

    f = _findings(client, tid)
    # privat ohne IPAM und öffentlich im WAN-Netz: Befund; öffentlich ausserhalb: keiner
    assert f["unmatched_object"] == {"10.40.0.5", "45.10.10.5"}
    assert f["subnet_not_in_ipam"] == {WAN}  # das WAN-Netz selbst gehört ins IPAM
    assert _status(client, tid) == {
        "10.20.0.5": "matched", "10.40.0.5": "unmatched", "8.8.8.8": "expected_external",
        "9.9.9.0/24": "expected_external", "45.10.10.5": "unmatched",
    }  # fmt: skip

    # kommt die Adresse ins IPAM, gilt sie wieder als gefunden
    _refs(client, tid, ddi, "infoblox", [*DDI, {"kind": "host", "value": "8.8.8.8",
                                                "dns_name": "dns.example.ch"}])  # fmt: skip
    status = _status(client, tid)
    assert status["8.8.8.8"] == "matched" and status["9.9.9.0/24"] == "expected_external"


def _status(client, tid) -> dict[str, str]:
    rule = client.get(f"/rules?tenant_id={tid}").json()[0]
    detail = client.get(f"/rules/{rule['id']}?tenant_id={tid}").json()
    return {e["value"]: e["match_status"] for e in detail["endpoints"] if e.get("value")}


def test_managed_bereiche():
    nets = Managed(["45.10.10.0/29", "45.10.10.8/29", "10.0.0.0/8", "2001:db8:1::/64", "bogus"])
    assert nets.external("8.8.8.8") and nets.external("9.9.9.0/24")
    assert not nets.external("45.10.10.12")  # im zweiten, zusammengefassten Netz
    assert not nets.external("45.10.10.0/28")  # überlappt gemanagte Netze
    assert not nets.external("10.1.2.3") and not nets.external("192.168.1.0/24")  # privat
    assert not nets.external("100.64.1.1")  # CGNAT, nicht öffentlich
    assert nets.external("8.8.8.1-8.8.8.9") and not nets.external("45.10.10.3-45.10.10.20")
    assert nets.external("2a00:1450::1") and not nets.external("fd00::1")
    assert not nets.external(None) and not nets.external("H_web01")
