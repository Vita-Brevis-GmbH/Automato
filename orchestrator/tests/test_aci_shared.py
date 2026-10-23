# ruff: noqa: S104 - 0.0.0.0/0 sind Subnetze und Routen, keine Bind-Adressen
"""ACI zwischen VRFs (0.44.0, Befund 0.43.1: die Applikations-Doku zeigte einen Zugriff auf ein
System in einem anderen Tenant, die Weg-Suche sperrte ihn).

Topologie aus `test_aci_check` (VRF TE:VRF1 mit EPG_web 10.50.1.0/24), dazu EG_srv im Tenant
TE2 (VRF TE2:VRF2, 10.60.0.0/24) und ein Contract CT_shared mit Scope global (Consumer
EPG_web, Provider EG_srv). Der Contract allein oeffnet keinen Weg zwischen den VRFs - erst
geteilte Subnetze (Shared Service) bzw. Route-Leaking. Weg-Suche und Doku urteilen gleich.
"""

import copy

import test_aci_check as ac
import test_paths as tp

OP = {"X-Dev-Roles": "operator"}
SRV = "uni/tn-TE2/ap-AP9/epg-EG_srv"


def _apic(shared: bool, leak: bool = False) -> dict:
    apic = copy.deepcopy(ac.APIC)
    web = next(e for e in apic["epgs"] if e["name"] == "EPG_web")
    srv = {"name": "EG_srv", "dn": SRV, "tenant": "TE2", "ap": "AP9", "bd": "BD_srv",
           "vrf": "VRF2", "vrf_tenant": "TE2", "subnets": ["10.60.0.1/24"]}  # fmt: skip
    if shared:
        web["bd_subnet_scopes"] = {"10.50.1.1/24": "public,shared"}
        srv["subnet_scopes"] = {"10.60.0.1/24": "private,shared"}
    else:
        web["bd_subnet_scopes"] = {"10.50.1.1/24": "public"}
        srv["subnet_scopes"] = {"10.60.0.1/24": "private"}
    apic["epgs"].append(srv)
    apic["inventory"]["subnets"].append(
        {"prefix": "10.60.0.0/24", "gateway": "10.60.0.1/24", "vrf": "TE2:VRF2", "context": "epg",
         "context_name": "EG_srv", "aci_tenant": "TE2", "ap": "AP9", "epg": "EG_srv"}
    )  # fmt: skip
    apic["rules"].append(
        {"source_platform": "cisco_aci", "external_id": "uni/tn-TE2/brc-CT_shared/SJ_1",
         "name": "CT_shared", "action": "allow", "sources": [f"epg:{ac.ar.WEB}"],
         "destinations": [f"epg:{SRV}"], "services": ["tcp/443"], "aci_scope": "global",
         "raw": {"name": "CT_shared", "kind": "contract", "scope": "global"}}
    )  # fmt: skip
    if leak:  # Route-Leaking statt geteilter Subnetze (leakRoutes)
        apic["inventory"]["vrf_leaks"] = [
            {"vrf": "TE2:VRF2", "prefix": "10.60.0.0/24", "to": ["TE:VRF1"]},
            {"vrf": "TE:VRF1", "prefix": "10.50.1.0/24", "to": ["TE2:VRF2"]},
        ]
    return apic


def _seed(client, apic: dict) -> int:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=tp.ADMIN).json()["id"]
    for name, platform, result in (("fw-01", "fortigate", ac.FW1), ("apic-01", "cisco_aci", apic)):
        tp._deliver(client, tid, tp._system(client, tid, name, platform), platform, result)
    return tid


def _app_rules(client, tid) -> tuple[set[str], dict]:
    body = {"name": "Web", "members": [{"kind": "epg", "ref": ac.ar.WEB}]}
    app = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP)
    assert app.status_code == 201, app.text
    doc = client.get(f"/applications/{app.json()['id']}/doc").json()
    return {r["name"] for r in doc["rules"]}, doc


def test_shared_service_weg_im_fabric_und_contract(client):
    tid = _seed(client, _apic(shared=True))
    out = tp._trace(client, tid, src="10.50.1.10", dst="10.60.0.5", service="tcp/443")
    (path,) = out["paths"]
    end = path["end"]
    assert end["kind"] == "fabric", end
    assert "VRF TE2:VRF2" in end["text"] and "Shared Service" in end["text"]
    (hop,) = (h for h in path["hops"] if h["kind"] == "vrf")
    assert hop["leak"]["vrf"] == "TE2:VRF2"
    rules = hop["rules"]
    assert rules["verdict"] == "allow" and "CT_shared" in rules["text"], rules
    assert rules["dst_epg"] == "EPG TE2/AP9/EG_srv"
    assert path["policy"]["verdict"] == "allow"
    # anderer Dienst: kein Contract
    other = tp._trace(client, tid, src="10.50.1.10", dst="10.60.0.5", service="tcp/22")
    assert other["paths"][0]["policy"]["verdict"] in ("implicit_deny", "deny")

    names, _doc = _app_rules(client, tid)
    assert "CT_shared" in names


def test_ohne_geteiltes_subnetz_kein_weg_im_fabric(client):
    """Kein Shared Service: die VRF kennt das Ziel nicht - die Weg-Suche folgt ihrem Routing
    (hier die Default-Route ueber L3O_inet), die Doku nimmt den Contract nicht auf."""
    tid = _seed(client, _apic(shared=False))
    out = tp._trace(client, tid, src="10.50.1.10", dst="10.60.0.5", service="tcp/443")
    for path in out["paths"]:
        assert "TE2:VRF2" not in path["end"]["text"]
        assert all("leak" not in h for h in path["hops"])
    names, doc = _app_rules(client, tid)
    assert "CT_shared" not in names
    assert doc["summary"]["impossible"] >= 1
    # Doku je System: unter „nicht möglich“ mit Begruendung
    systems = client.get("/appdoc/systems", params={"tenant_id": tid}).json()
    name = next(s["name"] for s in systems if s["name"].startswith("TE/AP1/EPG_web")
                or "EPG_web" in s["name"])  # fmt: skip
    one = client.get(f"/appdoc/systems/{name}", params={"tenant_id": tid}).json()
    gone = {x["description"]: x["text"] for x in one["impossible"]}
    assert "kein geteiltes Subnetz" in gone["CT_shared"]


def test_route_leaking_statt_shared_service(client):
    tid = _seed(client, _apic(shared=False, leak=True))
    out = tp._trace(client, tid, src="10.50.1.10", dst="10.60.0.5", service="tcp/443")
    (path,) = out["paths"]
    assert path["end"]["kind"] == "fabric" and "Route-Leaking 10.60.0.0/24" in path["end"]["text"]
    assert path["policy"]["verdict"] == "allow"
    names, _doc = _app_rules(client, tid)
    assert "CT_shared" in names
