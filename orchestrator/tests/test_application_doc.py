"""Applikations-Doku mit der Doku im Mittelpunkt (Dev-Befund 0.14.0): Stammdaten (externer und
interner Name, Verwendungszweck), alle VMs der Applikation als Systeme, ihre EPGs im ACI,
jede Regel mit Richtung und Bezug aus Sicht des Systems (spezifisch bzw. unspezifisch:
Subnetz, any), Kennzahlen und Befunde."""

from test_vmware import ADMIN, _deliver, _seed, _system

OP = {"X-Dev-Roles": "operator"}

# zweite FortiGate: Regel ueber ein ganzes Netz und eine Deny-Regel mit any als Quelle
FW2 = {
    "status": "completed",
    "rules": [
        {"source_platform": "fortigate", "external_id": "10", "name": "net-in",
         "action": "allow", "sources": ["10.9.0.0/16"], "destinations": ["10.20.0.0/16"],
         "services": ["tcp/22"], "source_refs": ["N_clients"], "destination_refs": ["N_srv16"],
         "family": "v4"},
        {"source_platform": "fortigate", "external_id": "11", "name": "any-out",
         "action": "deny", "sources": ["all"], "destinations": ["203.0.113.0/24"],
         "services": ["ALL"], "source_refs": ["all"], "destination_refs": ["N_ext"],
         "family": "v4"},
    ],
    "objects": [
        {"name": "N_clients", "category": "address", "family": "v4", "values": ["10.9.0.0/16"]},
        {"name": "N_srv16", "category": "address", "family": "v4", "values": ["10.20.0.0/16"]},
        {"name": "N_ext", "category": "address", "family": "v4", "values": ["203.0.113.0/24"]},
    ],
}  # fmt: skip


def _app(client) -> tuple[int, dict]:
    ids = _seed(client)
    tid = ids["tid"]
    fw2 = _system(client, tid, "fw-02", "fortigate", base_url="https://fw2")
    _deliver(client, tid, fw2, "fortigate", FW2)
    body = {
        "name": "Webshop",
        "external_name": "Shop Kunde A",
        "internal_name": "webshop-ka",
        "purpose": "prod",
        "code": "007",
        "members": [{"kind": "ap", "ref": "TE_ka/AP_007"}],
    }
    r = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP)
    assert r.status_code == 201, r.text
    return tid, r.json()


def test_stammdaten_externer_interner_name_und_zweck(client):
    tid, app = _app(client)
    assert (app["external_name"], app["internal_name"], app["purpose"]) == (
        "Shop Kunde A",
        "webshop-ka",
        "prod",
    )
    aid = app["id"]
    r = client.patch(
        f"/applications/{aid}", json={"purpose": "test", "internal_name": "  "}, headers=OP
    )
    assert (r.json()["purpose"], r.json()["internal_name"]) == ("test", None)
    assert client.get(f"/applications?tenant_id={tid}").json()[0]["purpose"] == "test"
    bad = client.patch(f"/applications/{aid}", json={"purpose": "qa"}, headers=OP)
    assert bad.status_code == 422
    assert (
        client.patch(f"/applications/{aid}", json={"purpose": None}, headers=OP).json()["purpose"]
        is None
    )


def test_doku_vms_epgs_regeln_mit_richtung_und_bezug(client):
    tid, app = _app(client)
    doc = client.get(f"/applications/{app['id']}/doc").json()

    # Systeme = alle VMs der Applikation (Portgruppen ihrer EPGs), mit Grund und EPG-Pfad
    vms = {s["name"]: s for s in doc["systems"]}
    assert set(vms) == {"web01-007-ka", "db01-007-ka"}
    assert all(s["kind"] == "vm" for s in doc["systems"])
    web, db = vms["web01-007-ka"], vms["db01-007-ka"]
    assert web["epgs"] == ["TE_ka/AP_007/EG_web01"] and web["servers"] == ["10.20.0.5"]
    assert [o["name"] for o in web["objects"]] == ["H_web01"]  # Firewall-Objekt (Link)
    assert web["dns"] == ["web01.example.ch"] and web["ips"] == ["10.20.0.5"]
    assert web["member"] == (
        "Portgruppe TE_ka|AP_007|EG_web01 → EPG TE_ka/AP_007/EG_web01 (AP TE_ka/AP_007)"
    )
    assert db["epgs"] == ["TE_ka/AP_007/EG_backup", "TE_ka/AP_007/EPG_db01"]  # zwei NICs
    assert (web["power_state"], web["guest_os"]) == ("poweredOn", "Ubuntu Linux (64-bit)")

    # EPGs der Applikation im ACI
    epgs = {e["label"]: e for e in doc["epgs"]}
    assert set(epgs) == {"TE_ka/AP_007/EG_web01", "TE_ka/AP_007/EPG_db01", "TE_ka/AP_007/EG_backup"}
    assert epgs["TE_ka/AP_007/EG_web01"]["subnets"] == ["10.20.0.1/24"]
    assert epgs["TE_ka/AP_007/EG_web01"]["vm_count"] == 1
    assert epgs["TE_ka/AP_007/EG_web01"]["member"] == "AP TE_ka/AP_007"

    # Regeln wie in der Regel-Ansicht (Aktion, Herkunft), je System Richtung und Bezug
    rules = {r["id"]: r for r in doc["rules"]}
    by_name = {r["name"]: r for r in doc["rules"]}
    assert set(by_name) == {"web-in", "CT_app:SJ", "net-in", "any-out"}
    assert (
        by_name["any-out"]["action"] == "deny" and by_name["CT_app:SJ"]["domain"] == "aci_contract"
    )
    names = {s["key"]: s["name"] for s in doc["systems"]} | {
        e["key"]: e["label"] for e in doc["epgs"]
    }
    hits = {
        (rules[h["rule_id"]]["name"], names[h["system"]], h["direction"], h["scope"])
        for h in doc["hits"]
    }
    assert {
        ("web-in", "web01-007-ka", "in", "specific"),  # direkt ueber H_web01
        ("CT_app:SJ", "web01-007-ka", "out", "specific"),  # Consumer ueber die EPG der VM
        ("CT_app:SJ", "db01-007-ka", "in", "specific"),
        ("net-in", "db01-007-ka", "in", "subnet"),  # nur ueber 10.20.0.0/16
        ("any-out", "web01-007-ka", "out", "any"),
        ("web-in", "TE_ka/AP_007/EG_web01", "in", "specific"),  # Host im Subnetz der EPG
        ("net-in", "TE_ka/AP_007/EG_web01", "in", "subnet"),
    } <= hits
    via = {(rules[h["rule_id"]]["name"], names[h["system"]]): h["via"] for h in doc["hits"]}
    assert via[("CT_app:SJ", "web01-007-ka")] == "EPG TE_ka/AP_007/EG_web01"
    assert via[("net-in", "db01-007-ka")] == "N_srv16"

    # Kennzahlen: eine Regel zaehlt mit ihrem engsten Bezug
    s = doc["summary"]
    assert (s["vms"], s["servers"], s["epgs"], s["rules"]) == (2, 0, 3, 4)
    assert (s["firewall"], s["aci"], s["specific"], s["unspecific"]) == (3, 1, 2, 2)
    assert s["by_scope"] == {"subnet": 1, "any": 1, "vzany": 0, "pref_group": 0}
    assert web["rules"] == {"specific": 2, "unspecific": 2}

    # Befunde der Applikation (VM-IP fehlt im IPAM)
    assert any(
        f["type"] == "vm_not_in_ipam" and f["subject_id"] == "db01-007-ka" for f in doc["findings"]
    )
    assert s["findings"] == len(doc["findings"])
    # Seite und Objekt der Regel, ueber das das System erfasst wird (0.14.1)
    obj = {
        (rules[h["rule_id"]]["name"], names[h["system"]]): (h["side"], h["object"], h["value"])
        for h in doc["hits"]
    }
    assert obj[("net-in", "db01-007-ka")] == ("dst", "N_srv16", "10.20.0.0/16")
    assert obj[("web-in", "web01-007-ka")] == ("dst", "H_web01", "10.20.0.5")
    assert obj[("any-out", "web01-007-ka")] == ("src", "any", "")
    assert obj[("CT_app:SJ", "web01-007-ka")] == ("src", "EPG TE_ka/AP_007/EG_web01", "")
    # Firewall-Objekte je Name mit den Firewalls und Namenspruefung gegen das IPAM
    assert web["objects"] == [
        {"name": "H_web01", "name_ok": True, "where": [web["objects"][0]["where"][0]]}
    ]
    # Export: 10 Kategorien - je Regel, Seite und Objekt eine Zeile; Netz bzw. any mit den
    # Systemen darin statt einer Zeile je System
    rows = {r["Description"]: [] for t in doc["tables"] for r in t["rows"]}
    for t in doc["tables"]:
        for r in t["rows"]:
            rows[r["Description"]].append(r["Server Name"])
    assert rows["net-in"] == [
        "N_srv16 (10.20.0.0/16): db01-007-ka, web01-007-ka, TE_ka/AP_007/EG_web01"
    ]
    assert rows["any-out"] == ["any: db01-007-ka, web01-007-ka, TE_ka/AP_007/EG_web01"]
    assert "EPG TE_ka/AP_007/EG_web01: web01-007-ka" in rows["CT_app:SJ"]  # ganzer ACI-Pfad


def test_doku_server_ohne_vm(client):
    ids = _seed(client)
    tid = ids["tid"]
    body = {"name": "Nur Server", "members": [{"kind": "host", "ref": "10.20.0.5"}]}
    app = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP).json()
    doc = client.get(f"/applications/{app['id']}/doc").json()
    # der Server hat eine VM (gleiche IP) - er steht als VM da, mit dem Grund
    (web,) = doc["systems"]
    assert (web["kind"], web["name"]) == ("vm", "web01-007-ka")
    assert web["member"] == "IP 10.20.0.5 = Server 10.20.0.5 (Server 10.20.0.5)"
    assert doc["epgs"] == [] and doc["summary"]["vms"] == 1
    assert client.get(f"/applications/{app['id']}/doc", headers=ADMIN).status_code == 200
