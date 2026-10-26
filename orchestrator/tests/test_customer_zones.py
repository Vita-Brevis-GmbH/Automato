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
from test_zones import ADMIN, OP, VIEW, _model, _seed, _sel, _zones

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
    # eigene Zone mit Netzen; Zonen nur ueber Netze haben keine Uebergaenge - geteilte Zonen
    # zeigt erst das Zonenmodell (test_zonenplan_je_kunde); Kunde B nicht
    assert set(zones) == {"Server"}
    assert zones["Server"]["own"] and zones["Server"]["nets"] == 1
    assert all(x["selectors"] == [] for x in zones.values())
    nets = client.get(f"/zones/{z['server']['id']}/nets", headers=ua)
    assert nets.json()["items"][0]["network"] == "10.20.0.0/24"
    assert client.get(f"/zones/{z['lan']['id']}/nets", headers=ua).status_code == 404
    assert client.get(f"/zones/{z['inet']['id']}/nets", headers=ua).status_code == 404

    # Plan: seine Zone, der genutzte Zugang zu Kunde B (Regel lan-to-srv) als „anderer Kunde“;
    # keine Knoten ohne Zone, keine Netze ohne Zone
    plan = client.get("/zones/plan", params={"tenant_id": tid}, headers=ua).json()
    assert plan["nodes"] == [] and plan["unzoned"] == {"total": 0, "items": []}
    assert {x["name"] for x in plan["zones"]} == {"Server", "anderer Kunde"}
    assert {t["title"] for t in plan["layout"]["zones"]} == {"Server", "anderer Kunde"}

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


def test_zonenplan_je_kunde(client):
    """Zonenmodell mit Kunden: Kunde A besitzt Server, Kunde B Office; DC, Edge Internet und
    Public sind geteilt. Die Uebersicht zeigt die Kunden je als eine Zone, der Plan eines Kunden
    seine Zonen, die geteilten dahinter und genutzte Zugaenge zu anderen Kunden."""
    tid, ids = _seed(client)
    z = _model(client, tid, ids)
    a, b = _customer(client, tid, "Kunde A"), _customer(client, tid, "Kunde B")
    for key, owner in (("server", a), ("office", b)):
        zone = z[key]
        body = {"tenant_id": tid, "name": zone["name"], "kind": zone["kind"],
                "code": zone["code"], "customer_id": owner["id"],
                "selectors": zone["selectors"]}  # fmt: skip
        assert client.put(f"/zones/{zone['id']}", json=body, headers=OP).status_code == 200
    ka, kb = f"customer:{a['id']}", f"customer:{b['id']}"

    over = client.get("/zones/plan", params={"tenant_id": tid}).json()
    assert over["view"]["mode"] == "overview"
    assert [c["name"] for c in over["customers"]] == ["Kunde A", "Kunde B"]
    zones = {x["id"]: x for x in over["zones"]}
    assert zones[ka]["collapsed"] and zones[ka]["members"] == ["Server"]
    assert zones[kb]["members"] == ["Office"] and z["server"]["id"] not in zones
    assert {t["name"] for t in over["transitions"]} == {
        "DC-EINET", "EINET-PUB", "Kunde A-EINET", "Kunde B-EINET"}  # fmt: skip
    assert over["summary"]["collapsed"] == 2

    # Plan von Kunde A: Server, die geteilten Zonen dahinter, Kunde B (Regel lan-to-srv)
    mine = client.get("/zones/plan", params={"tenant_id": tid, "customer_id": a["id"]}).json()
    assert mine["view"] == {"mode": "customer", "customer_id": a["id"], "customer": "Kunde A"}
    assert {x["name"] for x in mine["zones"]} == {
        "Server", "Edge Internet", "DC", "Public", "Kunde B"}  # fmt: skip
    assert {t["name"] for t in mine["transitions"]} == {
        "DC-EINET", "EINET-PUB", "SRV-EINET", "Kunde B-EINET"}  # fmt: skip
    # alle Zonen einzeln
    full = client.get("/zones/plan", params={"tenant_id": tid, "all": "true"}).json()
    assert full["view"]["mode"] == "all" and full["summary"]["collapsed"] == 0

    # Kunden-Benutzer A: geteilte Zonen ohne Knoten und Netze, Kunde B als „anderer Kunde“
    ua = _as(tid, a["id"])
    plan = client.get("/zones/plan", params={"tenant_id": tid}, headers=ua).json()
    names = {x["name"]: x for x in plan["zones"]}
    assert set(names) == {"Server", "Edge Internet", "DC", "Public", "anderer Kunde"}
    assert names["Edge Internet"]["nodes"] == [] and names["DC"]["samples"] == []
    assert plan["nodes"] == [] and "Kunde B" not in str(plan)
    trans = {t["name"]: t for t in plan["transitions"]}
    assert set(trans) == {"DC-EINET", "EINET-PUB", "SRV-EINET", "anderer Kunde-EINET"}
    assert trans["DC-EINET"]["networks"] == [] and trans["EINET-PUB"]["count"] == 0
    (srv,) = trans["SRV-EINET"]["networks"]  # eigene Zone: Netz ja, fremde Geraete nein
    assert srv["network"] == "10.20.0.0/24" and srv["legs"] == [] and srv["routers"] == []
    listed = {x["name"] for x in client.get("/zones", params={"tenant_id": tid},
                                            headers=ua).json()}  # fmt: skip
    assert listed == {"Server", "Edge Internet", "DC", "Public"}
    # Netze der eigenen Zone: Uebergang ohne fremde Geraete; Zone eines anderen Kunden maskiert
    nets = client.get(f"/zones/{z['server']['id']}/nets", headers=ua).json()
    (t,) = nets["transitions"]
    assert (t["to"], t["name"]) == ("Edge Internet", "SRV-EINET")
    assert t["networks"][0]["legs"] == [] and nets["nodes"] == []
    office = client.get(f"/zones/{z['office']['id']}/nets", headers=ua)
    assert office.status_code == 404  # Zone von Kunde B
    # fw-01 gehoert vorne in der Reihenfolge einer Zone von Kunde B: der Uebergang von Server
    # dorthin heisst fuer A „anderer Kunde“
    body = {"tenant_id": tid, "name": "Firewall B", "customer_id": b["id"], "position": 1,
            "selectors": [_sel("fw_vdom", "root", source_system_id=ids["fw-01"])]}  # fmt: skip
    assert client.post("/zones", json=body, headers=OP).status_code == 201
    srv_b = client.get(f"/zones/{z['server']['id']}/nets", headers=ua).json()
    assert [(t["to"], t["to_id"]) for t in srv_b["transitions"]] == [("anderer Kunde", "other")]
    assert "Firewall B" not in str(srv_b) and "Kunde B" not in str(srv_b)
    svg = client.get("/zones/plan/export", params={"tenant_id": tid, "format": "svg"},
                     headers=ua)  # fmt: skip
    texts = [t.text for t in ET.fromstring(svg.content).iter(f"{SVG}text")]
    assert "anderer Kunde" in texts and "Kunde B" not in texts and "fw-01 · VDOM root" not in texts


def test_kommunikation_im_zonenplan(client):
    """Kommunikation (0.66.0): erlaubende Regeln je Zonenpaar als eigene Linie; im Plan eines
    Kunden die Partner (andere Kunden) einzeln ohne Knoten, mit Fokus auch ohne Uebergang;
    Kunden-Benutzer sehen den Partner als „anderer Kunde“."""
    tid, ids = _seed(client)
    z = _model(client, tid, ids)
    a, b = _customer(client, tid, "Kunde A"), _customer(client, tid, "Kunde B")
    for key, owner in (("server", a), ("office", b)):
        zone = z[key]
        body = {"tenant_id": tid, "name": zone["name"], "kind": zone["kind"],
                "code": zone["code"], "customer_id": owner["id"],
                "selectors": zone["selectors"]}  # fmt: skip
        assert client.put(f"/zones/{zone['id']}", json=body, headers=OP).status_code == 200
    srv, office, dc = z["server"]["id"], z["office"]["id"], z["dc"]["id"]

    def get(headers=None, **params):
        r = client.get("/zones/plan", params={"tenant_id": tid, **params}, headers=headers or {})
        assert r.status_code == 200, r.text
        return r.json()

    assert get(all="true")["flows"] == []  # nur auf Wunsch
    full = get(all="true", flows="true")
    pairs = {(f["a"], f["b"]): f for f in full["flows"]}
    # lan-to-srv: Office -> Server (erlaubt); srv-to-web: Server -> DC; lan-to-web ist gesperrt
    assert pairs[(office, srv)] == {"id": f"flow:{office}~{srv}", "a": office, "b": srv,
                                    "rules": 1, "ab": 1, "ba": 0}  # fmt: skip
    assert pairs[(dc, srv)]["ba"] == 1 and (dc, office) not in pairs
    edges = [e for e in full["layout"]["edges"] if e.get("flow")]
    assert len(edges) == len(pairs) and all(e["dashed"] for e in edges)
    flow = next(e for e in edges if {e["a"], e["b"]} == {office, srv})
    assert flow["label"] == "1 Regel" and flow["name"] == (
        "Office → Server: 1, Server → Office: 0 erlaubende Regeln")  # fmt: skip
    # Plan von Kunde A: Office (Kunde B) einzeln als Partner, ohne Knoten
    mine = get(customer_id=a["id"], flows="true")
    partner = next(x for x in mine["zones"] if x["id"] == office)
    assert partner["partner"] and partner["nodes"] == []
    assert not any(x.get("collapsed") for x in mine["zones"])
    assert (office, srv) in {(f["a"], f["b"]) for f in mine["flows"]}
    # Fokus auf Office ohne Nachbarn: mit Kommunikation kommt Server dazu
    focus = get(all="true", flows="true", focus=str(office), depth=0)
    assert {x["id"] for x in focus["zones"]} == {office, srv}
    assert "mit Kommunikation (Regeln)" in focus["filter"]["label"]
    # Kunden-Benutzer A: Partner als „anderer Kunde“, keine fremden Namen
    user = get(_as(tid, a["id"]), flows="true")
    names = {x["id"]: x["name"] for x in user["zones"]}
    assert ("Server", "anderer Kunde") in {(names[f["a"]], names[f["b"]]) for f in user["flows"]}
    assert "Office" not in str(user["flows"]) and "Kunde B" not in str(user)
    # Export zeigt die Linien
    svg = client.get("/zones/plan/export", params={"tenant_id": tid, "format": "svg", "all": "true",
                                                   "flows": "true"}).text  # fmt: skip
    assert "1 Regel" in svg and "#2563eb" in svg


def test_zonenplan_kunde_nur_benoetigte_knoten(client):
    """Plan eines Kunden (0.57.1): nur die Firewalls/VRFs, die er braucht - an seinen Netzen und
    auf den kuerzesten Wegen zu den Zonen seiner Regeln; geteilte Zonen ohne solche Knoten und
    ohne Uebergang fallen weg."""
    tid, ids = _seed(client)
    z = _model(client, tid, ids)
    a, b = _customer(client, tid, "Kunde A"), _customer(client, tid, "Kunde B")
    for key, owner in (("server", a), ("office", b)):
        zone = z[key]
        body = {"tenant_id": tid, "name": zone["name"], "kind": zone["kind"],
                "code": zone["code"], "customer_id": owner["id"],
                "selectors": zone["selectors"]}  # fmt: skip
        assert client.put(f"/zones/{zone['id']}", json=body, headers=OP).status_code == 200

    def plan(customer):
        p = client.get("/zones/plan", params={"tenant_id": tid, "customer_id": customer}).json()
        labels = {n["id"]: n["label"] for n in p["nodes"]}
        zones = {x["name"]: [labels[n] for n in x["nodes"]] for x in p["zones"]}
        return p, zones, {t["name"] for t in p["transitions"]}

    # Kunde B (Office an fw-02): Regeln zu Server (Kunde A) und ins DC - kein Internet, also
    # ohne Public (frueher ueber Edge Internet erreichbar und gezeigt)
    p, zones, trans = plan(b["id"])
    assert set(zones) == {"Office", "Edge Internet", "DC", "Kunde A"}
    assert zones["Edge Internet"] == ["fw-01 · VDOM root", "fw-02"]
    assert zones["DC"] == ["apic-01 · VRF TE:VRF1"]
    assert trans == {"DC-EINET", "EINET-OACC", "Kunde A-EINET"}
    assert "1|kunde-a" not in str(p["nodes"])

    # ohne die Zone Office liegt 10.60.0.0/24 in Edge Internet: Kunde A braucht fw-02 nicht
    assert client.delete(f"/zones/{z['office']['id']}", headers=OP).status_code == 204
    p, zones, trans = plan(a["id"])
    assert zones["Edge Internet"] == ["fw-01 · VDOM root"]
    einet = next(x for x in p["zones"] if x["name"] == "Edge Internet")
    assert einet["nodes_total"] == 2
    assert set(zones) == {"Server", "Edge Internet", "DC", "Public"}
    assert trans == {"DC-EINET", "EINET-PUB", "SRV-EINET"}
    assert "fw-02" not in {n["label"] for n in p["nodes"]}
    full = client.get("/zones/plan", params={"tenant_id": tid, "all": "true"}).json()
    assert "fw-02" in {n["label"] for n in full["nodes"]}  # alle Zonen: unveraendert
    # Kunden-Benutzer: auch die Anzahl der Knoten geteilter Zonen nicht
    ua = _as(tid, a["id"])
    mine = client.get("/zones/plan", params={"tenant_id": tid}, headers=ua).json()
    assert all("nodes_total" not in x for x in mine["zones"] if x["name"] != "Server")

    # Kunde C mit der VDOM kunde-a (ohne Regeln): nur sein Knoten und der Router dahinter
    c = _customer(client, tid, "Kunde C")
    body = {"tenant_id": tid, "name": "Mandant C", "customer_id": c["id"],
            "selectors": [_sel("fw_vdom", "kunde-a", source_system_id=ids["fw-01"])]}  # fmt: skip
    assert client.post("/zones", json=body, headers=OP).status_code == 201
    p, zones, trans = plan(c["id"])
    assert zones == {"Mandant C": ["fw-01 · VDOM kunde-a"], "ausserhalb (nicht gelesen)": []}
    assert {n["label"] for n in p["nodes"]} == {"fw-01 · VDOM kunde-a"}


def test_benoetigte_knoten_kuerzeste_wege():
    """`zone_struct.needed`: eigene Knoten, alle gleich kurzen Wege zur Zielzone, Verbund-Partner;
    Sackgassen und laengere Wege nicht."""
    from types import SimpleNamespace

    from app.store import zone_struct

    key = {n: (i, "root") for i, n in enumerate("ABCDEFG", start=1)}
    nid = {n: zone_struct.node_id(k) for n, k in key.items()}
    nodes = {k: SimpleNamespace(group="v" if n in "BF" else None) for n, k in key.items()}
    st = zone_struct.Structure(SimpleNamespace(nodes=nodes))
    st.node_zone = {key["A"]: 1, key["D"]: 2}
    # A - B - D und A - C - D (gleich lang), A - E (Sackgasse), A - G - X - D gibt es nicht
    for pair in ("AB", "BD", "AC", "CD", "AE"):
        st.segments.append(({nid[x] for x in pair}, set()))
    st.segments.append(({nid["G"]}, {3}))  # Zone 3 nur an G - von A aus nicht erreichbar
    need, reached = zone_struct.needed(st, {1}, {2, 3})
    names = {n for n, i in nid.items() if i in need}
    assert names == {"A", "B", "C", "D", "F"}  # F: Verbund-Partner von B
    assert reached == {2}
    # Netz der eigenen Zone mit einem Nachbarn: der Nachbar gehoert dazu
    st.segments.append(({nid["A"], nid["E"]}, {1}))
    need, _ = zone_struct.needed(st, {1}, set())
    assert {n for n, i in nid.items() if i in need} == {"A", "E"}


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
