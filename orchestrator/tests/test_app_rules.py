"""Applikationen ausbauen (0.60.0, ADR 0012): Regeln direkt zuordnen bzw. ausschliessen und
Abhaengigkeiten zwischen Applikationen.

Umgebung wie in `test_customer_users`: APIC mit TE_ka (EPG_web, EPG_db), TE_kb (EPG_app) und
common (EPG_dns, 10.0.53.0/24) mit den Contracts CT_web (EPG_db -> EPG_web), CT_app (EPG_app ->
EPG_web) und CT_dns (EPG_app -> EPG_dns); fw-k mit kb-out (VDOM kunde-b), fw-u mit a-dns (H_a01
-> N_dns 10.0.53.0/24).
"""

from test_customer_users import FWU
from test_customers import APP, DB, DNS, OP, WEB, _deliver, _seed, _system

ADMIN = {"X-Dev-Roles": "admin"}


def _app(client, tid, name, members):
    body = {"name": name, "members": members}
    r = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP)
    assert r.status_code == 201, r.text
    return r.json()


def _rule(client, tid, name):
    rows = client.get("/rules", params={"tenant_id": tid, "limit": 500}).json()
    return next(r for r in rows if r["name"] == name)


def _doc(client, app_id):
    r = client.get(f"/applications/{app_id}/doc")
    assert r.status_code == 200, r.text
    return r.json()


def test_regeln_direkt_zuordnen_und_ausschliessen(client):
    tid, ids = _seed(client)
    fwu = _system(client, tid, "fw-u", "fortigate")
    _deliver(client, tid, fwu, "fortigate", FWU)
    web = _app(client, tid, "Web", [{"kind": "epg", "ref": WEB}])
    names = {r["name"] for r in _doc(client, web["id"])["rules"]}
    assert "b-leer" not in names and "CT_web" in names

    # b-leer (fw-u) hat keinen Treffer bei Web - direkt zuordnen
    leer = _rule(client, tid, "b-leer")
    r = client.post(f"/applications/{web['id']}/rules", json={"rule_id": leer["id"]}, headers=OP)
    assert r.status_code == 200, r.text
    member = next(m for m in r.json()["members"] if m["kind"] == "rule")
    assert member["ref"] == f"{fwu}|root|23" and member["exists"]
    assert member["label"] == "Policy 23 „b-leer“ (fw-u)"
    doc = _doc(client, web["id"])
    assert "b-leer" in {r["name"] for r in doc["rules"]}
    (hit,) = (h for h in doc["hits"] if h["rule_id"] == leer["id"])
    assert (hit["scope"], hit["system"], hit["via"]) == ("assigned", "app", "direkt zugeordnet")
    assert doc["summary"]["assigned"] == 1
    # neuer Lauf: die Zuordnung bleibt (stabiler Verweis statt Regel-ID)
    _deliver(client, tid, fwu, "fortigate", FWU)
    leer2 = _rule(client, tid, "b-leer")
    assert leer2["id"] in {r["id"] for r in _doc(client, web["id"])["rules"]}

    # CT_web ausschliessen: faellt aus der Doku, steht als ausgeschlossen da
    ct = _rule(client, tid, "CT_web")
    r = client.post(f"/applications/{web['id']}/rules", json={"rule_id": ct["id"], "exclude": True},
                    headers=OP)  # fmt: skip
    assert r.status_code == 200, r.text
    doc = _doc(client, web["id"])
    assert "CT_web" not in {r["name"] for r in doc["rules"]}
    assert doc["summary"]["excluded"] == 1
    excluded = next(m for m in doc["rule_members"] if m["kind"] == "norule")
    assert excluded["rule_id"] == ct["id"] and excluded["label"].startswith("Contract CT_web")
    # wieder zuordnen ersetzt den Ausschluss; Mitglied entfernen macht es rueckgaengig
    r = client.post(f"/applications/{web['id']}/rules", json={"rule_id": ct["id"]}, headers=OP)
    kinds = [m["kind"] for m in r.json()["members"] if m["ref"].endswith("SJ_1")]
    assert kinds == ["rule"]
    mid = next(m["id"] for m in r.json()["members"] if m["ref"].endswith("SJ_1"))
    assert client.delete(f"/applications/{web['id']}/members/{mid}", headers=OP).status_code == 200
    assert _doc(client, web["id"])["summary"]["assigned"] == 1  # nur noch b-leer
    # nur Operator; unbekannte Regel 404
    viewer = {"X-Dev-Roles": "viewer"}
    url = f"/applications/{web['id']}/rules"
    assert client.post(url, json={"rule_id": ct["id"]}, headers=viewer).status_code == 403
    assert client.post(url, json={"rule_id": 999999}, headers=OP).status_code == 404


def test_abhaengigkeiten_zwischen_applikationen(client):
    tid, ids = _seed(client)
    fwu = _system(client, tid, "fw-u", "fortigate")
    _deliver(client, tid, fwu, "fortigate", FWU)
    web = _app(client, tid, "Web", [{"kind": "epg", "ref": WEB}])
    db = _app(client, tid, "DB", [{"kind": "epg", "ref": DB}])
    app = _app(client, tid, "App", [{"kind": "epg", "ref": APP}])
    dns = _app(client, tid, "DNS", [{"kind": "epg", "ref": DNS}])
    a = _app(client, tid, "Kunde A", [{"kind": "host", "ref": "10.10.1.5"}])  # H_a01

    def deps(x):
        d = _doc(client, x["id"])["dependencies"]
        return (
            {e["name"]: [r["name"] for r in e["rules"]] for e in d["uses"]},
            {e["name"]: [r["name"] for r in e["rules"]] for e in d["used_by"]},
        )

    # Contracts: Consumer nutzt Provider
    assert deps(web) == ({}, {"App": ["CT_app"], "DB": ["CT_web"]})
    assert deps(app) == ({"DNS": ["CT_dns"], "Web": ["CT_app"]}, {})
    # Firewall-Regel: H_a01 -> N_dns (Netz im Subnetz von EPG_dns) - Kunde A nutzt DNS; der
    # Bezug nennt das Objekt der Gegenseite
    d = _doc(client, a["id"])["dependencies"]
    assert [(e["name"], e["rules"][0]["name"], e["rules"][0]["via"]) for e in d["uses"]] == [
        ("DNS", "a-dns", "N_dns")]  # fmt: skip
    assert d["used_by"] == []
    d = _doc(client, dns["id"])["dependencies"]
    by = {e["name"]: e for e in d["used_by"]}
    assert set(by) == {"App", "Kunde A"}
    assert by["Kunde A"]["rules"][0]["via"] == "H_a01" and by["App"]["rules"][0]["via"] == "EPG"
    assert deps(db) == ({"Web": ["CT_web"]}, {})
