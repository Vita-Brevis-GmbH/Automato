"""Doku je System erfasst alle Regeln (Dev-Befund 0.10.0): Firewall-Regeln direkt und ueber
Netze, Gruppen und any (gleichwertig, mit Bezug), ACI-Contracts direkt, ueber vzAny der VRF und
ueber ESGs; die EPG eines Servers bringt ihre Contracts mit; Regeln je Subnetz."""

ADMIN = {"X-Dev-Roles": "admin"}
TOKEN = {"X-Internal-Token": "dev-only-token"}
APP = "uni/tn-TE/ap-AP_001/epg-EG_app"
DB = "uni/tn-TE/ap-AP_001/epg-EG_db"
ESG = "uni/tn-TE/ap-AP_001/esg-ES_web"
ANY = "uni/tn-TE/ctx-VRF1/any"

FW_OBJECTS = [
    {"name": "H_app01", "category": "address", "family": "v4", "values": ["10.20.0.5"]},
    {"name": "N_srv", "category": "address", "family": "v4", "values": ["10.20.0.0/24"]},
    {"name": "G_srv", "category": "address_group", "family": "v4", "members": ["N_srv"],
     "values": ["10.20.0.0/24"]},
    {"name": "N_clients", "category": "address", "family": "v4", "values": ["10.9.0.0/16"]},
    {"name": "N_ext", "category": "address", "family": "v4", "values": ["203.0.113.0/24"]},
    {"name": "N_other", "category": "address", "family": "v4", "values": ["10.30.0.0/24"]},
]  # fmt: skip


def _fw(ext_id, name, src, dst, src_refs, dst_refs):
    return {
        "source_platform": "fortigate",
        "external_id": ext_id,
        "name": name,
        "action": "allow",
        "sources": src,
        "destinations": dst,
        "services": ["tcp/443"],
        "source_refs": src_refs,
        "destination_refs": dst_refs,
        "service_refs": ["HTTPS"],
        "src_zone": "lan",
        "dst_zone": "srv",
        "family": "v4",
    }


FW_RULES = [
    _fw("1", "direct-in", ["10.9.0.0/16"], ["10.20.0.5"], ["N_clients"], ["H_app01"]),
    _fw("2", "net-in", ["10.9.0.0/16"], ["10.20.0.0/24"], ["N_clients"], ["G_srv"]),
    _fw("3", "any-out", ["all"], ["203.0.113.0/24"], ["all"], ["N_ext"]),
    _fw("4", "other", ["10.9.0.0/16"], ["10.30.0.0/24"], ["N_clients"], ["N_other"]),
]


def _ct(name, src, dst):
    return {
        "source_platform": "cisco_aci",
        "external_id": f"uni/tn-TE/brc-{name}/SJ",
        "name": f"{name}:SJ",
        "action": "allow",
        "sources": [f"epg:{src}"],
        "destinations": [f"epg:{dst}"],
        "services": ["tcp/5432"],
        "aci_scope": "context",
        "raw": {"tenant": "TE", "kind": "contract", "dn": f"uni/tn-TE/brc-{name}"},
    }


ACI = {
    "status": "completed",
    "epgs": [
        {"name": "EG_app", "dn": APP, "tenant": "TE", "ap": "AP_001", "vrf": "VRF1",
         "subnets": ["10.20.0.1/24"], "endpoints": [{"ip": "10.20.0.5"}]},
        {"name": "EG_db", "dn": DB, "tenant": "TE", "ap": "AP_001", "vrf": "VRF1"},
        {"name": "ES_web", "kind": "esg", "dn": ESG, "tenant": "TE", "ap": "AP_001",
         "vrf": "VRF1", "selectors": [f"EPG {APP}"]},
        {"name": "vzAny", "kind": "vzany", "dn": ANY, "tenant": "TE", "vrf": "VRF1"},
    ],
    "rules": [_ct("CT_direct", APP, DB), _ct("CT_vz", ANY, DB), _ct("CT_esg", DB, ESG)],
}  # fmt: skip


def _deliver(client, tid, name, platform, result):
    body = {"tenant_id": tid, "name": name, "platform": platform, "base_url": "https://x"}
    sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
    run = {"platform": platform, "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=run).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text


def _seed(client) -> int:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    _deliver(client, tid, "apic-01", "cisco_aci", ACI)
    fw = {"status": "completed", "rules": FW_RULES, "objects": FW_OBJECTS}
    _deliver(client, tid, "fw-01", "fortigate", fw)
    return tid


def _rows(doc) -> dict[str, list[dict]]:
    """Beschreibung (Regel) -> Zeilen ueber alle Tabellen."""
    out: dict[str, list[dict]] = {}
    for t in doc["tables"]:
        assert t["columns"][-1] == "Bezug"
        for row in t["rows"]:
            out.setdefault(row["Description"], []).append({**row, "table": t["index"]})
    return out


def test_server_doku_mit_netzen_any_und_epg(client):
    tid = _seed(client)
    doc = client.get("/appdoc/systems/10.20.0.5", params={"tenant_id": tid}).json()
    rows = _rows(doc)
    assert rows["direct-in"][0]["Bezug"] == "direkt"
    assert rows["net-in"][0]["Bezug"] == "G_srv"  # ueber die Gruppe mit dem Netz
    assert rows["net-in"][0]["_scope"] == "subnet"  # fuer den Server: ein ganzes Netz
    assert rows["any-out"][0]["Bezug"] == "any"
    assert rows["any-out"][0]["Target Systems"] == "203.0.113.0/24"
    assert "other" not in rows
    # Server steht als gelernter Endpunkt in EG_app: deren Contracts gehoeren dazu
    assert rows["CT_direct:SJ"][0]["Bezug"] == "EPG AP_001/EG_app"
    assert {r["Description"] for r in sum(rows.values(), [])} >= {"CT_vz:SJ", "CT_esg:SJ"}


def test_epg_doku_mit_vzany_esg_und_firewall(client):
    tid = _seed(client)
    doc = client.get("/appdoc/systems/EG_app", params={"tenant_id": tid}).json()
    rows = _rows(doc)
    assert rows["CT_direct:SJ"][0]["Bezug"] == "direkt"
    assert rows["CT_vz:SJ"][0]["Bezug"] == "vzAny VRF1"
    assert rows["CT_esg:SJ"][0]["Bezug"] == "ESG ES_web"
    assert rows["CT_esg:SJ"][0]["Target Systems"] == "TE/AP_001/EG_db"
    # Firewall-Regeln, die das EPG-Subnetz bzw. den Server darin erfassen
    assert {"direct-in", "net-in", "any-out"} <= set(rows) and "other" not in rows
    assert rows["net-in"][0]["Server Name"] == "TE/AP_001/EG_app"  # ganzer ACI-Pfad (0.14.0)
    # Art des Bezugs (0.14.0): EPG selbst bzw. Host im Subnetz = spezifisch, sonst Netz/any/vzAny
    assert rows["CT_direct:SJ"][0]["_scope"] == rows["CT_esg:SJ"][0]["_scope"] == "specific"
    assert rows["CT_vz:SJ"][0]["_scope"] == "vzany"
    assert rows["direct-in"][0]["_scope"] == "specific" and rows["direct-in"][0]["_dir"] == "in"
    assert rows["net-in"][0]["_scope"] == "specific"  # genau das Subnetz der EPG
    assert rows["any-out"][0]["_scope"] == "any" and rows["any-out"][0]["_dir"] == "out"


def test_regeln_je_subnetz(client):
    tid = _seed(client)

    def names(**params):
        r = client.get("/rules", params={"tenant_id": tid, "domain": "firewall", **params})
        return sorted(x["name"] for x in r.json())

    assert names(net="10.20.0.0/24") == ["any-out", "direct-in", "net-in"]
    assert names(net="10.20.0.0/24", net_any="false") == ["direct-in", "net-in"]
    # Netz im Suchfeld: Regeln mit Bereichen, die sich mit ihm ueberschneiden (ohne any)
    assert names(q="10.20.0.0/25") == ["direct-in", "net-in"]
