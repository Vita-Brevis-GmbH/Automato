"""Relevanz in der Doku (0.24.0, ADR 0019 Punkt 6): Regeln, die einen Server nach dem Routing
ihrer Firewall gar nicht sehen koennen, stehen nicht als Zugriff in der Doku.

Firewall aus `test_policy_check` (LAN, DMZ, Internet): Der Webserver steht in der DMZ. Die Regel
„lan -> internet, alles“ erfasst ihn ueber `any`, die Firewall leitet ihn aber ueber die DMZ -
routingtechnisch nicht moeglich. Die Regeln „lan -> dmz“ sind moeglich.
"""

import test_policy_check as pc
from system_doc import system_doc, system_list

ADMIN = {"X-Dev-Roles": "admin"}
OP = {"X-Dev-Roles": "operator"}


def test_regeln_nur_wo_der_verkehr_durchkommt(client):
    tid = pc._seed(client)
    systems = system_list(client, tid)
    web = next(s["name"] for s in systems if s["ip"] == "10.30.0.10")

    doc = system_doc(client, web, tid)
    shown = {(row["_rule"], row["_side"]) for t in doc["tables"] for row in t["rows"]}
    gone = {(x["description"], x["side"]): x for x in doc["impossible"]}
    # ausgehend mit breiter Quelle (lan-to-dmz-deny, lan-to-inet-users vom Server aus) stehen
    # seit 0.103.0 gar nicht in der Doku des Servers - auch nicht als „nicht möglich“
    assert set(gone) == {
        ("lan-to-inet-users", "dst"),
        ("lan-to-dmz-falsch", "dst"),  # DMZ-Netz am Ausgang internet
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

    # Applikations-Doku: unmoegliche Treffer stehen gar nicht drin, sie werden nur gezaehlt
    # (Dev-Befund 0.33.0, nicht mehr einblendbar)
    body = {"name": "Web", "members": [{"kind": "host", "ref": web}]}
    app = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP)
    assert app.status_code == 201, app.text
    out = client.get(f"/applications/{app.json()['id']}/doc", params={"tenant_id": tid}).json()
    assert all(h["relevance"] != "impossible" for h in out["hits"])
    assert gone["rule_id"] not in {h["rule_id"] for h in out["hits"]}
    assert gone["rule_id"] not in {r["id"] for r in out["rules"]}
    assert out["summary"]["impossible"] == 2  # lan-to-inet-users, lan-to-dmz-falsch
    assert out["summary"]["rules"] == len({h["rule_id"] for h in out["hits"]})
    exported = {r.get("_rule") for t in out["tables"] for r in t["rows"]}
    assert gone["rule_id"] not in exported


def test_gegenstelle_any_ist_was_hinter_dem_interface_liegt(client):
    """0.25.0: Statt „any“ nennt die Doku, was hinter Interface bzw. Zone der Regel liegt -
    die Netze dahinter, die Default-Route bzw. eine erfasste Anbindung (Kategorie 2 „Zugriff
    aus dem Internet“ - seit 0.106.0 schon nach dem Namen der Firewall-Zone)."""
    tid = pc._seed(client)

    def rows() -> dict[str, tuple[int, dict]]:
        doc = system_doc(client, "10.30.0.10", tid)
        return {(r["Description"], r["_side"]): (t["index"], r)
                for t in doc["tables"] for r in t["rows"]}  # fmt: skip

    found = rows()
    _, deny = found[("lan-to-dmz-deny", "dst")]
    assert deny["Source"] == "all → Netze hinter lan: 10.10.0.0/24"
    idx, inet = found[("inet-to-web", "dst")]
    # seit 0.106.0 ohne Zone: Zonentyp nach dem Namen der Firewall-Zone („internet“ → Public)
    assert (idx, inet["Source"]) == (2, "all → extern (Default-Route über internet)")

    # Anbindung Internet an der Zone: die Gegenstelle ist das Internet (Kategorie 2)
    nodes = client.get("/routing/summary", params={"tenant_id": tid}).json()["nodes"]
    sid = nodes[0]["source_system_id"]
    exit_ = {"tenant_id": tid, "name": "Internet Provider", "kind": "internet",
             "source_system_ids": [sid], "interfaces": ["internet"]}  # fmt: skip
    assert client.post("/routing/exits", json=exit_, headers=OP).status_code == 201
    idx, inet = rows()[("inet-to-web", "dst")]
    assert (idx, inet["Source"]) == (2, "all → Internet Provider (internet)")


def test_regel_liste_mit_relevanz_fuer_eine_ip(client):
    """0.26.0: Suche nach einer IP - jede Firewall-Regel trägt die Relevanz für sie; mit
    `routable=true` fehlen die routingtechnisch unmöglichen."""
    tid = pc._seed(client)
    params = {"tenant_id": tid, "q": "10.30.0.10"}
    r = client.get("/rules", params=params)
    rel = {x["name"]: x["relevance"] for x in r.json()}  # über Netze bzw. Hosts, ohne any
    assert set(rel) == {"lan-to-dmz-web", "inet-to-web", "lan-to-dmz-falsch"}
    assert r.headers["X-Impossible-Count"] == "1"
    assert rel["lan-to-dmz-falsch"] == {
        "state": "impossible",
        "text": "fw-r leitet 10.30.0.10 über dmz (Zone dmz) – die Regel gilt nur für den Ausgang "
        "internet",
        "sides": {"dst": "impossible"},
    }
    assert rel["inet-to-web"]["state"] == rel["lan-to-dmz-web"]["state"] == "possible"
    hidden = client.get("/rules", params={**params, "routable": True})
    assert hidden.headers["X-Total-Count"] == "2"
    assert "lan-to-dmz-falsch" not in {x["name"] for x in hidden.json()}
    # andere Suche: keine Relevanz
    other = client.get("/rules", params={"tenant_id": tid, "q": "lan"})
    assert "X-Impossible-Count" not in other.headers
    assert all(x["relevance"] is None for x in other.json())
