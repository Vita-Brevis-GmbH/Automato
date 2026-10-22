"""Relevanz in der Doku (0.24.0, ADR 0019 Punkt 6): Regeln, die einen Server nach dem Routing
ihrer Firewall gar nicht sehen koennen, stehen nicht als Zugriff in der Doku.

Firewall aus `test_policy_check` (LAN, DMZ, Internet): Der Webserver steht in der DMZ. Die Regel
„lan -> internet, alles“ erfasst ihn ueber `any`, die Firewall leitet ihn aber ueber die DMZ -
routingtechnisch nicht moeglich. Die Regeln „lan -> dmz“ sind moeglich.
"""

import test_policy_check as pc

ADMIN = {"X-Dev-Roles": "admin"}
OP = {"X-Dev-Roles": "operator"}


def test_regeln_nur_wo_der_verkehr_durchkommt(client):
    tid = pc._seed(client)
    systems = client.get("/appdoc/systems", params={"tenant_id": tid}).json()
    web = next(s["name"] for s in systems if s["ip"] == "10.30.0.10")

    doc = client.get(f"/appdoc/systems/{web}", params={"tenant_id": tid}).json()
    shown = {(row["_rule"], row["_side"]) for t in doc["tables"] for row in t["rows"]}
    gone = {(x["description"], x["side"]): x for x in doc["impossible"]}
    assert set(gone) == {
        ("lan-to-dmz-deny", "src"),  # vom Server aus: sein Verkehr kommt über dmz, nicht lan
        ("lan-to-inet-users", "dst"),
        ("lan-to-inet-users", "src"),
    }
    inet = gone[("lan-to-inet-users", "dst")]
    assert inet["text"] == (
        "fw-r leitet 10.30.0.10 über dmz (Zone dmz) – die Regel gilt nur für den Ausgang internet"
    )
    assert not {(x["rule_id"], x["side"]) for x in doc["impossible"]} & shown
    rows = [row for t in doc["tables"] for row in t["rows"]]
    assert {(r["Description"], r["_side"]) for r in rows} >= {
        ("lan-to-dmz-web", "dst"),
        ("lan-to-dmz-deny", "dst"),
    }
    gone = inet

    # Applikations-Doku: Treffer mit Relevanz; Kennzahlen ohne die unmoegliche Regel
    body = {"name": "Web", "members": [{"kind": "host", "ref": web}]}
    app = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP)
    assert app.status_code == 201, app.text
    out = client.get(f"/applications/{app.json()['id']}/doc", params={"tenant_id": tid}).json()
    rel = {(h["rule_id"], h["side"]): h["relevance"] for h in out["hits"]}
    assert rel[(gone["rule_id"], "dst")] == "impossible"
    assert rel[(gone["rule_id"], "src")] == "impossible"  # Verkehr vom Server kommt über dmz
    texts = [h["relevance_text"] for h in out["hits"] if h["relevance"] == "impossible"]
    assert any("kommt bei fw-r über dmz (Zone dmz) an" in t for t in texts)
    assert out["summary"]["impossible"] == 1
    assert out["summary"]["rules"] == len({h["rule_id"] for h in out["hits"]}) - 1
    exported = {r.get("_rule") for t in out["tables"] for r in t["rows"]}
    assert gone["rule_id"] not in exported
