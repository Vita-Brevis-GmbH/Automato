"""Scope der ACI-Contracts im ganzen Modell (0.45.1, Anwender): AP verbindet nur innerhalb des
AP, VRF nur innerhalb der VRF, Tenant im Tenant auch ueber mehrere VRFs, global alle
verbundenen EPGs.

Ein APIC mit TE_a (VRF_a, VRF_a2, AP_100, AP_infra) und TE_b (VRF_b); Contracts aus `common`
bzw. TE_a mit verschiedenen Scopes. Geprueft: die Regel selbst (`scope_allows`), das
Regel-Detail (Paare, Endpunkte ohne Gegenstelle), die Regel-Liste fuer eine IP und der Befund
`aci_scope_unpaired`.
"""

from app.models import Epg
from app.store.aci_relevance import Classifier
from test_reference_sources import TOKEN, _system, _tenant

DNS = "uni/tn-TE_a/ap-AP_infra/epg-EG_dns"
WEB = "uni/tn-TE_a/ap-AP_100/epg-EG_web"
WEB2 = "uni/tn-TE_a/ap-AP_100/epg-EG_web2"  # gleicher Tenant, andere VRF
APP_B = "uni/tn-TE_b/ap-AP_200/epg-EG_app"
ANY_A = "uni/tn-TE_a/ctx-VRF_a/any"


def _epg(dn, tenant, ap, vrf, subnet, ip):
    name = dn.rsplit("epg-", 1)[1]
    return {"name": name, "dn": dn, "tenant": tenant, "ap": ap, "bd": f"BD_{name}", "vrf": vrf,
            "vrf_tenant": tenant, "bd_subnets": [subnet], "endpoints": [{"ip": ip}]}  # fmt: skip


def _ct(name, tenant, scope, consumers, provider=DNS):
    sources = [f"epg:{c}" for c in consumers]
    return {"source_platform": "cisco_aci", "external_id": f"uni/tn-{tenant}/brc-{name}/SJ_1",
            "name": name, "action": "allow", "sources": sources,
            "destinations": [f"epg:{provider}"], "services": ["udp/53"], "aci_scope": scope,
            "raw": {"name": name, "kind": "contract", "scope": scope,
                    "tenant": tenant}}  # fmt: skip


RESULT = {
    "status": "completed",
    "epgs": [
        _epg(DNS, "TE_a", "AP_infra", "VRF_a", "10.1.0.1/24", "10.1.0.53"),
        _epg(WEB, "TE_a", "AP_100", "VRF_a", "10.1.1.1/24", "10.1.1.10"),
        _epg(WEB2, "TE_a", "AP_100", "VRF_a2", "10.1.2.1/24", "10.1.2.10"),
        _epg(APP_B, "TE_b", "AP_200", "VRF_b", "10.2.0.1/24", "10.2.0.10"),
        {
            "name": "vzAny",
            "kind": "vzany",
            "dn": ANY_A,
            "tenant": "TE_a",
            "vrf": "VRF_a",
            "vrf_tenant": "TE_a",
        },
    ],  # fmt: skip
    "rules": [
        # VRF: EG_web (VRF_a) ja, EG_app in TE_b/VRF_b nicht - Contract aus common
        _ct("CT_dns_vrf", "common", "context", [WEB, APP_B]),
        # Tenant: EG_web2 in VRF_a2 ja (gleicher Tenant), EG_app (TE_b) nicht
        _ct("CT_dns_tn", "TE_a", "tenant", [WEB2, APP_B]),
        # AP: EG_web liegt in AP_100, der Provider in AP_infra - verbindet nichts
        _ct("CT_dns_ap", "TE_a", "application-profile", [WEB]),
        # AP mit vzAny: alle EPGs der VRF_a, darunter die des AP_infra
        _ct("CT_dns_any", "TE_a", "application-profile", [ANY_A]),
        # global: alle
        _ct("CT_dns_gl", "common", "global", [WEB, WEB2, APP_B]),
    ],
}


def _seed(client) -> int:
    tid = _tenant(client)
    apic = _system(client, tid, name="apic", platform="cisco_aci", base_url="https://apic")
    body = {"platform": "cisco_aci", "tenant_id": tid, "source_system_id": apic["id"]}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=RESULT)
    assert r.json()["status"] == "completed", r.text
    return tid


def _rules(client, tid) -> dict[str, int]:
    rows = client.get("/rules", params={"tenant_id": tid, "domain": "aci_contract"}).json()
    return {r["name"]: r["id"] for r in rows}


def test_scope_regeln(client, session_factory):
    tid = _seed(client)
    with session_factory() as s:
        cls = Classifier(s, tid)
        e = {x.dn: x for x in s.query(Epg).filter(Epg.tenant_id == tid)}
        dns, web, web2, app_b, any_a = (e[d] for d in (DNS, WEB, WEB2, APP_B, ANY_A))
        assert cls.scope_allows("context", web, dns)
        assert not cls.scope_allows("context", app_b, dns)
        assert "verschiedenen VRFs" in cls.scope_reason("context", web2, dns)
        assert cls.scope_allows("tenant", web2, dns)  # gleicher Tenant, andere VRF
        assert "verschiedenen Tenants" in cls.scope_reason("tenant", app_b, dns)
        assert not cls.scope_allows("application-profile", web, dns)
        assert cls.scope_allows("application-profile", web, web2)  # gleiches AP_100
        assert cls.scope_allows("application-profile", any_a, dns)  # vzAny der VRF_a
        assert not cls.scope_allows("application-profile", any_a, app_b)
        assert all(cls.scope_allows("global", a, b) for a in (web, web2, app_b) for b in (dns,))


def test_regel_detail_zeigt_was_der_scope_verbindet(client):
    tid = _seed(client)
    ids = _rules(client, tid)
    d = client.get(f"/rules/{ids['CT_dns_vrf']}").json()
    assert d["aci_scope"]["label"] == "VRF" and d["aci_scope"]["pairs"] == 1
    assert d["aci_scope"]["unpaired"] == 1
    partners = {e["epg"]["name"]: e["scope_partners"] for e in d["endpoints"]}
    assert partners == {"EG_web": 1, "EG_app": 0, "EG_dns": 1}
    assert client.get(f"/rules/{ids['CT_dns_ap']}").json()["aci_scope"]["pairs"] == 0
    assert client.get(f"/rules/{ids['CT_dns_any']}").json()["aci_scope"]["pairs"] == 1
    gl = client.get(f"/rules/{ids['CT_dns_gl']}").json()["aci_scope"]
    assert (gl["pairs"], gl["unpaired"]) == (3, 0)


def test_regel_liste_fuer_eine_ip_mit_scope(client):
    tid = _seed(client)
    params = {"tenant_id": tid, "q": "10.2.0.10", "domain": "aci_contract"}
    rel = {x["name"]: x["relevance"] for x in client.get("/rules", params=params).json()}
    # EG_app (TE_b/VRF_b): ueber VRF- und Tenant-Scope keine Gegenstelle, global schon
    assert rel["CT_dns_vrf"]["state"] == "impossible"
    assert "Scope VRF" in rel["CT_dns_vrf"]["text"]
    assert rel["CT_dns_tn"]["state"] == "impossible"
    assert (rel["CT_dns_gl"] or {}).get("state") != "impossible"  # ohne Urteil: nicht geprueft
    # EG_web (VRF_a): im Scope - nicht ausgeschlossen
    params["q"] = "10.1.1.10"
    rel = {x["name"]: x["relevance"] for x in client.get("/rules", params=params).json()}
    assert (rel["CT_dns_vrf"] or {}).get("state") != "impossible"
    assert rel["CT_dns_ap"]["state"] == "impossible"  # AP_100 vs. AP_infra


def test_befund_ohne_gegenstelle_im_scope(client):
    tid = _seed(client)
    params = {"tenant_id": tid, "type": "aci_scope_unpaired"}
    found = {f["detail"]["name"]: f for f in client.get("/findings", params=params).json()}
    assert set(found) == {"CT_dns_vrf", "CT_dns_tn", "CT_dns_ap"}
    assert found["CT_dns_ap"]["severity"] == "warning"  # verbindet nichts
    vrf = found["CT_dns_vrf"]
    assert vrf["severity"] == "info" and vrf["detail"]["epgs"] == ["TE_b/AP_200/EG_app"]
    assert "ohne Gegenstelle im selben VRF" in vrf["message"]
    assert "verschiedenen VRFs" in vrf["detail"]["reason"]
