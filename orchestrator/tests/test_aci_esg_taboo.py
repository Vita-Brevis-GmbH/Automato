"""ACI: ESG mit Selektoren, Taboo-Contract (deny) und Preferred Group im Store/API."""

from test_reference_sources import TOKEN, _system, _tenant

WEB = "uni/tn-TE_a/ap-AP_1/epg-EG_web"
ESG = "uni/tn-TE_a/ap-AP_1/esg-ES_pci"
ANY = "uni/tn-TE_a/ctx-VRF_a/any"

RESULT = {
    "status": "completed",
    "epgs": [
        {"name": "EG_web", "dn": WEB, "tenant": "TE_a", "ap": "AP_1", "pref_group": True},
        {
            "name": "ES_pci",
            "kind": "esg",
            "dn": ESG,
            "tenant": "TE_a",
            "ap": "AP_1",
            "vrf": "VRF_a",
            "subnets": ["10.9.0.0/24"],
            "selectors": ["Tag zone=pci", "IP ip=='10.9.0.0/24'"],
            "pref_group": True,
        },
        {"name": "vzAny", "kind": "vzany", "dn": ANY, "tenant": "TE_a", "vrf": "VRF_a"},
    ],
    "rules": [
        {
            "source_platform": "cisco_aci",
            "external_id": "uni/tn-TE_a/taboo-TB_noadmin/TS_rdp",
            "name": "TB_noadmin:TS_rdp",
            "action": "deny",
            "sources": ["any"],
            "destinations": [f"epg:{WEB}"],
            "services": ["tcp/3389"],
            "raw": {"kind": "taboo", "dn": "uni/tn-TE_a/taboo-TB_noadmin", "name": "TB_noadmin"},
        },
        {
            "source_platform": "cisco_aci",
            "external_id": f"{ANY}/prefgr",
            "name": "Preferred Group VRF_a",
            "action": "allow",
            "sources": [f"epg:{WEB}", f"epg:{ESG}"],
            "destinations": [f"epg:{WEB}", f"epg:{ESG}"],
            "services": ["ip/any"],
            "aci_scope": "context",
            "raw": {"kind": "preferred_group", "dn": ANY, "name": "Preferred Group VRF_a"},
        },
    ],
}


def test_esg_taboo_preferred_group(client):
    tid = _tenant(client)
    apic = _system(client, tid, name="apic", platform="cisco_aci", base_url="https://apic")
    body = {"platform": "cisco_aci", "tenant_id": tid, "source_system_id": apic["id"]}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=RESULT)
    assert r.json()["status"] == "completed", r.text

    rows = {
        x["aci"]["kind"]: x
        for x in client.get("/rules", params={"tenant_id": tid, "domain": "aci_contract"}).json()
    }
    taboo = rows["taboo"]
    assert taboo["action"] == "deny" and taboo["destinations"] == ["AP_1/EG_web"]
    assert taboo["aci"]["contract"] == "TB_noadmin" and taboo["aci"]["subject"] == "TS_rdp"
    pref = rows["preferred_group"]
    assert pref["aci"]["tenant"] == "TE_a" and sorted(pref["sources"]) == [
        "AP_1/EG_web",
        "AP_1/ES_pci",
    ]

    detail = client.get(f"/rules/{pref['id']}").json()
    esg = next(e["epg"] for e in detail["endpoints"] if e["epg"]["kind"] == "esg")
    assert esg["selectors"] == ["Tag zone=pci", "IP ip=='10.9.0.0/24'"] and esg["pref_group"]
    assert esg["subnets"][0]["origin"] == "esg" and esg["vrf"] == "VRF_a"
    # ESGs sind keine Systeme der Applikations-Doku (gruppieren nur EPGs/Endpunkte)
    systems = client.get("/appdoc/systems", params={"tenant_id": tid}).json()
    assert [s["name"] for s in systems] == ["EG_web"]
