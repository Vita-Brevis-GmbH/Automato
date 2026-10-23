# ruff: noqa: S104 - 0.0.0.0/0 sind Subnetze und Routen, keine Bind-Adressen
"""Weg-Suche (0.35.0): ACI-Contracts je VRF-Hop, Suche nach Namen statt IPs, Dienst als
Service-Objekt bzw. ACI-Filter.

Topologie aus `test_aci_relevance` (fw-01 mit Servernetz hinter L3O_fw, VRF TE:VRF1 mit
EPG_web, externe EPGs EN_srv /24, EN_any 0/0, EN_inet 0/0 ueber L3O_inet), dazu EPG_db mit
einem Taboo (tcp/3306), Contract web -> db (tcp/1433) und vzAny -> db (tcp/22).
"""

import copy

import test_aci_relevance as ar
import test_paths as tp

TE = "uni/tn-TE"
DB = f"{TE}/ap-AP1/epg-EPG_db"
ANY = f"{TE}/ctx-VRF1/any"

APIC = copy.deepcopy(ar.APIC)
APIC["inventory"]["subnets"].append(
    {"prefix": "10.50.2.0/24", "gateway": "10.50.2.1/24", "vrf": "TE:VRF1", "context": "epg",
     "context_name": "EPG_db", "aci_tenant": "TE", "ap": "AP1", "epg": "EPG_db"}
)  # fmt: skip
APIC["epgs"] += [
    {"name": "EPG_db", "dn": DB, "tenant": "TE", "ap": "AP1", "bd": "BD_db", "vrf": "VRF1",
     "bd_subnets": ["10.50.2.1/24"], "endpoints": [{"ip": "10.50.2.20"}]},
    {"name": "vzAny", "kind": "vzany", "dn": ANY, "tenant": "TE", "vrf": "VRF1"},
]  # fmt: skip


def _rule(name, src, dst, services, action="allow", kind="contract", refs=()):
    raw = {"name": name, "kind": kind, "scope": "context"}
    return {"source_platform": "cisco_aci", "external_id": f"{TE}/{name}/SJ_1", "name": name,
            "action": action, "sources": src, "destinations": dst, "services": services,
            "service_refs": list(refs), "aci_scope": "context", "raw": raw}  # fmt: skip


APIC["rules"] += [
    _rule("TB_db", ["any"], [f"epg:{DB}"], ["tcp/3306"], "deny", "taboo"),
    _rule("CT_db", [f"epg:{ar.WEB}"], [f"epg:{DB}"], ["tcp/1433", "tcp/3306"], refs=["TE/FT_sql"]),
    _rule("CT_mgmt", [f"epg:{ANY}"], [f"epg:{DB}"], ["tcp/22"]),
]
APIC["objects"] = [
    {"name": "TE/FT_https", "category": "service", "obj_type": "vzFilter", "values": ["tcp/443"]},
    {"name": "TE/FT_web", "category": "service", "obj_type": "vzFilter",
     "values": ["tcp/80", "tcp/443"]},
]  # fmt: skip
FW1 = copy.deepcopy(ar.FW1)
FW1["objects"] = [
    {"name": "H_app01", "category": "address", "values": ["10.20.0.5/32"], "vdom": "root"},
    {"name": "H_legacy01", "category": "address", "values": ["10.20.0.9/32"], "vdom": "root"},
    {"name": "N_server", "category": "address", "values": ["10.20.0.0/24"], "vdom": "root"},
]


def _seed(client) -> int:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=tp.ADMIN).json()["id"]
    for name, platform, result in (("fw-01", "fortigate", FW1), ("apic-01", "cisco_aci", APIC)):
        tp._deliver(client, tid, tp._system(client, tid, name, platform), platform, result)
    ddi = tp._system(client, tid, "ddi", "infoblox", username="u", password="p")
    run_id = tp._run(client, tid, ddi, "infoblox")
    client.post(f"/internal/collector-runs/{run_id}/references", headers=tp.TOKEN,
                json={"records": tp.DDI})  # fmt: skip
    done = {"status": "completed", "reference_count": len(tp.DDI)}
    client.post(f"/internal/collector-runs/{run_id}/result", headers=tp.TOKEN, json=done)
    return tid


def _aci(client, tid, **params) -> tuple[dict, dict, dict]:
    """(Regeln des ACI-Hops, Gesamturteil, Antwort) des einzigen Wegs."""
    out = tp._trace(client, tid, **params)
    (path,) = out["paths"]
    (hop,) = (h for h in path["hops"] if h["kind"] == "vrf")
    return hop["rules"], path["policy"], out


def test_contracts_je_vrf_hop(client):
    tid = _seed(client)
    web = {"src": "10.20.0.5", "dst": "10.50.1.10"}

    # Firewall erlaubt, im ACI ordnet EN_srv (/24) die Quelle zu - CT_srv erlaubt tcp/443
    rules, policy, _ = _aci(client, tid, **web, service="tcp/443")
    assert (rules["verdict"], rules["text"]) == ("allow", "erlaubt durch Contract CT_srv")
    assert rules["src_epg"] == "externe EPG L3O_fw|EN_srv (10.20.0.0/24)"
    assert rules["dst_epg"] == "EPG TE/AP1/EPG_web"
    (ct,) = rules["rules"]
    assert (ct["kind"], ct["src"], ct["dst"]) == ("contract", "direkt", "direkt")
    assert ct["decides"]
    assert policy == {"verdict": "allow", "text": "erlaubt (1 Firewall, 1 ACI-VRF)"}

    # ohne Dienst: nur möglich
    rules, policy, _ = _aci(client, tid, **web)
    assert rules["verdict"] == "depends" and rules["rules"][0]["unsure"] == ["Dienst"]

    # tcp/80 gilt nur für EN_any - die Quelle gehört aber zu EN_srv
    rules, policy, _ = _aci(client, tid, **web, service="tcp/80")
    assert rules == {
        "verdict": "implicit_deny",
        "rules": [],
        "text": "kein Contract von externe EPG L3O_fw|EN_srv (10.20.0.0/24) zu EPG TE/AP1/EPG_web "
        "– im ACI gesperrt",
        "src_epg": "externe EPG L3O_fw|EN_srv (10.20.0.0/24)",
        "dst_epg": "EPG TE/AP1/EPG_web",
    }
    assert policy["verdict"] == "deny"

    # ausserhalb des /24: EN_any (über L3O_fw geleitet) bzw. EN_inet (unbekannt) - abhängig
    start = next(
        n["key"]
        for n in client.get("/routing/summary", params={"tenant_id": tid}).json()["nodes"]
        if n["label"] == "fw-01 · VDOM root"
    )
    other = {"src": "10.20.1.7", "start": start, "dst": "10.50.1.10", "service": "tcp/80"}
    rules, _, _ = _aci(client, tid, **other)
    assert rules["verdict"] == "depends"
    assert rules["src_epg"] == (
        "externe EPG L3O_fw|EN_any (0.0.0.0/0) bzw. externe EPG L3O_inet|EN_inet (0.0.0.0/0)"
    )
    assert [r["name"] for r in rules["rules"]] == ["CT_any"]

    # Startpunkt ohne Quelle: Contracts nicht prüfbar
    rules, policy, _ = _aci(client, tid, start=start, dst="10.50.1.10", service="tcp/443")
    assert (rules["verdict"], rules["text"]) == (
        "unknown",
        "Quelle unbekannt – Contracts nicht prüfbar",
    )


def test_im_fabric_taboo_vzany_gleiche_epg(client):
    tid = _seed(client)
    db = {"src": "10.50.1.10", "dst": "10.50.2.20"}

    rules, policy, out = _aci(client, tid, **db, service="tcp/1433")
    assert out["start"][0]["via"].startswith("Quelle im Fabric")
    assert (rules["verdict"], rules["text"]) == ("allow", "erlaubt durch Contract CT_db")
    assert rules["rules"][0]["services"] == ["FT_sql"]  # Name des Filters statt tcp/1433
    assert (rules["src_epg"], rules["dst_epg"]) == ("EPG TE/AP1/EPG_web", "EPG TE/AP1/EPG_db")
    assert policy == {"verdict": "allow", "text": "erlaubt (1 ACI-VRF)"}

    # Taboo sperrt vor dem Contract, der tcp/3306 auch erlaubt
    rules, policy, _ = _aci(client, tid, **db, service="tcp/3306")
    assert (rules["verdict"], rules["text"]) == ("deny", "gesperrt durch Taboo TB_db")
    assert [r["name"] for r in rules["rules"]] == ["TB_db"]
    assert rules["rules"][0]["src"] == "any"
    assert policy["text"] == "gesperrt an apic-01 · VRF TE:VRF1"

    # über vzAny der VRF
    rules, _, _ = _aci(client, tid, **db, service="tcp/22")
    assert rules["text"] == "erlaubt durch Contract CT_mgmt"
    assert rules["rules"][0]["src"] == "vzAny TE:VRF1"

    # nichts passt
    rules, _, _ = _aci(client, tid, **db, service="udp/53")
    assert rules["verdict"] == "implicit_deny"

    # gleiche EPG: ohne Contract
    rules, _, _ = _aci(client, tid, src="10.50.1.10", dst="10.50.1.20", service="tcp/5000")
    assert rules["verdict"] == "allow"
    assert rules["text"] == "gleiche EPG (EPG TE/AP1/EPG_web) – ohne Contract erlaubt"


def test_dienst_als_objektname(client):
    tid = _seed(client)
    web = {"src": "10.20.0.5", "dst": "10.50.1.10"}

    rules, _, out = _aci(client, tid, **web, service="FT_https")
    assert rules["verdict"] == "allow"
    assert (out["service"], out["service_name"]) == ("tcp/443", "FT_https")

    # mehrere Dienste: der erste wird geprüft, die anderen stehen im Hinweis
    rules, _, out = _aci(client, tid, **web, service="TE/FT_web")
    assert out["service"] == "tcp/80"
    assert out["notes"][0] == "TE/FT_web: geprüft tcp/80 – weitere Dienste: tcp/443"

    r = client.get("/routing/trace", params={"tenant_id": tid, **web, "service": "FT_nix"})
    assert r.status_code == 422 and "FT_nix" in r.json()["detail"]


def test_suche_nach_namen(client):
    tid = _seed(client)

    def find(q):
        r = client.get("/routing/resolve", params={"tenant_id": tid, "q": q})
        assert r.status_code == 200, r.text
        return r.json()

    # IPAM: DNS-Name, auch nur das erste Label; das Firewall-Objekt mit derselben IP dazu
    (hit,) = find("app01")
    assert (hit["ip"], hit["name"], hit["kind"], hit["source"]) == (
        "10.20.0.5",
        "app01.example.ch",
        "ipam",
        "ddi",
    )
    assert hit["also"] == ["H_app01"]
    assert find("APP01.example.ch.")[0]["ip"] == "10.20.0.5"

    # IP direkt mit dem Namen aus dem IPAM
    assert find("10.20.0.5") == [
        {"ip": "10.20.0.5", "name": "app01.example.ch", "kind": "ip", "source": None,
         "detail": None}
    ]  # fmt: skip

    # Objekt der Firewall ohne IPAM-Eintrag (ohne Präfix gesucht); Netze zählen nicht
    (fw,) = find("legacy01")
    assert (fw["ip"], fw["name"], fw["kind"], fw["source"]) == (
        "10.20.0.9",
        "H_legacy01",
        "object",
        "fw-01",
    )
    assert find("N_server") == [] and find("x") == [] and find("gibtsnicht") == []

    # Weg zum Namen: Oberfläche löst auf, die API nimmt IPs
    r = client.get("/routing/trace", params={"tenant_id": tid, "dst": "app01"})
    assert r.status_code == 422


def test_dienste_mit_objektnamen(client):
    """0.36.0: Regel-Liste und Applikations-Doku zeigen den Namen des Filters bzw. Service-
    Objekts (FT_sql) statt der Werte (tcp/1433) - ACI ohne den Tenant davor."""
    tid = _seed(client)
    (row,) = client.get("/rules", params={"tenant_id": tid, "q": "CT_db"}).json()
    assert row["service_names"] == ["FT_sql"]
    detail = client.get(f"/rules/{row['id']}").json()
    assert sorted(detail["services"]) == ["tcp/1433", "tcp/3306"]  # Werte bleiben im Detail
    # ohne Verweis: die Werte
    (mgmt,) = client.get("/rules", params={"tenant_id": tid, "q": "CT_mgmt"}).json()
    assert mgmt["service_names"] == ["tcp/22"]
