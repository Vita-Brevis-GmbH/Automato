"""ACI-Contracts: wo liegen Consumer (Quelle) und Provider (Ziel)? Tenant, AP, BD, VRF,
Subnetze mit IPAM-Netz, gelernte IPs mit DNS-Namen aus der DDI (Dev-Befund)."""

from test_reference_sources import REC, TOKEN, _ddi, _run, _system, _tenant

WEB = "uni/tn-TE_a/ap-AP_100/epg-EG_web"
APP = "uni/tn-common/ap-AP_infra/epg-EG_app"
EXT = "uni/tn-TE_a/out-L3O_inet/instP-EN_inet"

ACI_RESULT = {
    "status": "completed",
    "epgs": [
        {
            "name": "EG_web",
            "dn": WEB,
            "tenant": "TE_a",
            "ap": "AP_100",
            "bd": "BD_web",
            "vrf": "VRF_a",
            "bd_subnets": ["10.20.0.1/24"],
            "endpoints": [{"ip": "10.20.0.5", "mac": "aa"}, {"ip": "10.20.0.6", "mac": "bb"}],
        },
        {"name": "EG_app", "dn": APP, "tenant": "common", "ap": "AP_infra"},
        {
            "name": "EN_inet",
            "kind": "external",
            "dn": EXT,
            "tenant": "TE_a",
            "l3out": "L3O_inet",
            "vrf": "VRF_a",
            "subnets": ["0.0.0.0/0"],
        },
    ],
    "rules": [
        {
            "source_platform": "cisco_aci",
            "external_id": "uni/tn-TE_a/brc-CT_web/SJ_https",
            "name": "CT_web:SJ_https",
            "action": "allow",
            "sources": [f"epg:{EXT}", f"epg:{APP}"],
            "destinations": [f"epg:{WEB}"],
            "services": ["tcp/443"],
            "aci_scope": "global",
            "raw": {"dn": "uni/tn-TE_a/brc-CT_web", "name": "CT_web", "scope": "global"},
        }
    ],
}


def test_consumer_und_provider_mit_lage_ipam_und_dns(client):
    tid = _tenant(client)
    ddi = _ddi(client, tid)
    _run(client, tid, ddi["id"], "infoblox", REC)  # 10.20.0.0/24 (VLAN 110, RZ1), web01
    apic = _system(client, tid, name="apic", platform="cisco_aci", base_url="https://apic")
    body = {"platform": "cisco_aci", "tenant_id": tid, "source_system_id": apic["id"]}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=ACI_RESULT)
    assert r.json()["status"] == "completed", r.text

    (row,) = client.get("/rules", params={"tenant_id": tid, "domain": "aci_contract"}).json()
    # Liste: AP/EPG, L3Out/EPG; ACI-Tenant davor, wenn anders als der des Contracts
    assert row["sources"] == ["L3O_inet/EN_inet", "common/AP_infra/EG_app"]
    assert row["destinations"] == ["AP_100/EG_web"]

    detail = client.get(f"/rules/{row['id']}").json()
    by_role = {(e["role"], e["epg"]["name"]): e["epg"] for e in detail["endpoints"]}
    web = by_role[("dst", "EG_web")]
    assert (web["aci_tenant"], web["ap"], web["bd"], web["vrf"]) == (
        "TE_a",
        "AP_100",
        "BD_web",
        "VRF_a",
    )
    (subnet,) = web["subnets"]
    assert subnet["origin"] == "bd" and subnet["ip"] == "10.20.0.1/24"
    assert subnet["ipam"]["vlan"] == 110 and subnet["ipam"]["site"] == "RZ1"
    assert web["endpoint_count"] == 2
    assert web["endpoints"][0] == {"ip": "10.20.0.5", "mac": "aa", "dns_name": "web01.example.ch"}
    assert web["endpoints"][1]["dns_name"] is None  # nicht in der DDI

    ext = by_role[("src", "EN_inet")]
    assert ext["kind"] == "external" and ext["l3out"] == "L3O_inet"
    assert ext["subnets"] == [{"ip": "0.0.0.0/0", "origin": "ext", "ipam": None}]
    assert by_role[("src", "EG_app")]["aci_tenant"] == "common"


def test_app_doku_gleichnamige_epgs_mehrerer_aci_tenants(client):
    tid = _tenant(client)
    apic = _system(client, tid, name="apic", platform="cisco_aci", base_url="https://apic")
    a, b = "uni/tn-TE_a/ap-AP_100/epg-EG_app", "uni/tn-TE_b/ap-AP_200/epg-EG_app"
    result = {
        "status": "completed",
        "epgs": [
            {"name": "EG_app", "dn": a, "tenant": "TE_a", "ap": "AP_100"},
            {"name": "EG_app", "dn": b, "tenant": "TE_b", "ap": "AP_200"},
        ],
        "rules": [
            {
                "source_platform": "cisco_aci",
                "external_id": "uni/tn-TE_b/brc-CT_glob/SJ_1",
                "name": "CT_glob:SJ_1",
                "action": "allow",
                "sources": [f"epg:{a}"],
                "destinations": [f"epg:{b}"],
                "services": ["tcp/443"],
                "aci_scope": "tenant",
            }
        ],
    }
    body = {"platform": "cisco_aci", "tenant_id": tid, "source_system_id": apic["id"]}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)

    names = {r["name"] for r in client.get(f"/appdoc/systems?tenant_id={tid}").json()}
    assert names == {"EG_app · TE_a · AP_100", "EG_app · TE_b · AP_200"}
    doc = client.get("/appdoc/systems/EG_app · TE_a · AP_100", params={"tenant_id": tid}).json()
    rows = {t["index"]: t["rows"] for t in doc["tables"]}
    # Gegenstelle in einem anderen ACI-Tenant -> Tabelle 9, mit Tenant gekennzeichnet
    assert [r["Target Systems"] for r in rows[9]] == ["TE_b/EG_app"]


def test_app_doku_externe_gegenstelle_nach_l3out(client):
    """EPG mit Consumer hinter L3O_inet -> „Zugriff aus dem Internet“ (2), nicht 7/8/9."""
    tid = _tenant(client)
    apic = _system(client, tid, name="apic", platform="cisco_aci", base_url="https://apic")
    result = {
        "status": "completed",
        "epgs": [e for e in ACI_RESULT["epgs"] if e["name"] != "EG_app"],
        "rules": [{**ACI_RESULT["rules"][0], "sources": [f"epg:{EXT}"]}],
    }
    body = {"platform": "cisco_aci", "tenant_id": tid, "source_system_id": apic["id"]}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)

    doc = client.get("/appdoc/systems/EG_web", params={"tenant_id": tid}).json()
    rows = {t["index"]: t["rows"] for t in doc["tables"]}
    assert [r["Target Systems"] for r in rows[2]] == ["L3O_inet/EN_inet"]
    assert not any(rows[i] for i in (7, 8, 9))
