"""Regeln je Kategorie der Applikations-Doku ausblenden (0.69.0, ADR 0012 Ergaenzung 0.69.0):
mit Pflicht-Begruendung, je Kategorie bzw. in allen, im Audit, haelt ueber neue Laeufe, wieder
einblenden; Rechte (Operator, Kunden-Benutzer nicht).

Umgebung aus `test_app_rules`: Web (EPG_web) mit CT_web/CT_app in Kategorie 9 und
web-to-backend, kb-out, any-any in Kategorie 10.
"""

from app.models import AuditLog
from test_app_rules import _app, _doc, _rule
from test_customer_users import FWU
from test_customers import OP, WEB, _deliver, _seed, _system

VIEW = {"X-Dev-Roles": "viewer"}


def _table(doc, index):
    return next(t for t in doc["tables"] if t["index"] == index)


def test_regel_je_kategorie_ausblenden(client, session_factory):
    tid, _ids = _seed(client)
    fwu = _system(client, tid, "fw-u", "fortigate")
    _deliver(client, tid, fwu, "fortigate", FWU)
    web = _app(client, tid, "Web", [{"kind": "epg", "ref": WEB}])
    url = f"/applications/{web['id']}/hidden"
    anyany = _rule(client, tid, "any-any")

    # Begruendung ist Pflicht; nur Operator
    bad = {"rule_ids": [anyany["id"]], "category": 10, "reason": "  "}
    assert client.post(url, json=bad, headers=OP).status_code == 422
    body = {"rule_ids": [anyany["id"]], "category": 10,
            "reason": "Grundregel des Rechenzentrums, nicht applikationsspezifisch"}  # fmt: skip
    assert client.post(url, json=body, headers=VIEW).status_code == 403
    r = client.post(url, json=body, headers=OP)
    assert r.status_code == 201, r.text
    (hide,) = r.json()
    assert hide["category"] == 10 and hide["by"] == "user:dev" and hide["label"].startswith(
        "Policy ")  # fmt: skip

    doc = _doc(client, web["id"])
    cat10 = _table(doc, 10)
    assert "any-any" not in [x.get("Description") for x in cat10["rows"]]
    (row,) = cat10["hidden"]
    assert row["Description"] == "any-any"
    assert row["_hides"][0]["reason"].startswith("Grundregel")
    assert doc["summary"]["hidden"] == 1
    # die Regel bleibt sonst in der Doku (Liste, Kennzahlen)
    assert "any-any" in {x["name"] for x in doc["rules"]}
    assert [h["active"] for h in doc["hides"]] == [True]

    # in allen Kategorien: CT_web (Kategorie 9)
    ct = _rule(client, tid, "CT_web")
    r = client.post(url, json={"rule_ids": [ct["id"]], "reason": "Test-Contract, wird entfernt"},
                    headers=OP)  # fmt: skip
    assert r.status_code == 201 and r.json()[0]["category"] is None
    doc = _doc(client, web["id"])
    assert [x["Description"] for x in _table(doc, 9)["hidden"]] == ["CT_web"]
    assert doc["summary"]["hidden"] == 2
    # gleiche Regel und Kategorie noch einmal: neue Begruendung statt zweitem Eintrag
    again = dict(body, reason="Grundregel, siehe Betriebshandbuch")
    assert client.post(url, json=again, headers=OP).status_code == 201
    doc = _doc(client, web["id"])
    assert len(doc["hides"]) == 2
    assert _table(doc, 10)["hidden"][0]["_hides"][0]["reason"] == again["reason"]

    # neuer Lauf: bleibt ausgeblendet (stabiler Verweis)
    _deliver(client, tid, fwu, "fortigate", FWU)
    doc = _doc(client, web["id"])
    assert [x["Description"] for x in _table(doc, 10)["hidden"]] == ["any-any"]

    # wieder einblenden
    hid = _table(doc, 10)["hidden"][0]["_hides"][0]["id"]
    assert client.delete(f"{url}/{hid}", headers=VIEW).status_code == 403
    assert client.delete(f"{url}/{hid}", headers=OP).status_code == 204
    assert client.delete(f"{url}/{hid}", headers=OP).status_code == 404
    doc = _doc(client, web["id"])
    assert "any-any" in [x.get("Description") for x in _table(doc, 10)["rows"]]
    assert doc["summary"]["hidden"] == 1

    # Audit mit Begruendung
    s = session_factory()
    rows = s.query(AuditLog).filter(AuditLog.action.in_(
        ["application.rule_hidden", "application.rule_shown"])).all()  # fmt: skip
    actions = [a.action for a in rows]
    assert actions.count("application.rule_hidden") == 3 and "application.rule_shown" in actions
    shown = next(a for a in rows if a.action == "application.rule_shown")
    assert shown.detail["reason"] == again["reason"] and shown.detail["category"] == 10
    s.close()

    # fremde Regel bzw. Kunden-Benutzer
    other = client.post(url, json={"rule_ids": [999999], "reason": "gibt es nicht"}, headers=OP)
    assert other.status_code == 404
    cust = {"X-Dev-Roles": "operator", "X-Dev-Customers": "1"}
    assert client.post(url, json=body, headers=cust).status_code == 403
    # Applikation geloescht: Eintraege weg
    assert client.delete(f"/applications/{web['id']}", headers=OP).status_code == 204
