"""Regelwerk der Applikations-Doku als Einstellung (0.118.0, ADR 0043; Anwender 2026-10-30:
„Regeln in die Settings übernehmen … aktivieren oder deaktivieren … die Wirkung einstellen … die
beiden Regelsets hinterlegen, damit man umschalten kann“).

Umgebung aus `test_policy_check` (fw-r mit LAN, DMZ, Internet; Webserver 10.30.0.10 in der DMZ):
`lan-to-dmz-falsch` ist routingtechnisch unmoeglich, `block-all` deaktiviert, `lan-to-inet-users`
ausgehend mit breiter Quelle.
"""

import pytest
import test_policy_check as pc
from app import audit
from app.models import AuditLog
from app.store import appdoc_filter
from app.store import appdoc_ruleset as rs
from system_doc import system_list
from test_appdoc_catalog import _default_profile

ADMIN = {"X-Dev-Roles": "admin"}
OP = {"X-Dev-Roles": "operator"}
VIEW = {"X-Dev-Roles": "viewer"}
PATH = {"impossible", "off_path", "off_path_specific", "to_internet"}


def test_eingebaute_profile():
    assert rs.DEFAULT.key == "v1" and [p.key for p in rs.BUILTIN] == ["v1", "v2"]
    for p in rs.BUILTIN:  # alle Bedingungen je einmal, nur erlaubte Wirkungen
        assert sorted(r.key for r in p.rules) == sorted(rs.BY_KEY)
        assert all(r.effect in rs.BY_KEY[r.key].effects for r in p.rules)
    v2 = {r.key: r for r in rs.V2.rules}
    # 1 nicht im Pfad, 2 ueber das Internet-Gateway hinaus: nicht gezeigt, nicht gezaehlt
    assert all(v2[k].on and v2[k].effect == rs.DROP for k in PATH)
    # 3 direkt zugeordnete heben 1 und 2 auf, ausgeschlossene fallen weg
    assert set(v2["assigned"].lifts) == PATH and v2["excluded"].effect == rs.DROP
    # 4-6 wie bisher
    assert (v2["l3out_helper"].effect, v2["l3out_covered"].effect, v2["cat10_out"].effect) == (
        rs.NOTE, rs.HIT, rs.HIT)  # fmt: skip
    # Vorschlaege: ausgeschaltet vor 4-6 (dahinter verdeckt)
    assert [r.key for r in rs.V2.rules[6:8]] == ["out_broad", "disabled"]
    assert not v2["out_broad"].on and not v2["disabled"].on


def test_erste_regel_entscheidet_ausnahme_hebt_auf():
    row = {"_rule": 7, "_rel": "impossible", "_scope": "subnet", "_out_broad": True}
    assert appdoc_filter.decide(row, rs.V1) == (rs.COUNTED, "out_broad")
    # Regelwerk 1: direkt zugeordnet hebt nur breite Quelle und Internet auf
    assert appdoc_filter.decide(row, rs.V1, assigned={7}) == (rs.COUNTED, "impossible")
    assert appdoc_filter.decide(row, rs.V2) == (rs.DROP, "impossible")
    assert appdoc_filter.decide(row, rs.V2, assigned={7}) == (rs.TABLE, None)
    assert appdoc_filter.decide(row, rs.V2, excluded={7}) == (rs.DROP, "excluded")
    off = {"_rule": 8, "_rel": "impossible", "_rel_cause": "off_path", "_scope": "specific"}
    assert appdoc_filter.decide(off, rs.V2) == (rs.DROP, "off_path_specific")
    assert appdoc_filter.decide({**off, "_scope": "any"}, rs.V1) == (rs.COUNTED, "off_path")
    marked = {"_rule": 9, "_disabled": True, "Aktion": "Erlauben"}
    profile = rs.Profile("t", "t", rules=rs.validate_rules([{"key": "disabled", "effect": "mark"}]))
    marked["_decided"], marked["_decided_by"] = appdoc_filter.decide(marked, profile)
    appdoc_filter.mark(marked)
    assert marked["Aktion"] == "Erlauben (deaktiviert)"


def test_ungueltige_regeln():
    for bad in ([{"key": "gibt-es-nicht"}], [{"key": "excluded"}, {"key": "excluded"}],
                [{"key": "excluded", "effect": "note"}],
                [{"key": "impossible"}, {"key": "assigned", "effect": "except",
                                         "lifts": ["impossible"]}]):  # fmt: skip
        with pytest.raises(rs.Invalid):
            rs.validate_rules(bad)
    # fehlende Bedingungen (neuer als das Profil): ausgeschaltet ans Ende
    rules = rs.validate_rules([{"key": "impossible", "effect": "drop"}])
    assert rules[0] == rs.Rule("impossible", True, rs.DROP) and len(rules) == len(rs.CONDITIONS)
    assert not any(r.on for r in rules[1:])


def _body(profile: dict, **changes) -> dict:
    rules = [{k: r[k] for k in ("key", "on", "effect", "lifts")} for r in profile["rules"]]
    for r in rules:
        r.update(changes.get(r["key"], {}))
    return {"title": "Test A", "based_on": profile["key"], "rules": rules}


def test_umschalten_und_eigene_profile(client, session_factory):
    tid = pc._seed(client)
    pid = _default_profile(session_factory)
    web = next(s["name"] for s in system_list(client, tid) if s["ip"] == "10.30.0.10")
    body = {"name": "Web", "members": [{"kind": "host", "ref": web}]}
    app = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP).json()
    rules = client.get("/rules", params={"tenant_id": tid, "limit": 500}).json()
    falsch = next(r["id"] for r in rules if r["name"] == "lan-to-dmz-falsch")

    def doc():
        r = client.get(f"/applications/{app['id']}/doc")
        assert r.status_code == 200, r.text
        return r.json()

    def names(d):
        return {r["name"] for r in d["rules"]}

    d1 = doc()  # Vorgabe Regelwerk 1: unmoeglich und ausgehend breit gezaehlt
    s1 = d1["summary"]
    assert s1["ruleset"]["key"] == "v1"
    assert s1["ruleset"]["title"] == "Regelwerk 1 – Stand bis 0.117.0"
    assert s1["impossible"] == 1 and s1["outbound_broad"] >= 1
    assert {"impossible", "out_broad"} <= {f["key"] for f in s1["filtered"]}
    assert "lan-to-dmz-falsch" not in names(d1)

    url = f"/naming-profiles/{pid}/appdoc-ruleset"
    out = client.get(url, headers=VIEW).json()
    assert out["active"] == "v1" and [p["key"] for p in out["profiles"]] == ["v1", "v2"]
    assert len(out["conditions"]) == len(rs.CONDITIONS)
    assert client.put(f"{url}/active", json={"key": "v2"}, headers=OP).status_code == 403
    assert client.put(f"{url}/active", json={"key": "x"}, headers=ADMIN).status_code == 422
    r = client.put(f"{url}/active", json={"key": "v2"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["active"] == "v2", r.text

    d2 = doc()  # Regelwerk 2: nicht im Pfad weder gezeigt noch gezaehlt
    s2 = d2["summary"]
    assert s2["ruleset"]["key"] == "v2" and s2["impossible"] == 0
    assert not PATH & {f["key"] for f in s2["filtered"]}
    assert "lan-to-dmz-falsch" not in names(d2)
    # lan-to-inet-users (bisher „ausgehend breit“ gezaehlt) ist auch nicht im Pfad: weg
    assert s2["outbound_broad"] == 0 and "lan-to-inet-users" not in names(d2)
    assert "block-all" in names(d2)  # deaktiviert: in Regelwerk 2 nur ein Vorschlag (aus)
    # direkt zugeordnet hebt „nicht im Pfad“ auf: in der Kategorie
    r = client.post(f"/applications/{app['id']}/rules", json={"rule_id": falsch}, headers=OP)
    assert r.status_code == 200, r.text
    d2 = doc()
    shown = {rid for t in d2["tables"] for row in t["rows"] for rid in row.get("_rules", [])}
    assert falsch in shown

    v2 = next(p for p in out["profiles"] if p["key"] == "v2")
    # eingebaute bleiben unveraendert
    assert client.put(f"{url}/v2", json=_body(v2), headers=ADMIN).status_code == 422
    assert client.delete(f"{url}/v2", headers=ADMIN).status_code == 422  # aktiv (0.119.0)
    # eigenes Profil: Regelwerk 2 mit „ausgehend breit“ gezaehlt und deaktivierte weg
    own = _body(v2, out_broad={"on": True}, disabled={"on": True, "effect": "drop"})
    assert client.post(url, json=own, headers=OP).status_code == 403
    r = client.post(url, json=own, headers=ADMIN)
    assert r.status_code == 201, r.text
    key = r.json()["saved"]
    assert key.startswith("p-") and r.json()["active"] == "v2"
    client.put(f"{url}/active", json={"key": key}, headers=ADMIN)
    d3 = doc()
    assert d3["summary"]["ruleset"] == {"key": key, "title": "Test A", "description": ""}
    assert d3["summary"]["outbound_broad"] >= 1 and "block-all" not in names(d3)
    # gleiche Reihenfolge aendern geht; eine neue Reihenfolge nur als neues Profil
    mine = next(p for p in client.get(url, headers=VIEW).json()["profiles"] if p["key"] == key)
    marked = _body(mine, disabled={"effect": "mark"})
    assert client.put(f"{url}/{key}", json=marked, headers=ADMIN).status_code == 200
    order = marked["rules"]
    assert order[7]["key"] == "disabled"
    moved = {**marked, "rules": [order[7], *order[:7], *order[8:]]}
    r = client.put(f"{url}/{key}", json=moved, headers=ADMIN)
    assert r.status_code == 422 and "new ruleset" in r.text
    r = client.post(url, json={**moved, "title": "Test B"}, headers=ADMIN)
    assert r.status_code == 201, r.text
    first = r.json()["saved"]
    client.put(f"{url}/active", json={"key": first}, headers=ADMIN)
    d4 = doc()  # deaktivierte zuerst, markiert: block-all in der Kategorie mit „(deaktiviert)“
    actions = {row["Description"]: row["Aktion"] for t in d4["tables"] for row in t["rows"]}
    assert actions["block-all"].endswith("(deaktiviert)")

    # loeschen (0.119.0): das aktive nicht - zuerst ein anderes aktivieren
    r = client.delete(f"{url}/{first}", headers=ADMIN)
    assert r.status_code == 422 and "activate another" in r.text
    client.put(f"{url}/active", json={"key": "v1"}, headers=ADMIN)
    assert client.delete(f"{url}/{first}", headers=ADMIN).status_code == 200
    out = client.get(url, headers=VIEW).json()
    assert out["active"] == "v1" and first not in [p["key"] for p in out["profiles"]]
    assert doc()["summary"]["ruleset"]["key"] == "v1"

    s = session_factory()
    acts = [(a.action, audit.decode_detail(a)) for a in s.query(AuditLog).order_by(AuditLog.id)
            if a.action.startswith("appdoc_ruleset.")]  # fmt: skip
    s.close()
    assert [a for a, _d in acts][:3] == ["appdoc_ruleset.activated", "appdoc_ruleset.saved",
                                         "appdoc_ruleset.activated"]  # fmt: skip
    assert acts[0][1] == {"profile_id": pid, "key": "v2"}


def test_namen_beschreibung_und_ausblenden(client, session_factory):
    """0.119.0 (Anwender: „richtige Namen und eine Beschreibung, auch den Bestehenden … löschen
    sollte ich auch können, sonst haben wir einen Friedhof an Test-Regelwerken“)."""
    pid = _default_profile(session_factory)
    url = f"/naming-profiles/{pid}/appdoc-ruleset"
    out = client.get(url, headers=VIEW).json()
    v1 = out["profiles"][0]
    assert v1["description"].startswith("Wie die Doku bis 0.117.0") and out["hidden"] == []

    # eingebautes umbenennen: Name und Beschreibung im Namensprofil, die Regeln bleiben
    label = {"title": "Standard DC", "title_en": "Standard DC",
             "description": "Für die Rechenzentren",
             "description_en": "For the data centers"}  # fmt: skip
    assert client.patch(f"{url}/v1", json=label, headers=OP).status_code == 403
    r = client.patch(f"{url}/v1", json=label, headers=ADMIN)
    assert r.status_code == 200, r.text
    v1 = next(p for p in r.json()["profiles"] if p["key"] == "v1")
    assert v1["title"] == "Standard DC" and v1["builtin"]
    assert v1["description"] == "Für die Rechenzentren"
    assert [x["key"] for x in v1["rules"]] == [x.key for x in rs.V1.rules]
    en = client.get(url, headers={**VIEW, "Accept-Language": "en"}).json()
    assert en["profiles"][0]["description"] == "For the data centers"
    # leer = wieder die Vorgabe
    r = client.patch(f"{url}/v1", json={}, headers=ADMIN)
    assert r.json()["profiles"][0]["title"] == "Regelwerk 1 – Stand bis 0.117.0"
    assert client.patch(f"{url}/gibt-es-nicht", json=label, headers=ADMIN).status_code == 422

    # eigenes Profil mit Beschreibung, umbenennen (ohne Titel nicht)
    body = _body(next(p for p in out["profiles"] if p["key"] == "v2"))
    body |= {"title": "Test X", "description": "nur zum Probieren"}
    key = client.post(url, json=body, headers=ADMIN).json()["saved"]
    mine = next(p for p in client.get(url, headers=VIEW).json()["profiles"] if p["key"] == key)
    assert mine["description"] == "nur zum Probieren"
    assert client.patch(f"{url}/{key}", json={"title": " "}, headers=ADMIN).status_code == 422
    r = client.patch(f"{url}/{key}", json={"title": "Test Y", "description": "neu"}, headers=ADMIN)
    mine = next(p for p in r.json()["profiles"] if p["key"] == key)
    assert (mine["title"], mine["description"], len(mine["rules"])) == ("Test Y", "neu", 11)

    # loeschen: eingebaute ausgeblendet und wiederherstellbar, das aktive nicht
    assert client.delete(f"{url}/v1", headers=ADMIN).status_code == 422  # aktiv (Vorgabe)
    client.put(f"{url}/active", json={"key": key}, headers=ADMIN)
    r = client.delete(f"{url}/v1", headers=ADMIN)
    assert r.status_code == 200, r.text
    assert [p["key"] for p in r.json()["profiles"]] == ["v2", key]
    assert r.json()["hidden"] == [{"key": "v1", "title": "Regelwerk 1 – Stand bis 0.117.0"}]
    assert client.put(f"{url}/active", json={"key": "v1"}, headers=ADMIN).status_code == 422
    r = client.post(f"{url}/v1/restore", headers=ADMIN)
    assert r.status_code == 200 and r.json()["hidden"] == []
    assert client.post(f"{url}/{key}/restore", headers=ADMIN).status_code == 422
    client.put(f"{url}/active", json={"key": "v1"}, headers=ADMIN)
    assert client.delete(f"{url}/{key}", headers=ADMIN).status_code == 200

    s = session_factory()
    acts = [a.action for a in s.query(AuditLog) if a.action.startswith("appdoc_ruleset.")]
    s.close()
    want = {"appdoc_ruleset.renamed", "appdoc_ruleset.restored", "appdoc_ruleset.deleted"}
    assert want <= set(acts)


def test_regelwerke_vergleichen(client, session_factory):
    """0.120.0 (Anwender: „Vergleich … aber nur mit Klicken auf einen Button“): die Doku mit dem
    aktiven und einem anderen Regelwerk - was dazukommt bzw. wegfällt, mit Grund."""
    tid = pc._seed(client)
    pid = _default_profile(session_factory)
    web = next(s["name"] for s in system_list(client, tid) if s["ip"] == "10.30.0.10")
    body = {"name": "Web", "members": [{"kind": "host", "ref": web}]}
    app = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP).json()
    url = f"/applications/{app['id']}/ruleset-compare"
    choice = client.get(url, headers=VIEW).json()
    assert choice["current"]["key"] == "v1"
    assert [p["key"] for p in choice["profiles"]] == ["v1", "v2"]
    assert client.get(url, params={"key": "x"}, headers=VIEW).status_code == 404

    # eigenes Profil: deaktivierte Regeln zuerst weglassen
    rules_url = f"/naming-profiles/{pid}/appdoc-ruleset"
    v2 = next(p for p in client.get(rules_url, headers=VIEW).json()["profiles"]
              if p["key"] == "v2")  # fmt: skip
    own = _body(v2, disabled={"on": True, "effect": "drop"})
    order = own["rules"]
    own["rules"] = [order[7], *order[:7], *order[8:]]
    key = client.post(rules_url, json=own, headers=ADMIN).json()["saved"]

    out = client.get(url, params={"key": key}, headers=VIEW).json()
    assert out["current"]["key"] == "v1" and out["other"]["key"] == key
    removed = {x["name"]: x for x in out["rules"]["removed"]}
    assert removed["block-all"]["key"] == "disabled"
    assert removed["block-all"]["why"] == "Deaktivierte Firewall-Regeln"
    assert removed["block-all"]["effect_label"] == "nicht gezeigt, nicht gezählt"
    assert "block-all" in {x["name"] for x in out["categories"]["removed"]}
    assert {f["key"] for f in out["current"]["filtered"]} >= {"impossible", "out_broad"}
    assert not {f["key"] for f in out["other"]["filtered"]} & PATH
    assert out["current"]["rules"] == out["other"]["rules"] + len(out["rules"]["removed"]) - len(
        out["rules"]["added"])  # fmt: skip
    # die Doku selbst bleibt beim aktiven Regelwerk
    doc = client.get(f"/applications/{app['id']}/doc").json()
    assert doc["summary"]["ruleset"]["key"] == "v1" and "block-all" in {
        r["name"] for r in doc["rules"]}  # fmt: skip
