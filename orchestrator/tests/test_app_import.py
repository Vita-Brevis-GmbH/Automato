"""Stammdaten von Applikationen aus CSV (0.75.0) und mehrere Applikationen loeschen; VMs je
Applikation in der Liste."""

from app.models import AuditLog
from sqlalchemy import select
from test_applications import OP, _seed
from test_reference_sources import ADMIN, _tenant

CSV = (
    "﻿Name;Externer Name;Interner Name;Verwendungszweck;Kürzel;Verantwortlich;Kritikalität;"
    "Standort;Beschreibung;Notiz\r\n"
    "Webshop;Webshop Kunden;webshop-prod;Produktion;042;Team Web;hoch;000;Bestellungen;x\r\n"
    "Intranet;;;test;;;;;\r\n"
    ";ohne Namen;;;;;;;\r\n"
    "Webshop;doppelt;;;;;;;\r\n"
    "Archiv;;;irgendwas;;;sehr wichtig;zrh9;\r\n"
    ";;;;;;;;\r\n"
)


def _import(client, tid, text, apply=False):
    r = client.post(
        f"/applications/import?tenant_id={tid}&apply={'true' if apply else 'false'}",
        json={"csv": text},
        headers=OP,
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_vorschau_und_uebernehmen(client, session_factory):
    tid = _tenant(client)
    client.post(f"/tenants/{tid}/sites", json={"code": "000", "name": "Virtuell"}, headers=ADMIN)
    plan = _import(client, tid, CSV)
    assert plan["ignored"] == ["Notiz"] and plan["columns"]["Verwendungszweck"] == "purpose"
    assert plan["counts"] == {"create": 2, "update": 0, "unchanged": 0, "error": 3}
    rows = {r["line"]: r for r in plan["rows"]}
    assert rows[2]["action"] == "create" and rows[2]["changes"]["purpose"] == [None, "prod"]
    assert rows[2]["changes"]["criticality"] == [None, "high"]
    assert rows[2]["changes"]["site"] == [None, "000"]
    assert rows[4]["errors"] == ["Name fehlt"]
    assert rows[5]["errors"] == ["Name kommt in der Datei mehrfach vor"]
    assert len(rows[6]["errors"]) == 3  # Zweck, Kritikalität, Standort unbekannt
    assert client.get(f"/applications?tenant_id={tid}").json() == []  # Vorschau schreibt nicht

    done = _import(client, tid, CSV, apply=True)
    assert done["counts"]["create"] == 2
    apps = {a["name"]: a for a in client.get(f"/applications?tenant_id={tid}").json()}
    assert set(apps) == {"Webshop", "Intranet"}
    web = apps["Webshop"]
    assert (web["external_name"], web["internal_name"], web["purpose"], web["code"]) == (
        "Webshop Kunden", "webshop-prod", "prod", "042",
    )  # fmt: skip
    assert (web["owner"], web["criticality"], web["site"]["code"]) == ("Team Web", "high", "000")
    assert web["member_count"] == 0 and web["vm_count"] == 0

    # erneut: unveraendert; geaendert nur, was ausgefuellt ist (leere Zelle laesst den Wert)
    again = _import(client, tid, CSV)
    assert again["counts"]["unchanged"] == 2 and again["counts"]["create"] == 0
    change = "Name;Verantwortlich;Zweck\nwebshop;Team Shop;\n"
    plan = _import(client, tid, change)
    (row,) = plan["rows"]
    assert row["action"] == "update" and row["changes"] == {"owner": ["Team Web", "Team Shop"]}
    _import(client, tid, change, apply=True)
    web = client.get(f"/applications/{web['id']}").json()
    assert (web["owner"], web["purpose"]) == ("Team Shop", "prod")

    s = session_factory()
    try:
        audits = list(s.scalars(select(AuditLog).where(AuditLog.action == "application.imported")))
        assert len(audits) == 2  # nur beim Uebernehmen
    finally:
        s.close()


def test_kopfzeile_trennzeichen_und_rechte(client):
    tid = _tenant(client)
    plan = _import(client, tid, "name,purpose,criticality\nCRM,dev,critical\n")
    assert plan["counts"]["create"] == 1 and plan["rows"][0]["changes"]["purpose"][1] == "dev"
    tab = _import(client, tid, "Name\tBeschreibung\nKüche\tfür alle\n")
    assert tab["rows"][0]["changes"]["description"] == [None, "für alle"]
    r = client.post(f"/applications/import?tenant_id={tid}", json={"csv": "Zweck\nprod\n"},
                    headers=OP)  # fmt: skip
    assert r.status_code == 422 and "Name" in r.json()["detail"]
    viewer = {"X-Dev-Roles": "viewer"}
    r = client.post(f"/applications/import?tenant_id={tid}", json={"csv": "Name\nA\n"},
                    headers=viewer)  # fmt: skip
    assert r.status_code == 403
    t = client.get("/applications/import/template", headers=OP)
    assert t.status_code == 200 and t.text.lstrip("﻿").startswith("Name;Externer Name;")
    assert _import(client, tid, t.text)["counts"]["create"] == 1


def test_vms_je_applikation_und_mehrere_loeschen(client, session_factory):
    tid, _ = _seed(client, session_factory)
    leer = client.post(f"/applications?tenant_id={tid}", json={"name": "Leer"}, headers=OP).json()
    app = client.post(
        f"/applications?tenant_id={tid}",
        json={"name": "Mit AP", "members": [{"kind": "ap", "ref": "TE_a/AP_002"}]},
        headers=OP,
    ).json()
    rows = {a["name"]: a for a in client.get(f"/applications?tenant_id={tid}").json()}
    assert rows["Leer"]["vm_count"] == 0 and rows["Mit AP"]["system_count"] >= 1
    other = client.post("/tenants", json={"name": "kunde-x"}, headers=ADMIN).json()["id"]
    foreign = client.post(f"/applications?tenant_id={other}", json={"name": "Fremd"},
                          headers=OP).json()  # fmt: skip
    r = client.post(
        f"/applications/delete?tenant_id={tid}",
        json={"ids": [leer["id"], app["id"], foreign["id"]]},
        headers=OP,
    )
    assert r.status_code == 200 and r.json() == {"deleted": 2}  # fremder Mandant bleibt
    assert client.get(f"/applications?tenant_id={tid}").json() == []
    assert client.get(f"/applications/{foreign['id']}").status_code == 200
    viewer = {"X-Dev-Roles": "viewer"}
    r = client.post(f"/applications/delete?tenant_id={tid}", json={"ids": [1]}, headers=viewer)
    assert r.status_code == 403
