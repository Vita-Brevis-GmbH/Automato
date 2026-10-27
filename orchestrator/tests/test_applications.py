"""Applikationen (ADR 0012): Mitglieder AP/EPG/Server, Vorschläge per Projektnummer, Doku je
Applikation, Rechte; dazu die physische Lage einer EPG (Leaf-Port, VLAN)."""

from app.models import (
    Application,
    DictionaryEntry,
    EndpointKind,
    EndpointRole,
    NamingProfile,
    NetworkObject,
    ObjectClass,
    Rule,
    RuleAction,
    RuleDomain,
    RuleEndpoint,
    Tenant,
)
from app.store.applications import project_number
from sqlalchemy import select
from test_reference_sources import ADMIN, TOKEN, _system, _tenant

WEB = "uni/tn-TE_a/ap-AP_002/epg-EG_dmz01"
DB = "uni/tn-TE_a/ap-AP_002/epg-EG_internal01"
OTHER = "uni/tn-TE_a/ap-AP_003/epg-EG_dmz01"
OP = {"X-Dev-Roles": "operator"}

ACI_RESULT = {
    "status": "completed",
    "epgs": [
        {"name": "EG_dmz01", "dn": WEB, "tenant": "TE_a", "ap": "AP_002", "bd": "BD_dmz01"},
        {"name": "EG_internal01", "dn": DB, "tenant": "TE_a", "ap": "AP_002"},
        {"name": "EG_dmz01", "dn": OTHER, "tenant": "TE_a", "ap": "AP_003"},
    ],
    "rules": [
        {
            "source_platform": "cisco_aci",
            "external_id": "uni/tn-TE_a/brc-CT_db/SJ_sql",
            "name": "CT_db:SJ_sql",
            "action": "allow",
            "sources": [f"epg:{WEB}"],
            "destinations": [f"epg:{DB}"],
            "services": ["tcp/1433"],
            "aci_scope": "application-profile",
        }
    ],
    "inventory": {
        "interfaces": [
            {
                "device": "leaf101",
                "name": "eth1/10",
                "mode": "tagged",
                "tagged_vlans": [1002],
                "neighbor": "esx01",
                "neighbor_port": "vmnic2",
                "epgs": ["TE_a/AP_002/EG_dmz01"],
            },
            {"device": "leaf102", "name": "eth1/10", "epgs": ["TE_a/AP_003/EG_dmz01"]},
        ],
        "vlans": [{"vid": 1002, "name": "EG_dmz01", "epgs": ["TE_a/AP_002/EG_dmz01"]}],
    },
}


def _seed(client, session_factory) -> tuple[int, int]:
    tid = _tenant(client)
    apic = _system(client, tid, name="apic", platform="cisco_aci", base_url="https://apic")
    body = {"platform": "cisco_aci", "tenant_id": tid, "source_system_id": apic["id"]}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=ACI_RESULT)
    assert r.json()["status"] == "completed", r.text

    fw = _system(client, tid, name="fw", platform="fortigate", base_url="https://fw")
    s = session_factory()
    app01 = NetworkObject(
        tenant_id=tid,
        name="H_app01-002-test1.example.ch",
        object_class=ObjectClass.HOST,
        value="10.0.2.5",
    )
    db01 = NetworkObject(
        tenant_id=tid,
        name="H_dbs01-003-test1.example.ch",
        object_class=ObjectClass.HOST,
        value="10.0.3.5",
    )
    ext = NetworkObject(
        tenant_id=tid,
        name="H-EXT_partner-000-wan01_203.0.113.9",
        object_class=ObjectClass.HOST,
        value="203.0.113.9",
    )
    s.add_all([app01, db01, ext])
    s.flush()
    rule = Rule(
        tenant_id=tid,
        source_system_id=fw["id"],
        domain=RuleDomain.FIREWALL,
        external_id="7",
        name="app01-db01",
        action=RuleAction.ALLOW,
    )
    rule.endpoints += [
        RuleEndpoint(
            role=EndpointRole.SRC,
            kind=EndpointKind.HOST,
            network_object_id=app01.id,
            matched=True,
        ),
        RuleEndpoint(
            role=EndpointRole.DST,
            kind=EndpointKind.HOST,
            network_object_id=db01.id,
            matched=True,
        ),
    ]
    s.add(rule)
    s.commit()
    return tid, apic["id"]


def test_projektnummer_aus_dem_namen():
    assert project_number("ap", "AP_002") == "002"
    assert project_number("ap", "AP_002_webshop") == "002"
    assert project_number("ap", "AP_0021") is None
    assert project_number("ap", "AP_000") is None  # Standort/extern
    assert project_number("ap", "AP_infra") is None
    assert project_number("host", "H_app01-002-test1.example.ch") == "002"
    assert project_number("host", "app01-123-tonch.example.ch") == "123"
    assert project_number("host", "H-EXT_servicenow-000-wan01_185.161.103.34") is None
    assert project_number("epg", "EG_dmz01") is None


def test_vorschlag_uebernehmen_und_ergaenzen(client, session_factory):
    tid, _ = _seed(client, session_factory)
    sug = client.get("/applications/suggestions", params={"tenant_id": tid}).json()
    assert [(s["code"], s["name"], s["application_id"]) for s in sug] == [
        ("002", "Projekt 002", None),
        ("003", "Projekt 003", None),
    ]
    assert {(m["kind"], m["ref"]) for m in sug[0]["members"]} == {
        ("ap", "TE_a/AP_002"),
        ("host", "H_app01-002-test1.example.ch"),
    }

    r = client.post(f"/applications/suggestions/002/accept?tenant_id={tid}", headers=OP)
    assert r.status_code == 200, r.text
    app = r.json()
    assert (app["name"], app["code"], app["member_count"]) == ("Projekt 002", "002", 2)
    # AP_002 umfasst beide EPGs, dazu der Server -> 3 Systeme
    assert app["system_count"] == 3
    ap = next(m for m in app["members"] if m["kind"] == "ap")
    assert ap["origin"] == "suggestion" and ap["exists"]
    assert ap["systems"] == ["TE_a/AP_002/EG_dmz01", "TE_a/AP_002/EG_internal01"]

    left = client.get("/applications/suggestions", params={"tenant_id": tid}).json()
    assert [s["code"] for s in left] == ["003"]

    # neuer Server desselben Projekts -> Vorschlag ergänzt die bestehende Applikation
    s = session_factory()
    s.add(
        NetworkObject(
            tenant_id=tid,
            name="H_app02-002-test1.example.ch",
            object_class=ObjectClass.HOST,
            value="10.0.2.6",
        )
    )
    s.commit()
    (again, _) = client.get("/applications/suggestions", params={"tenant_id": tid}).json()
    assert again["code"] == "002" and again["application_id"] == app["id"]
    r = client.post(f"/applications/suggestions/002/accept?tenant_id={tid}", headers=OP)
    assert r.json()["id"] == app["id"] and r.json()["member_count"] == 3
    assert client.post(f"/applications/suggestions/999/accept?tenant_id={tid}").status_code == 404


def test_vorschlaege_je_tenant_getrennt(client, session_factory):
    """0.74.1 (Anwender): AP_002 in zwei Tenants hat ausser der Nummer nichts gemein - je
    Tenant ein Vorschlag; Server ohne Tenant bei mehreren Tenants eine eigene Gruppe."""
    tid, _ = _seed(client, session_factory)
    other = {**ACI_RESULT, "epgs": [*ACI_RESULT["epgs"],
                                    {"name": "EG_x", "dn": "uni/tn-TE_b/ap-AP_002/epg-EG_x",
                                     "tenant": "TE_b", "ap": "AP_002"}]}  # fmt: skip
    apic = next(x for x in client.get("/systems", params={"tenant_id": tid}).json()
                if x["platform"] == "cisco_aci")  # fmt: skip
    body = {"platform": "cisco_aci", "tenant_id": tid, "source_system_id": apic["id"]}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=other)
    assert r.json()["status"] == "completed", r.text
    sug = client.get("/applications/suggestions", params={"tenant_id": tid}).json()
    got = {s["key"]: (s["name"], sorted(m["ref"] for m in s["members"])) for s in sug}
    assert got == {
        "002": ("Projekt 002", ["H_app01-002-test1.example.ch"]),  # Server: kein Tenant
        "TE_a|002": ("Projekt 002 · TE_a", ["TE_a/AP_002"]),
        "TE_b|002": ("Projekt 002 · TE_b", ["TE_b/AP_002"]),
        "TE_a|003": ("Projekt 003", ["H_dbs01-003-test1.example.ch", "TE_a/AP_003"]),
    }
    # die Nummer allein ist hier mehrdeutig; je Schluessel eine eigene Applikation
    assert (
        client.post(f"/applications/suggestions/002/accept?tenant_id={tid}", headers=OP).status_code
        == 200
    )  # fmt: skip (nur die Server-Gruppe)
    r = client.post(f"/applications/suggestions/TE_b%7C002/accept?tenant_id={tid}", headers=OP)
    assert r.status_code == 200, r.text
    assert (r.json()["name"], r.json()["code"]) == ("Projekt 002 · TE_b", "002")
    left = client.get("/applications/suggestions", params={"tenant_id": tid}).json()
    assert [(s["key"], s["name"]) for s in left] == [("TE_a|002", "Projekt 002 · TE_a"),
                                                     ("TE_a|003", "Projekt 003")]  # fmt: skip
    # der Vorschlag ergaenzt nur die Applikation seines Tenants
    assert all(s["application_id"] is None for s in left)
    # Nummer allein eindeutig (nur ein Tenant): wie bisher
    r = client.post(f"/applications/suggestions/003/accept?tenant_id={tid}", headers=OP)
    assert r.status_code == 200 and r.json()["name"] == "Projekt 003"


def test_vorschlag_name_aus_woerterbuch(client, session_factory):
    tid, _ = _seed(client, session_factory)
    s = session_factory()
    profile = NamingProfile(customer="kunde", name="eigen")
    s.add(profile)
    s.flush()
    s.add(DictionaryEntry(profile_id=profile.id, kind="project", key="003", value="Webshop"))
    s.get(Tenant, tid).naming_profile_id = profile.id
    s.commit()
    sug = client.get("/applications/suggestions", params={"tenant_id": tid}).json()
    assert {x["code"]: x["name"] for x in sug} == {"002": "Projekt 002", "003": "Webshop"}


def test_applikation_manuell_pflegen_und_doku(client, session_factory):
    tid, _ = _seed(client, session_factory)
    site = client.post(
        f"/tenants/{tid}/sites", json={"code": "001", "name": "RZ1"}, headers=ADMIN
    ).json()
    body = {
        "name": "Webshop",
        "code": "002",
        "owner": "Team Shop",
        "criticality": "high",
        "site_id": site["id"],
        "members": [
            {"kind": "epg", "ref": WEB},
            {"kind": "host", "ref": "H_app01-002-test1.example.ch"},
        ],
    }
    r = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP)
    assert r.status_code == 201, r.text
    app = r.json()
    assert app["site"]["code"] == "001" and app["criticality"] == "high"
    assert [m["label"] for m in app["members"]] == [
        "TE_a/AP_002/EG_dmz01",
        "H_app01-002-test1.example.ch",
    ]

    assert client.post(f"/applications?tenant_id={tid}", json=body, headers=OP).status_code == 409
    bad = {"name": "X", "members": [{"kind": "host", "ref": "H_gibts-nicht"}]}
    assert client.post(f"/applications?tenant_id={tid}", json=bad, headers=OP).status_code == 422
    assert not session_factory().scalars(select(Application).where(Application.name == "X")).all()
    assert client.post("/applications", json={"name": "Y"}, headers=OP).status_code == 422

    doc = client.get(f"/applications/{app['id']}/doc").json()
    assert doc["application"]["name"] == "Webshop"
    # Server ohne VM als System, die EPG im eigenen Abschnitt (0.14.0)
    assert [(x["name"], x["kind"]) for x in doc["systems"]] == [
        ("H_app01-002-test1.example.ch", "host")
    ]
    assert [(e["label"], e["member"]) for e in doc["epgs"]] == [
        ("TE_a/AP_002/EG_dmz01", "EPG TE_a/AP_002/EG_dmz01")
    ]
    rows = {t["index"]: t["rows"] for t in doc["tables"]}
    assert [r["Description"] for r in rows[7]] == ["CT_db:SJ_sql"]  # innerhalb des AP
    # Firewall-Regel des Servers zu 10.0.3.5 (ausgehend, ohne Zone): seit 0.78.2 nicht in
    # Kategorie 10 - nur Zugriffe auf die Applikation; als Treffer bleibt sie
    assert rows[10] == []
    assert any(h["counterpart"] == "10.0.3.5" and h["table"] == 10 for h in doc["hits"])
    assert rows[7][0]["Target Systems"] == "TE_a/AP_002/EG_internal01"  # ganzer ACI-Pfad
    assert doc["summary"]["specific"] == 2 and doc["summary"]["unspecific"] == 0

    aid = app["id"]
    r = client.patch(f"/applications/{aid}", json={"owner": None, "criticality": "low"}, headers=OP)
    assert r.json()["owner"] is None and r.json()["criticality"] == "low"
    member = r.json()["members"][0]["id"]
    r = client.delete(f"/applications/{aid}/members/{member}", headers=OP)
    assert r.json()["member_count"] == 1
    r = client.post(
        f"/applications/{aid}/members",
        json={"members": [{"kind": "ap", "ref": "TE_a/AP_003"}]},
        headers=OP,
    )
    assert r.json()["member_count"] == 2

    # Standort gelöscht -> Applikation bleibt ohne Standort
    client.delete(f"/tenants/{tid}/sites/{site['id']}", headers=ADMIN)
    assert client.get(f"/applications/{aid}").json()["site"] is None

    assert client.delete(f"/applications/{aid}", headers=OP).status_code == 204
    assert client.get(f"/applications/{aid}").status_code == 404


def test_rechte_und_mandanten(client, session_factory):
    tid, _ = _seed(client, session_factory)
    other = client.post("/tenants", json={"name": "kunde-b"}, headers=ADMIN).json()["id"]
    app = client.post(f"/applications?tenant_id={tid}", json={"name": "Webshop"}, headers=OP).json()
    viewer = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid)}
    foreign = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(other)}
    assert client.get(f"/applications/{app['id']}", headers=viewer).status_code == 200
    assert client.patch(f"/applications/{app['id']}", json={}, headers=viewer).status_code == 403
    assert client.get(f"/applications/{app['id']}", headers=foreign).status_code == 403
    assert client.delete(f"/applications/{app['id']}", headers=foreign).status_code == 403
    assert client.get("/applications", headers=foreign).json() == []

    cands = client.get("/applications/candidates", params={"tenant_id": tid, "kind": "ap"}).json()
    assert [c["ref"] for c in cands] == ["TE_a/AP_002", "TE_a/AP_003"]
    hit = client.get("/applications/candidates", params={"tenant_id": tid, "q": "10.0.3"})
    assert [c["ref"] for c in hit.json()] == ["H_dbs01-003-test1.example.ch"]
    assert hit.headers["X-Total-Count"] == "1"

    # Mandant löschen nimmt die Applikationen mit
    r = client.delete(f"/tenants/{tid}?confirm=kunde-a", headers=ADMIN)
    assert r.status_code == 200 and r.json()["applications"] == 1


def test_alle_kandidaten_zuordenbar(client, session_factory):
    """0.74.1 (Anwender: „nach Hinzufügen bleibt die Liste Mitglieder leer“): jeder Kandidat
    der Auswahl laesst sich zuordnen und steht danach sofort in den Mitgliedern."""
    tid, _ = _seed(client, session_factory)
    app = client.post(f"/applications?tenant_id={tid}", json={"name": "Alle"}, headers=OP).json()
    cands = client.get("/applications/candidates", params={"tenant_id": tid}).json()
    assert {c["kind"] for c in cands} == {"ap", "epg", "host"}
    body = {"members": [{"kind": c["kind"], "ref": c["ref"]} for c in cands]}
    r = client.post(f"/applications/{app['id']}/members", json=body, headers=OP)
    assert r.status_code == 200, r.text
    got = {(m["kind"], m["ref"]) for m in r.json()["members"]}
    assert got == {(c["kind"], c["ref"]) for c in cands}
    again = client.get(f"/applications/{app['id']}").json()
    assert len(again["members"]) == len(cands) and all(m["exists"] for m in again["members"])


def test_epg_lage_mit_leaf_port_und_vlan(client, session_factory):
    tid, _ = _seed(client, session_factory)
    (row,) = client.get("/rules", params={"tenant_id": tid, "domain": "aci_contract"}).json()
    detail = client.get(f"/rules/{row['id']}").json()
    web = next(e["epg"] for e in detail["endpoints"] if e["role"] == "src")
    assert web["vlans"] == [1002] and web["port_count"] == 1
    (port,) = web["ports"]
    assert (port["device"], port["name"], port["neighbor"]) == ("leaf101", "eth1/10", "esx01")
    db = next(e["epg"] for e in detail["endpoints"] if e["role"] == "dst")
    assert db["ports"] == [] and db["vlans"] == []


def test_nachziehen_im_hintergrund_zusammengefasst(session_factory):
    """0.74.1: Befunde nach dem Speichern hinter der Antwort; eine noch nicht begonnene
    Aktualisierung derselben Applikation wird nicht doppelt vorgemerkt."""
    from app.store import app_refresh
    from fastapi import BackgroundTasks

    tasks = BackgroundTasks()
    assert app_refresh.schedule(tasks, session_factory, 1, 7) is True
    assert app_refresh.schedule(tasks, session_factory, 1, 7) is False
    assert app_refresh.schedule(tasks, session_factory, 1, 8) is True
    assert len(tasks.tasks) == 2 and app_refresh.pending(1, 7)
    for t in tasks.tasks:
        t.func(*t.args, **t.kwargs)  # ohne Applikationen: nichts zu tun, kein Fehler
    assert not app_refresh.pending(1, 7)
    assert app_refresh.schedule(tasks, session_factory, 1, 7) is True  # danach wieder
    app_refresh.run(session_factory, 1, 7)
