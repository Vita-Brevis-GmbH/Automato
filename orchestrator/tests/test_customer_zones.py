# ruff: noqa: S314 - geparst wird nur die eigene Ausgabe des Exports
"""Kunden auf Zonen und Kunden-Benutzer in Automato (0.53.0, ADR 0018 Schritt 4).

Umgebung wie in test_zones: fw-01 (Server, Transfer, Provider), fw-02 (LAN), ACI-VRF mit EPG_web.
Kunde A besitzt die Zone Server, Kunde B die Zone LAN; ACI Web, Internet und Transfer sind
geteilt.
"""

import xml.etree.ElementTree as ET

from app.auth import assignments
from app.auth.principal import Principal
from app.models import AuditLog
from test_zones import ADMIN, OP, VIEW, _seed, _sel, _zones

SVG = "{http://www.w3.org/2000/svg}"


def _customer(client, tid, name):
    r = client.post("/customers", json={"tenant_id": tid, "name": name, "selectors": []},
                    headers=OP)  # fmt: skip
    assert r.status_code == 201, r.text
    return r.json()


def _owned(client, tid, ids):
    z = _zones(client, tid, ids)
    a, b = _customer(client, tid, "Kunde A"), _customer(client, tid, "Kunde B")
    for key, owner in (("server", a), ("lan", b)):
        zone = z[key]
        body = {"tenant_id": tid, "name": zone["name"], "kind": zone["kind"],
                "customer_id": owner["id"], "selectors": zone["selectors"]}  # fmt: skip
        r = client.put(f"/zones/{zone['id']}", json=body, headers=OP)
        assert r.status_code == 200, r.text
    return z, a, b


def _as(tid, *customers):
    return {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid),
            "X-Dev-Customers": ",".join(str(c) for c in customers)}  # fmt: skip


def _members(client, customer_id, kind):
    r = client.get(f"/customers/{customer_id}/members", params={"kind": kind}, headers=ADMIN)
    return {m["name"]: m["via"] for m in r.json()}


def test_zonen_des_kunden_in_der_zugehoerigkeit(client):
    tid, ids = _seed(client)
    z, a, b = _owned(client, tid, ids)
    assert _members(client, a["id"], "net") == {"10.20.0.0/24": "Zone Server"}
    rules = _members(client, a["id"], "rule")
    assert {"srv-to-web", "srv-out", "lan-to-srv", "inet-to-srv", "wide"} <= set(rules)
    assert "lan-to-web" not in rules  # LAN -> ACI Web: Kunde B
    assert "lan-to-web" in _members(client, b["id"], "rule")
    listed = {c["name"]: c for c in client.get("/customers", params={"tenant_id": tid}).json()}
    assert listed["Kunde A"]["zones"] == ["Server"] and listed["Kunde A"]["counts"]["net"] == 1
    # Vorschau mit den Zonen des Kunden
    body = {"tenant_id": tid, "selectors": [], "customer_id": a["id"]}
    p = client.post("/customers/preview", json=body, headers=OP).json()
    assert p["counts"]["net"] == 1 and p["samples"]["net"] == ["10.20.0.0/24"]
    # ausschliessender Selektor des Kunden gilt auch fuer seine Zonen
    excl = {"tenant_id": tid, "name": "Kunde A", "selectors": [
        {"kind": "prefix", "value": "10.20.0.0/25", "exclude": True}]}  # fmt: skip
    assert client.put(f"/customers/{a['id']}", json=excl, headers=OP).status_code == 200
    assert _members(client, a["id"], "net") == {"10.20.0.128/25": "Zone Server"}
    # Zone an Kunde B: A verliert, B gewinnt - ohne neuen Lauf
    server = z["server"]
    body = {"tenant_id": tid, "name": "Server", "customer_id": b["id"],
            "selectors": server["selectors"]}  # fmt: skip
    assert client.put(f"/zones/{server['id']}", json=body, headers=OP).status_code == 200
    assert _members(client, a["id"], "net") == {}
    assert "10.20.0.0/24" in _members(client, b["id"], "net")


def test_kunden_benutzer_sehen_ihre_zonen(client):
    tid, ids = _seed(client)
    z, a, b = _owned(client, tid, ids)
    ua = _as(tid, a["id"])
    zones = {
        x["name"]: x for x in client.get("/zones", params={"tenant_id": tid}, headers=ua).json()
    }
    # eigene Zone mit Netzen, geteilte an seinem Knoten nur mit Namen; Kunde B nicht
    assert set(zones) == {"Server", "Transfer", "Internet"}
    assert zones["Server"]["own"] and zones["Server"]["nets"] == 1
    assert not zones["Internet"]["own"] and zones["Internet"]["nets"] == 0
    assert all(x["selectors"] == [] for x in zones.values())
    nets = client.get(f"/zones/{z['server']['id']}/nets", headers=ua)
    assert nets.json()["items"][0]["network"] == "10.20.0.0/24"
    assert client.get(f"/zones/{z['lan']['id']}/nets", headers=ua).status_code == 404
    assert client.get(f"/zones/{z['inet']['id']}/nets", headers=ua).status_code == 404

    # Plan: nur fw-01 (dort haengt Server), keine Netze ohne Zone, keine Router
    plan = client.get("/zones/plan", params={"tenant_id": tid}, headers=ua).json()
    assert {n["label"] for n in plan["nodes"]} == {"fw-01 · VDOM root"}
    assert {x["name"] for x in plan["zones"]} == {"Server", "Transfer", "Internet"}
    assert plan["unzoned"] == {"total": 0, "items": []}
    assert all(a_["routes"] == [] for a_ in plan["attachments"])
    shared = [a_ for a_ in plan["attachments"] if a_["zone"] == z["inet"]["id"]]
    assert shared and shared[0]["networks"] == [] and shared[0]["interfaces"] == ["wan1", "wan2"]
    assert {t["title"] for t in plan["layout"]["zones"]} == {"Server", "Transfer", "Internet"}

    # Matrix: Zellen mit Server; Kunde B als „anderer Kunde“
    m = client.get("/zones/matrix", params={"tenant_id": tid}, headers=ua).json()
    srv, web, inet = z["server"]["id"], z["web"]["id"], z["inet"]["id"]
    cells = {(c["src"], c["dst"]): c for c in m["cells"]}
    assert set(cells) == {(srv, web), (srv, "any"), ("other", srv), (inet, srv), (srv, "none")}
    assert {x["name"] for x in m["zones"]} == {"Server", "ACI Web", "Internet"}
    assert z["lan"]["id"] not in m["axes"] and "other" in m["axes"]
    assert m["summary"]["rules"] == 5 and m["summary"]["without"] == 0
    rules = client.get("/zones/matrix/rules", params={"tenant_id": tid, "src": "other",
                                                      "dst": srv}, headers=ua).json()  # fmt: skip
    assert [r["name"] for r in rules["items"]] == ["lan-to-srv"]
    # ohne eigene Zone: leer
    empty = client.get("/zones/matrix/rules", params={"tenant_id": tid, "src": "other",
                                                      "dst": web}, headers=ua).json()  # fmt: skip
    assert empty["total"] == 0

    # Absicht: Internet -> Server sichtbar mit Verstoss, LAN -> ACI Web (Kunde B) nicht
    for src, dst in ((inet, srv), (z["lan"]["id"], web)):
        body = {"tenant_id": tid, "src_zone_id": src, "dst_zone_id": dst, "intent": "deny"}
        assert client.put("/zones/policy", json=body, headers=OP).status_code == 200
    m = client.get("/zones/matrix", params={"tenant_id": tid}, headers=ua).json()
    assert [(i["src"], i["dst"]) for i in m["intents"]] == [(inet, srv)]
    cell = next(c for c in m["cells"] if (c["src"], c["dst"]) == (inet, srv))
    assert cell["intent"] == "deny" and cell["violations"] == 1
    # Kunde B sieht sein LAN, Kunde A als „anderer Kunde“
    mb = client.get("/zones/matrix", params={"tenant_id": tid}, headers=_as(tid, b["id"])).json()
    assert {(c["src"], c["dst"]) for c in mb["cells"]} >= {(z["lan"]["id"], "other")}

    # Export: nur sein Plan, Titel mit dem Kunden; Versionen nicht
    r = client.get("/zones/plan/export", params={"tenant_id": tid, "format": "svg"}, headers=ua)
    texts = [t.text for t in ET.fromstring(r.content).iter(f"{SVG}text")]
    assert "Zonenplan Kunde A" in texts and "LAN" not in texts and "fw-02" not in texts
    pdf = client.get("/zones/plan/export", params={"tenant_id": tid, "format": "pdf"}, headers=ua)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    v = client.post(
        "/zones/plan/versions", json={"tenant_id": tid, "label": "x"}, headers=OP
    ).json()
    for url, params in (
        ("/zones/plan/export", {"version_id": v["id"]}),
        ("/zones/plan/versions", {}),
        (f"/zones/plan/versions/{v['id']}", {}),
    ):
        r = client.get(url, params={"tenant_id": tid, **params}, headers=ua)
        assert r.status_code == 403, (url, r.text)
    body = {"tenant_id": tid, "selectors": [_sel("prefix", "10.0.0.0/8")]}
    assert client.post("/zones/preview", json=body, headers=ua).status_code == 403


def test_zuordnung_benutzer_kunde_in_automato(client, session_factory):
    tid, ids = _seed(client)
    z, a, b = _owned(client, tid, ids)
    body = {"customer_id": a["id"], "login": "Kunde.A@Example.ch", "note": "Ansprechpartner"}
    assert client.post("/customer-users", json=body, headers=OP).status_code == 403  # nur Admin
    r = client.post("/customer-users", json=body, headers=ADMIN)
    assert r.status_code == 201, r.text
    assigned = r.json()
    assert (assigned["login"], assigned["role"], assigned["customer"]) == (
        "kunde.a@example.ch", "viewer", "Kunde A")  # fmt: skip
    assert client.post("/customer-users", json=body, headers=ADMIN).status_code == 409
    assert client.post("/customer-users", json={**body, "login": "x", "role": "admin"},
                       headers=ADMIN).status_code == 422  # fmt: skip
    # das eigene Konto nicht (sonst sperrt sich der Admin aus)
    me = {"X-Dev-User": "chef", "X-Dev-Roles": "admin"}
    r = client.post("/customer-users", json={**body, "login": "CHEF"}, headers=me)
    assert r.status_code == 409

    # angemeldet ohne Rolle aus Keycloak: die Zuordnung macht ihn zum Kunden-Benutzer
    user = {"X-Dev-User": "kunde.a@example.ch"}
    who = client.get("/auth/me", headers=user).json()
    assert who["is_customer"] and who["customer_ids"] == [a["id"]]
    assert who["tenant_ids"] == [tid] and who["roles"] == ["viewer"]
    assert who["customer_source"] == "automato" and who["customers"][0]["name"] == "Kunde A"
    zones = client.get("/zones", params={"tenant_id": tid}, headers=user).json()
    assert {x["name"] for x in zones if x["own"]} == {"Server"}
    assert client.get("/systems", params={"tenant_id": tid}, headers=user).status_code == 403
    # auch ein Betreuer mit Rolle admin wird mit Zuordnung eingeschraenkt (nie Admin)
    boss = {"X-Dev-User": "kunde.a@example.ch", "X-Dev-Roles": "admin"}
    assert client.get("/customer-users", headers=boss).status_code == 403

    # Rolle operator: triagiert eigene Findings
    listed = client.get("/customer-users", params={"tenant_id": tid}, headers=ADMIN).json()
    assert [x["login"] for x in listed] == ["kunde.a@example.ch"]
    r = client.patch(f"/customer-users/{assigned['id']}", json={"role": "operator"},
                     headers=ADMIN)  # fmt: skip
    assert r.status_code == 200 and r.json()["role"] == "operator"
    assert "operator" in client.get("/auth/me", headers=user).json()["roles"]

    # entfernen: ohne Rolle aus Keycloak kein Zugriff mehr
    assert client.delete(f"/customer-users/{assigned['id']}", headers=ADMIN).status_code == 204
    assert client.get("/auth/me", headers=user).status_code == 403
    s = session_factory()
    actions = [x.action for x in s.query(AuditLog).all() if x.action.startswith("customer_user.")]
    s.close()
    assert actions == ["customer_user.assigned", "customer_user.changed", "customer_user.removed"]

    # Kunde geloescht: seine Zuordnungen fallen weg
    client.post("/customer-users", json={**body, "customer_id": b["id"]}, headers=ADMIN)
    assert client.delete(f"/customers/{b['id']}", headers=OP).status_code == 204
    assert client.get("/customer-users", params={"tenant_id": tid}, headers=ADMIN).json() == []


def test_zuordnung_und_claim_zusammen():
    claim = Principal(subject="k", roles=frozenset({"operator"}), tenant_ids=(1,),
                      customer_ids=(3,), logins=("k",), customer_source="claim")  # fmt: skip
    p = assignments.apply(claim, [(4, 2, "viewer")])
    assert p.customer_ids == (3, 4) and p.tenant_ids == (1, 2)
    assert p.roles == frozenset({"viewer", "operator"}) and p.customer_source == "claim+automato"
    staff = Principal(subject="s", roles=frozenset({"admin"}), logins=("s",))
    p = assignments.apply(staff, [(5, 1, "viewer")])
    assert p.is_customer and not p.is_admin and p.roles == frozenset({"viewer"})
    assert assignments.apply(staff, []) is staff  # ohne Zuordnung: unveraendert
    # Betreuer ohne Kennung (dev-Standard): keine Abfrage
    assert assignments.lookup(None, ()) == []
    assert VIEW  # gleiche Kopfzeilen wie in test_zones
