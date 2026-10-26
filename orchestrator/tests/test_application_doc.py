"""Applikations-Doku mit der Doku im Mittelpunkt (Dev-Befund 0.14.0): Stammdaten (externer und
interner Name, Verwendungszweck), alle VMs der Applikation als Systeme, ihre EPGs im ACI,
jede Regel mit Richtung und Bezug aus Sicht des Systems (spezifisch bzw. unspezifisch:
Subnetz, any), Kennzahlen und Befunde."""

import copy

from app.models import Epg
from app.store import aci_relevance
from sqlalchemy import select
from test_vmware import ACI, ADMIN, VCENTER, _deliver, _seed, _system
from test_vmware import DB as DB_DN
from test_vmware import WEB as WEB_DN

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
    # je Befund die EPGs (Tenant/AP/EPG) - Filter nach Tenant, AP, EPG (0.38.0): die VM db01
    # haengt an zwei EPGs, Befunde zu Regeln an den EPGs der Systeme, die sie treffen
    (vm,) = (f for f in doc["findings"] if f["type"] == "vm_not_in_ipam")
    assert vm["epgs"] == ["TE_ka/AP_007/EG_backup", "TE_ka/AP_007/EPG_db01"]
    assert all(e.startswith("TE_ka/AP_007/") for f in doc["findings"] for e in f["epgs"])
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
    # Export: 10 Kategorien - je Regel, Seite und Objekt eine Zeile statt einer Zeile je
    # System; unspezifische Regeln (Netz, any) nur mit ihrem Objekt (0.37.0), die Systeme
    # der Applikation, die sie nutzen können, in `_systems` (Knopf in der Oberfläche)
    rows = {r["Description"]: [] for t in doc["tables"] for r in t["rows"]}
    for t in doc["tables"]:
        for r in t["rows"]:
            rows[r["Description"]].append((r["Server Name"], r.get("_systems")))
    # die EPG mit den IPs ihrer VMs (Portgruppe, 0.43.1): EPG_db01 ueber db01 im /16
    everyone = ["db01-007-ka", "web01-007-ka", "TE_ka/AP_007/EG_web01", "TE_ka/AP_007/EPG_db01"]
    assert rows["net-in"] == [("N_srv16 (10.20.0.0/16)", everyone)]
    assert rows["any-out"] == [("any", [*everyone[:2], "TE_ka/AP_007/EG_backup", *everyone[2:]])]
    # spezifisch: das Objekt mit seinen Systemen, ganzer ACI-Pfad
    assert ("EPG TE_ka/AP_007/EG_web01: web01-007-ka", None) in rows["CT_app:SJ"]


def test_befund_einer_regel_mit_den_epgs_ihrer_treffer(client):
    """0.38.0: Befund zu einer Regel (ohne Quelle) - die EPGs der Systeme, die sie trifft, fuer
    den Filter nach Tenant, AP, EPG in der Doku."""
    tid, app = _app(client)
    fw3 = _system(client, tid, "fw-03", "fortigate", base_url="https://fw3")
    rule = {"source_platform": "fortigate", "external_id": "20", "name": "web-halb",
            "action": "allow", "sources": [], "destinations": ["10.20.0.5"],
            "services": ["tcp/8443"], "destination_refs": ["H_web01"], "family": "v4"}  # fmt: skip
    _deliver(client, tid, fw3, "fortigate", {"status": "completed", "rules": [rule]})
    doc = client.get(f"/applications/{app['id']}/doc").json()
    (f,) = (f for f in doc["findings"] if f["type"] == "rule_incomplete")
    assert f["rule_id"] is not None
    assert f["epgs"] == ["TE_ka/AP_007/EG_web01"]


def _other_tenants() -> dict:
    """Dev-Befund 0.42.2: weitere ACI-Tenants im selben APIC - TE_kb mit gleichem EPG-Namen und
    einem Endpunkt mit der IP von web01 (gleiche Adressen in einer anderen VRF), TE_kc mit einer
    externen EPG 0.0.0.0/0; je ein Contract, der die Applikation nicht betrifft."""
    kb, kc = "uni/tn-TE_kb", "uni/tn-TE_kc"
    aci = copy.deepcopy(ACI)
    aci["epgs"] += [
        {"name": "EG_web01", "dn": f"{kb}/ap-AP_100/epg-EG_web01", "tenant": "TE_kb",
         "ap": "AP_100", "vrf": "VRF1", "bd_subnets": ["10.20.0.1/24"],
         "endpoints": [{"ip": "10.20.0.5"}]},
        {"name": "EG_db", "dn": f"{kb}/ap-AP_100/epg-EG_db", "tenant": "TE_kb", "ap": "AP_100",
         "vrf": "VRF1"},
        {"name": "EG_app", "dn": f"{kc}/ap-AP_200/epg-EG_app", "tenant": "TE_kc", "ap": "AP_200",
         "vrf": "VRF_c", "bd_subnets": ["10.30.0.1/24"]},
        {"name": "EN_any", "kind": "external", "dn": f"{kc}/out-L3O_c/instP-EN_any",
         "tenant": "TE_kc", "l3out": "L3O_c", "vrf": "VRF_c", "subnets": ["0.0.0.0/0"]},  # noqa: S104
    ]  # fmt: skip
    aci["rules"] += [
        {"source_platform": "cisco_aci", "external_id": f"{kb}/brc-CT_kb/SJ", "name": "CT_kb:SJ",
         "action": "allow", "sources": [f"epg:{kb}/ap-AP_100/epg-EG_web01"],
         "destinations": [f"epg:{kb}/ap-AP_100/epg-EG_db"], "services": ["tcp/1433"],
         "aci_scope": "context", "raw": {"tenant": "TE_kb", "dn": f"{kb}/brc-CT_kb"}},
        {"source_platform": "cisco_aci", "external_id": f"{kc}/brc-CT_kc/SJ", "name": "CT_kc:SJ",
         "action": "allow", "sources": [f"epg:{kc}/out-L3O_c/instP-EN_any"],
         "destinations": [f"epg:{kc}/ap-AP_200/epg-EG_app"], "services": ["tcp/443"],
         "aci_scope": "context", "raw": {"tenant": "TE_kc", "dn": f"{kc}/brc-CT_kc"}},
    ]  # fmt: skip
    return aci


def test_contracts_anderer_tenants_und_vrfs_nicht_in_der_doku(client):
    """Dev-Befund 0.42.2: in der Doku eines Projekts (Tenant A, AP_007) standen Contracts
    anderer Tenants, die weder Tenant noch VRF der Applikation betreffen - ueber einen Endpunkt
    mit gleicher IP in einer anderen VRF und ueber eine externe EPG 0.0.0.0/0 einer anderen
    VRF. Die EPGs einer VM stehen fest (Portgruppe); was nur ueber die IP passt, zaehlt nur in
    ihren VRFs, externe EPGs anderer VRFs gar nicht."""
    tid, app = _app(client)
    apic = next(s["id"] for s in client.get("/systems").json() if s["name"] == "apic-01")
    _deliver(client, tid, apic, "cisco_aci", _other_tenants())
    doc = client.get(f"/applications/{app['id']}/doc").json()
    names = {r["name"] for r in doc["rules"]}
    assert "CT_app:SJ" in names
    assert not {"CT_kb:SJ", "CT_kc:SJ"} & names, names
    vms = {s["name"]: s for s in doc["systems"]}
    assert vms["web01-007-ka"]["epgs"] == ["TE_ka/AP_007/EG_web01"]
    # Treffer der ACI-Contracts mit der EPG, ueber die sie gelten (Filter nach EPG)
    (ct,) = (r["id"] for r in doc["rules"] if r["name"] == "CT_app:SJ")
    epgs = {h["epg"] for h in doc["hits"] if h["rule_id"] == ct}
    assert epgs == {"TE_ka/AP_007/EG_web01", "TE_ka/AP_007/EPG_db01"}
    assert all(h["epg"] is None for h in doc["hits"] if h["rule_id"] not in {ct})

    # Doku je System: der Server 10.20.0.5 (EPG ueber die Portgruppe seiner VM) ebenso
    systems = client.get("/appdoc/systems", params={"tenant_id": tid}).json()
    name = next(s["name"] for s in systems if s["ip"] == "10.20.0.5")
    one = client.get(f"/appdoc/systems/{name}", params={"tenant_id": tid}).json()
    shown = {r["Description"] for t in one["tables"] for r in t["rows"]}
    assert "CT_app:SJ" in shown and not {"CT_kb:SJ", "CT_kc:SJ"} & shown, shown


def _befund_043() -> tuple[dict, dict, dict]:
    """Dev-Befund 0.43.0 (Projekt TE_ka/AP_007, anonymisiert):

    - Contracts aus `common` mit Scope VRF bzw. AP: Consumer anderer Tenants/APs sind keine
      Gegenstelle;
    - EG_web01 teilt ihren BD mit EG_other (AP_008): Regeln zu einem Host von EG_other gehoeren
      nicht zu EG_web01, Regeln ueber das ganze BD-Subnetz schon;
    - die IP von web01 ist auch als Endpunkt in EG_other gelernt (alt) - die Portgruppe gilt;
    - VM app01-000-ka an einer gleichnamigen Portgruppe mit anderem VLAN; VM app02-008-ka an
      der Portgruppe von EG_web01 (Name nennt ein anderes Projekt)."""
    ka, cm = "uni/tn-TE_ka", "uni/tn-common"
    aci = copy.deepcopy(ACI)
    web = next(e for e in aci["epgs"] if e["name"] == "EG_web01")
    web.pop("subnets")
    web |= {"bd": "BD_web", "bd_subnets": ["10.20.0.1/24"]}
    other, mon = f"{ka}/ap-AP_008/epg-EG_other", f"{ka}/ap-AP_008/epg-EG_mon"
    aci["epgs"] += [
        {"name": "EG_other", "dn": other, "tenant": "TE_ka", "ap": "AP_008", "vrf": "VRF1",
         "bd": "BD_web", "bd_subnets": ["10.20.0.1/24"],
         "endpoints": [{"ip": "10.20.0.77"}, {"ip": "10.20.0.5"}]},
        {"name": "EG_mon", "dn": mon, "tenant": "TE_ka", "ap": "AP_008", "vrf": "VRF1"},
        {"name": "EG_x", "dn": "uni/tn-TE_kb/ap-AP_100/epg-EG_x", "tenant": "TE_kb",
         "ap": "AP_100", "vrf": "VRF1"},
    ]  # fmt: skip

    def ct(name, scope, consumers, provider, port):
        return {"source_platform": "cisco_aci", "external_id": f"{cm}/brc-{name}/SJ",
                "name": f"{name}:SJ", "action": "allow", "aci_scope": scope,
                "sources": [f"epg:{c}" for c in consumers], "destinations": [f"epg:{provider}"],
                "services": [f"tcp/{port}"],
                "raw": {"tenant": "common", "dn": f"{cm}/brc-{name}"}}  # fmt: skip

    aci["rules"] += [
        ct("CT_common", "context", [DB_DN, "uni/tn-TE_kb/ap-AP_100/epg-EG_x"], WEB_DN, 8080),
        ct("CT_ap", "application-profile", [mon], WEB_DN, 9090),
        ct("CT_other", "context", [mon], other, 7070),
    ]
    fw = {"status": "completed", "rules": [
        {"source_platform": "fortigate", "external_id": "30", "name": "other-in",
         "action": "allow", "sources": ["10.9.0.0/16"], "destinations": ["10.20.0.77"],
         "services": ["tcp/8080"], "family": "v4"},
        {"source_platform": "fortigate", "external_id": "31", "name": "bd-in",
         "action": "allow", "sources": ["10.9.0.0/16"], "destinations": ["10.20.0.0/24"],
         "services": ["tcp/8443"], "family": "v4"},
    ]}  # fmt: skip
    vc = copy.deepcopy(VCENTER)
    vc["virtual"]["vms"] += [
        {"name": "app01-000-ka", "uuid": "u-8", "power_state": "poweredOn",
         "nics": [{"name": "Network adapter 1", "portgroup": "PG_backup", "switch": "vSwitch1",
                   "vlan": 951, "connected": True, "ips": ["10.95.1.4"]}]},
        {"name": "app02-008-ka", "uuid": "u-9", "power_state": "poweredOn",
         "nics": [{"name": "Network adapter 1", "portgroup": "TE_ka|AP_007|EG_web01",
                   "switch": "DVS-ACI-A", "vlan": 1107, "connected": True,
                   "ips": ["10.20.0.9"]}]},
    ]  # fmt: skip
    return aci, fw, vc


def test_befund_contract_scope_bd_und_vm_zuordnung(client, session_factory):
    tid, app = _app(client)
    aci, fw, vc = _befund_043()
    ids = {s["name"]: s["id"] for s in client.get("/systems").json()}
    _deliver(client, tid, ids["apic-01"], "cisco_aci", aci)
    _deliver(client, tid, ids["fw-01"], "fortigate", fw)
    _deliver(client, tid, ids["vc-01"], "vmware", vc)
    doc = client.get(f"/applications/{app['id']}/doc").json()
    rules = {r["id"]: r["name"] for r in doc["rules"]}
    names = set(rules.values())

    # 1. Scope: CT_common nur mit der Gegenstelle derselben VRF, CT_ap (anderes AP) gar nicht
    assert "CT_common:SJ" in names and "CT_ap:SJ" not in names, names
    counter = {h["counterpart"] for h in doc["hits"] if rules.get(h["rule_id"]) == "CT_common:SJ"}
    assert counter and all("TE_kb" not in c for c in counter), counter
    # Beteiligte je Treffer (0.43.2): eigener Endpunkt und Gegenstellen im Scope - die Doku
    # zeigt im Contract nur diese, nicht alle Consumer
    parties = [h["parties"] for h in doc["hits"] if rules.get(h["rule_id"]) == "CT_common:SJ"]
    assert {dn for p in parties for dn in p["own"]} == {WEB_DN, DB_DN}
    assert {dn for p in parties for dn in p["counter"]} == {WEB_DN, DB_DN}
    assert all("TE_kb" not in dn for p in parties for dn in p["counter"])
    fw_hits = [h for h in doc["hits"] if rules.get(h["rule_id"]) == "bd-in"]
    assert fw_hits and all(h["parties"] is None for h in fw_hits)
    # 2. gemeinsamer BD: Host von EG_other nicht, das ganze BD-Subnetz schon
    assert "other-in" not in names and "bd-in" in names
    # 3. gelernter Endpunkt von web01 in EG_other: die Portgruppe gilt - kein CT_other
    assert "CT_other:SJ" not in names
    # 4. VMs: gleichnamige Portgruppe mit anderem VLAN gehoert nicht dazu; abweichendes Projekt
    #    im Namen als Hinweis, je NIC die EPG mit der Art der Zuordnung
    vms = {s["name"]: s for s in doc["systems"]}
    assert "app01-000-ka" not in vms
    app2 = vms["app02-008-ka"]
    assert app2["hints"] == [
        "Name nennt Projekt 008, die Portgruppe TE_ka|AP_007|EG_web01 gehört zur EPG "
        "TE_ka/AP_007/EG_web01 (Projekt 007)"
    ]
    (nic,) = app2["nics"]
    assert (nic["epg"], nic["match"], nic["vlan"]) == ("TE_ka/AP_007/EG_web01", "name", 1107)
    assert vms["web01-007-ka"]["hints"] == []
    # Liste der VMs: die NIC an der fremden Portgruppe ohne EPG
    rows = {r["name"]: r for r in client.get("/vms", params={"tenant_id": tid}).json()["rows"]}
    assert rows["app01-000-ka"]["nics"][0]["epg"] is None

    # Weg-Suche (aci_check) nutzt dieselbe Pruefung des Scopes
    with session_factory() as session:
        by_name = {e.name: e for e in session.scalars(select(Epg).where(Epg.tenant_id == tid))}
        cls = aci_relevance.Classifier(session, tid)
        assert cls.scope_allows("context", by_name["EG_web01"], by_name["EPG_db01"])
        assert not cls.scope_allows("context", by_name["EG_web01"], by_name["EG_x"])
        assert not cls.scope_allows("application-profile", by_name["EG_web01"], by_name["EG_mon"])
        assert not cls.scope_allows("tenant", by_name["EG_web01"], by_name["EG_x"])
        assert cls.scope_allows("global", by_name["EG_web01"], by_name["EG_x"])


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


def test_kategorien_paar_ipv4_ipv6_als_eine_zeile():
    """Dev-Befund 0.33.0: gepaarte IPv4/IPv6-Regeln (gleich bzw. bestaetigt) stehen in den
    Kategorien als eine Zeile mit beiden Regeln; ungepaarte bleiben getrennt."""
    from app.store import appdoc
    from app.store.applications import _hits_and_tables

    def row(rid, name, system, obj="H_web01"):
        return {"Server Name": system, "TCP/UDP": "tcp/443", "Target Systems": "any",
                "Aktion": "Allow", "Schutzmechanismen": "", "Gerät": "fw-01",
                "Description": name, appdoc.RULE: rid, appdoc.DIRECTION: "in",
                appdoc.SIDE: "dst", appdoc.OBJECT: obj, appdoc.SCOPE: "specific"}  # fmt: skip

    rows = [
        ("vm:1", 10, row(1, "web-in", "web01")),
        ("vm:1", 10, row(2, "web-in6", "web01")),  # IPv6-Gegenstueck von 1
        ("vm:1", 10, row(3, "db-in", "web01")),
    ]
    hits, tables = _hits_and_tables(rows, {1: 1, 2: 1})
    t10 = next(t for t in tables if t["index"] == 10)["rows"]
    by_desc = {r["Description"]: r for r in t10}
    assert set(by_desc) == {"web-in / web-in6 (IPv4 + IPv6)", "db-in"}
    assert by_desc["web-in / web-in6 (IPv4 + IPv6)"]["_rules"] == [1, 2]
    assert by_desc["db-in"]["_rules"] == [3]
    assert {h["rule_id"] for h in hits} == {1, 2, 3}  # Treffer bleiben je Regel
    _, separate = _hits_and_tables(rows)
    assert len(next(t for t in separate if t["index"] == 10)["rows"]) == 3
