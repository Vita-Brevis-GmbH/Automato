"""Konfiguration vor Gelerntem (0.46.0, R12, Anwender): zu welcher EPG gehoert eine IP?

Zwei EPGs im selben BD (10.1.0.0/24) der VRF TE:VRF1. vm-a haengt an der Portgruppe der
EG_a, das ACI hat ihre IP 10.1.0.5 aber in EG_b gelernt; 10.1.0.7 ist ein statischer Endpunkt
der EG_a und ebenfalls in EG_b gelernt; 10.1.0.9 kennt nur das Fabric (EG_b). Weg-Suche und
Regel-Liste folgen der Konfiguration, der Widerspruch ist ein Befund.
"""

import ipaddress

from app.store.aci_check import AciChecker
from app.store.epg_locate import Locator
from test_vmware import ADMIN, _deliver, _system

A = "uni/tn-TE/ap-AP/epg-EG_a"
B = "uni/tn-TE/ap-AP/epg-EG_b"


def _epg(dn, endpoints):
    name = dn.rsplit("epg-", 1)[1]
    return {"name": name, "dn": dn, "tenant": "TE", "ap": "AP", "bd": "BD_shared", "vrf": "VRF1",
            "vrf_tenant": "TE", "bd_subnets": ["10.1.0.1/24"], "endpoints": endpoints}  # fmt: skip


ACI = {
    "status": "completed",
    "epgs": [
        _epg(A, [{"ip": "10.1.0.7", "origin": "static"}]),
        _epg(
            B,
            [
                {"ip": "10.1.0.5", "origin": "learned"},
                {"ip": "10.1.0.7", "origin": "learned"},
                {"ip": "10.1.0.9", "origin": "learned"},
            ],
        ),
    ],  # fmt: skip
    "rules": [
        {
            "source_platform": "cisco_aci",
            "external_id": "uni/tn-TE/brc-CT_b/SJ_1",
            "name": "CT_b",
            "action": "allow",
            "sources": [f"epg:{B}"],
            "destinations": [f"epg:{B}"],
            "services": ["tcp/22"],
            "aci_scope": "context",
            "raw": {"name": "CT_b", "kind": "contract", "scope": "context"},
        },
    ],  # fmt: skip
}
VCENTER = {
    "status": "completed",
    "virtual": {
        "vms": [{"name": "vm-a", "power_state": "poweredOn",
                 "nics": [{"name": "Network adapter 1", "portgroup": "TE|AP|EG_a",
                           "switch": "DVS-A", "vlan": 1100, "ips": ["10.1.0.5"]}]}],
        "portgroups": [{"name": "TE|AP|EG_a", "kind": "dvs", "switch": "DVS-A", "vlan": 1100}],
    },
}  # fmt: skip


def _seed(client) -> tuple[int, int]:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    vc = _system(client, tid, "vc-01", "vmware", base_url="https://vc", username="ro", password="x")
    _deliver(client, tid, vc, "vmware", VCENTER)
    apic = _system(client, tid, "apic-01", "cisco_aci", base_url="https://apic")
    _deliver(client, tid, apic, "cisco_aci", ACI)
    return tid, apic


def test_konfiguration_vor_gelerntem(client, session_factory):
    tid, apic = _seed(client)
    with session_factory() as s:
        loc = Locator(s, tid)
        epgs, how = loc.locate("10.1.0.5", apic, "TE:VRF1")
        assert [e.name for e in epgs] == ["EG_a"] and how == "Portgruppe der VM vm-a"
        epgs, how = loc.locate("10.1.0.7", apic, "TE:VRF1")
        assert [e.name for e in epgs] == ["EG_a"] and how == "statischer Endpunkt"
        epgs, how = loc.locate("10.1.0.9", apic, "TE:VRF1")
        assert [e.name for e in epgs] == ["EG_b"] and how == "gelernter Endpunkt"
        epgs, how = loc.locate("10.1.0.20", apic, "TE:VRF1")  # nur das BD-Subnetz: mehrdeutig
        assert {e.name for e in epgs} == {"EG_a", "EG_b"} and how == "Subnetz"
        # Weg-Suche: die EPG der Konfiguration, mit der Herkunft
        members, text = AciChecker(s, tid).members(
            apic, "TE:VRF1", ipaddress.ip_address("10.1.0.5")
        )
        assert [m.epg.name for m in members] == ["EG_a"] and members[0].sure
        assert text == "EPG TE/AP/EG_a (Portgruppe der VM vm-a)"
        members, _text = AciChecker(s, tid).members(
            apic, "TE:VRF1", ipaddress.ip_address("10.1.0.9")
        )
        assert [m.epg.name for m in members] == ["EG_b"]


def test_regel_liste_folgt_der_konfiguration(client):
    """CT_b gilt fuer EG_b; ueber das gemeinsame BD-Subnetz erfasst er auch die IP von vm-a -
    die gehoert nach der Portgruppe aber zu EG_a: fuer sie nicht moeglich."""
    tid, _apic = _seed(client)
    params = {"tenant_id": tid, "q": "10.1.0.5", "domain": "aci_contract"}
    rel = {x["name"]: x["relevance"] for x in client.get("/rules", params=params).json()}
    assert rel["CT_b"]["state"] == "impossible"
    assert rel["CT_b"]["text"] == "10.1.0.5 gehört nach Portgruppe der VM vm-a zur EPG TE/AP/EG_a"
    params["q"] = "10.1.0.9"  # nur gelernt in EG_b: der Contract gilt
    rel = {x["name"]: x["relevance"] for x in client.get("/rules", params=params).json()}
    assert (rel["CT_b"] or {}).get("state") != "impossible"


def test_befund_gelernt_anders_als_konfiguriert(client):
    tid, _apic = _seed(client)
    params = {"tenant_id": tid, "type": "epg_assignment_conflict"}
    found = {
        (f["subject_kind"], f["subject_id"]): f
        for f in client.get("/findings", params=params).json()
    }
    assert set(found) == {("vm", "vm-a"), ("epg", "TE/AP/EG_a")}
    vm = found[("vm", "vm-a")]
    assert vm["detail"]["ips"] == ["10.1.0.5"] and vm["detail"]["learned"] == ["TE/AP/EG_b"]
    assert vm["message"] == (
        "VM „vm-a“: 10.1.0.5 gehört laut Portgruppe der VM vm-a zur EPG TE/AP/EG_a, das ACI hat "
        "sie in TE/AP/EG_b gelernt (VRF TE:VRF1)."
    )
    static = found[("epg", "TE/AP/EG_a")]
    assert static["detail"]["via"] == "statischer Endpunkt" and static["detail"]["ips"] == [
        "10.1.0.7"
    ]
