# ruff: noqa: S314 - geparst wird nur die eigene Ausgabe des Exports
"""Zonenplan uebersichtlich (0.61.0, ADR 0024 Nr. 14): Filter (Suche, Fokus mit Nachbarn),
Ebenen ausblenden, Zonen von Hand anordnen - Oberflaeche, Export und Versionen gleich.

Umgebung wie in test_zones (`_model`): DC (ACI-Tenant), Edge Internet (fw-01/fw-02 root), Office
(Access), Server (Interface srv) und Public (Anbindung) mit den Uebergaengen DC-EINET,
EINET-OACC, EINET-PUB und SRV-EINET; „Knoten ohne Zone“ (fw-01 · VDOM kunde-a).
"""

import xml.etree.ElementTree as ET

from app.models import AuditLog, ZonePlanArrangement
from app.store import plan_export, plan_view
from test_zones import ADMIN, OP, VIEW, _model, _seed

SVG = "{http://www.w3.org/2000/svg}"


def _plan(client, tid, headers=None, **params) -> dict:
    r = client.get("/zones/plan", params={"tenant_id": tid, **params}, headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()


def _names(plan) -> set[str]:
    return {z["name"] for z in plan["zones"] if isinstance(z["id"], int)}


def test_filter_suche_fokus_und_ebenen(client):
    tid, ids = _seed(client)
    z = _model(client, tid, ids)
    full = _plan(client, tid)
    assert full["filter"] == {"active": False, "zones": 5, "total": 5, "hidden": 0}
    assert {c["name"] for c in full["zone_choices"]} >= {"DC", "Public", "Knoten ohne Zone"}
    assert {c["layer"] for c in full["zone_choices"]} >= {"dc", "edge", "access", "public"}

    # Suche: Treffer mit den Nachbarn (ein Uebergang weit), Layout ruckt zusammen
    p = _plan(client, tid, q="dc")
    assert _names(p) == {"DC", "Edge Internet"}
    assert [t["name"] for t in p["transitions"]] == ["DC-EINET"]
    assert p["filter"]["zones"] == 2 and p["filter"]["total"] == 5 and p["filter"]["matches"] == 1
    assert p["filter"]["label"] == "Suche „dc“ + Nachbarn"
    assert len(p["layout"]["zones"]) == 2 and p["layout"]["width"] < full["layout"]["width"]
    # nur die Treffer; Suche nach einem Geraet bzw. dem Namen eines Uebergangs
    assert _names(_plan(client, tid, q="dc", depth=0)) == {"DC"}
    assert _names(_plan(client, tid, q="fw-02", depth=0)) == {"Edge Internet"}
    assert _names(_plan(client, tid, q="srv-einet", depth=0)) == {"Server", "Edge Internet"}

    # Fokus auf gewaehlte Zonen, zwei Schritte weit
    office = str(z["office"]["id"])
    assert _names(_plan(client, tid, focus=[office], depth=0)) == {"Office"}
    assert _names(_plan(client, tid, focus=[office], depth=1)) == {"Office", "Edge Internet"}
    assert _names(_plan(client, tid, focus=[office], depth=2)) == _names(full)

    # Ebenen ausblenden: Access weg samt Uebergang; gewaehlte Zone bleibt trotzdem
    p = _plan(client, tid, hide_layer=["access"])
    assert "Office" not in _names(p) and "EINET-OACC" not in {t["name"] for t in p["transitions"]}
    assert p["filter"]["label"] == "ohne Access"
    assert "Office" in _names(_plan(client, tid, hide_layer=["access"], focus=[office]))
    # Edge weg und Zonen ohne Knoten und Uebergang ausblenden: nur das DC (mit Knoten) bleibt
    p = _plan(client, tid, hide_layer=["edge", "none"], hide=["empty"])
    assert _names(p) == {"DC"} and p["transitions"] == []
    assert not any(z["id"] == "none" for z in p["zones"])  # Knoten ohne Zone: eigene Ebene
    p = _plan(client, tid, hide_layer=["edge"], hide=["empty"])  # Kontakte bleiben
    assert any(z["id"] == "none" for z in p["zones"])

    # Elemente: Knoten und Namen der Uebergaenge nur in der Zeichnung weg, Kontakte ganz
    p = _plan(client, tid, hide=["nodes", "labels"])
    box = {b["title"]: b for b in p["layout"]["zones"]}
    assert box["Edge Internet"]["lines"] == [] and box["Edge Internet"]["h"] == 42
    assert {e["label"] for e in p["layout"]["edges"]} == {""}
    assert {e["name"] for e in p["layout"]["edges"] if not e["contact"]} == {
        "DC-EINET", "EINET-OACC", "EINET-PUB", "SRV-EINET"}  # fmt: skip
    assert client.get("/zones/plan", params={"tenant_id": tid, "hide_layer": "foo"}
                      ).status_code == 422  # fmt: skip

    # Export folgt dem Filter: nur die gezeigten Zonen, Filter im Kopf
    r = client.get("/zones/plan/export", params={"tenant_id": tid, "format": "svg", "q": "dc"})
    assert r.status_code == 200, r.text
    texts = [t.text or "" for t in ET.fromstring(r.content).iter(f"{SVG}text")]
    assert "DC" in texts and "Public" not in texts and "Office" not in texts
    assert any("Suche „dc“ + Nachbarn (2 von 5 Zonen)" in t for t in texts)
    r = client.get("/zones/plan/export", params={"tenant_id": tid, "format": "drawio",
                                                 "hide": ["labels"]})  # fmt: skip
    root = ET.fromstring(r.content)
    trans = [
        o.get("automato_transition")
        for o in root.iter("UserObject")
        if o.get("id", "").startswith("transition-") and o.get("automato_transition")
    ]
    assert "DC-EINET" in trans  # Name bleibt Eigenschaft, nur die Beschriftung faellt weg
    pdf = {"tenant_id": tid, "format": "pdf", "focus": [office]}
    assert client.get("/zones/plan/export", params=pdf).status_code == 200


def test_suche_kunden_benutzer_ohne_geraete():
    plan = {
        "format": 2,
        "zones": [
            {"id": 1, "name": "DC", "kind": "dc", "nodes": ["1|root"], "samples": ["10.0.0.0/24"]},
            {"id": 2, "name": "Edge", "kind": "edge", "nodes": []},
        ],
        "nodes": [{"id": "1|root", "label": "fw-zrh-01"}],
        "transitions": [{"id": "1~2", "a": 1, "b": 2, "name": "DC-EDGE", "networks": []}],
    }
    f = plan_view.Filter(q="fw-zrh", depth=0)
    assert [z["name"] for z in plan_view.apply(plan, f)["zones"]] == ["DC"]
    assert plan_view.apply(plan, f, devices=False)["zones"] == []  # kein Raten von Geraetenamen
    f = plan_view.Filter(q="10.0.0", depth=0)
    assert plan_view.apply(plan, f, devices=False)["zones"] == []
    # Kontakte (Knoten ohne Zone an Netzen einer Zone) ausblenden
    plan["zones"].append({"id": "none", "name": "Knoten ohne Zone", "kind": "none",
                          "nodes": ["2|x"]})  # fmt: skip
    plan["transitions"].append({"id": "1~none", "a": 1, "b": "none", "name": "DC-none",
                                "networks": [], "contact": True})  # fmt: skip
    f = plan_view.Filter(focus=["1"], hide={"contacts"})
    out = plan_view.apply(plan, f)
    assert [t["id"] for t in out["transitions"]] == ["1~2"]
    assert {z["name"] for z in out["zones"]} == {"DC", "Edge"}
    assert {z["name"] for z in plan_view.apply(plan, plan_view.Filter(focus=["1"]))["zones"]} == {
        "DC", "Edge", "Knoten ohne Zone"}  # fmt: skip
    f = plan_view.Filter(focus=["1"], depth=5, hide_layers={"x", "edge"}, hide={"labels", "y"})
    assert (f.depth, f.hide_layers, f.hide) == (3, {"edge"}, {"labels"})
    assert plan_view.describe(f, {"1": {"name": "DC"}}) == (
        "Fokus: DC + 3 Schritte · ohne Edge, Namen der Übergänge")  # fmt: skip


def test_layout_mit_positionen_und_linien():
    plan = {
        "format": 2,
        "zones": [
            {"id": 1, "name": "A", "kind": "dc", "nodes": []},
            {"id": 2, "name": "B", "kind": "edge", "nodes": []},
            {"id": 3, "name": "C", "kind": "access", "nodes": []},
        ],
        "nodes": [],
        "transitions": [
            {"id": "1~2", "a": 1, "b": 2, "name": "A-B"},
            {"id": "2~3", "a": 2, "b": 3, "name": "B-C"},
        ],
        "positions": {"1": [500, 200], "2": [520, 400]},
    }
    lay = plan_export.layout(plan)
    box = {b["title"]: b for b in lay["zones"]}
    # gezogene Zonen an ihrem Platz (an den Rand gerueckt), C in ihrer Spalte darunter
    assert box["A"]["placed"] and box["B"]["placed"] and not box["C"]["placed"]
    assert box["C"]["y"] > box["B"]["y"] + box["B"]["h"]
    assert lay["offset"] == [16 - 500, 12 - 200]  # C stand in ihrer Spalte bei x 776
    assert min(b["x"] for b in lay["zones"]) == 16 and min(b["y"] for b in lay["zones"]) == 12
    assert box["B"]["x"] - box["A"]["x"] == 20 and box["B"]["y"] - box["A"]["y"] == 200
    edges = {e["name"]: e for e in lay["edges"]}
    # A und B ueberlappen in x: rechts herum; B und C nebeneinander: von rechts nach links
    assert len(edges["A-B"]["points"]) == 4
    bend = edges["A-B"]["points"][1][0]
    assert bend > max(box["A"]["x"] + box["A"]["w"], box["B"]["x"] + box["B"]["w"])
    b_c = edges["B-C"]["points"]
    assert len(b_c) == 2
    left, right = sorted((box["B"], box["C"]), key=lambda b: b["x"])
    assert b_c[0][0] == left["x"] + left["w"] and b_c[1][0] == right["x"]
    assert lay["width"] >= max(b["x"] + b["w"] for b in lay["zones"]) + 16
    # ohne Positionen: Spalten nach Ebene wie bisher
    auto = plan_export.layout({**plan, "positions": {}})
    assert auto["offset"] == [0.0, 0.0]
    xs = [b["x"] for b in sorted(auto["zones"], key=lambda b: b["id"])]
    assert xs == sorted(xs) and len(set(xs)) == 3


def test_anordnung_speichern_export_versionen(client, session_factory):
    tid, ids = _seed(client)
    z = _model(client, tid, ids)
    full = _plan(client, tid)
    view = full["view"]["mode"]
    assert view == "all"  # keine Zonen eines Kunden: eine Ansicht
    assert full["arrangement"] == {"view": "all", "saved": False, "count": 0, "updated_by": None,
                                   "updated_at": None}  # fmt: skip
    dc, pub = str(z["dc"]["id"]), str(z["public"]["id"])
    body = {"tenant_id": tid, "view": view, "positions": {dc: [900, 40], pub: [16, 12]}}
    assert client.put("/zones/plan/arrangement", json=body, headers=VIEW).status_code == 403
    r = client.put("/zones/plan/arrangement", json=body, headers=OP)
    assert r.status_code == 200, r.text
    assert r.json()["saved"] and r.json()["count"] == 2 and r.json()["updated_by"] == "dev"
    # weitere Zone dazu: die gespeicherten bleiben
    more = {"tenant_id": tid, "view": view, "positions": {str(z["office"]["id"]): [400, 40]}}
    assert client.put("/zones/plan/arrangement", json=more, headers=OP).json()["count"] == 3

    p = _plan(client, tid)
    box = {b["title"]: b for b in p["layout"]["zones"]}
    assert (box["Public"]["x"], box["Public"]["y"]) == (16, 12)
    assert (box["DC"]["x"], box["DC"]["y"]) == (900, 40) and box["DC"]["placed"]
    assert box["Office"]["x"] == 400 and not box["Server"]["placed"]
    assert box["Server"]["y"] > max(b["y"] + b["h"] for b in p["layout"]["zones"] if b["placed"])
    assert set(p["positions"]) == {dc, pub, str(z["office"]["id"])}
    # der Filter behaelt die Anordnung und rueckt an den Rand
    q = _plan(client, tid, q="dc")
    fbox = {b["title"]: b for b in q["layout"]["zones"]}
    assert min(b["x"] for b in q["layout"]["zones"]) == 16  # Edge Internet in ihrer Spalte
    assert fbox["DC"]["x"] == 900 + q["layout"]["offset"][0] and q["layout"]["offset"][0] < 0
    # Export folgt der Anordnung
    r = client.get("/zones/plan/export", params={"tenant_id": tid, "format": "svg"})
    rects = [(t.get("x"), t.text) for t in ET.fromstring(r.content).iter(f"{SVG}text")]
    assert ("912", "DC") in rects
    # neue Version zeichnet mit der Anordnung, auch wenn sie spaeter zurueckgesetzt wird
    v = client.post("/zones/plan/versions", json={"tenant_id": tid, "label": "angeordnet"},
                    headers=OP).json()  # fmt: skip
    listed = client.get("/zones/plan/versions", params={"tenant_id": tid}).json()
    assert listed["current_fingerprint"] == v["fingerprint"]

    # ungueltig: Ansicht, Koordinaten, Form
    bad = [
        {**body, "view": "foo"},
        {**body, "positions": {dc: [1e9, 0]}},
        {**body, "positions": {dc: [1, 2, 3]}},
        {**body, "positions": {"x" * 65: [1, 2]}},
    ]
    for b in bad:
        assert client.put("/zones/plan/arrangement", json=b, headers=OP).status_code == 422, b

    # zuruecksetzen: wieder nach Ebenen
    url = "/zones/plan/arrangement"
    assert client.delete(url, params={"tenant_id": tid, "view": view}, headers=VIEW).status_code \
        == 403  # fmt: skip
    assert client.delete(url, params={"tenant_id": tid, "view": view}, headers=OP).status_code \
        == 204  # fmt: skip
    p = _plan(client, tid)
    assert not p["arrangement"]["saved"] and not any(b["placed"] for b in p["layout"]["zones"])
    assert p["layout"]["zones"] == full["layout"]["zones"]
    old = client.get(f"/zones/plan/versions/{v['id']}").json()["data"]["plan"]["layout"]
    assert {b["title"]: (b["x"], b["y"]) for b in old["zones"]}["DC"] == (900, 40)
    # Audit
    s = session_factory()
    actions = {a.action for a in s.query(AuditLog).filter(AuditLog.action.like("zone_plan.%"))}
    assert {"zone_plan.arranged", "zone_plan.arrangement_reset"} <= actions

    # Kunden-Benutzer: lesen mit Filter, nicht anordnen
    cust = client.post("/customers", json={"tenant_id": tid, "name": "Kunde X", "selectors": []},
                       headers=OP).json()  # fmt: skip
    who = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid), "X-Dev-Customers": str(cust["id"])}
    r = client.get("/zones/plan", params={"tenant_id": tid, "q": "fw"}, headers=who)
    assert r.status_code == 200 and "arrangement" not in r.json()
    assert client.put(url, json=body, headers=who).status_code == 403

    # Mandant loeschen: Anordnung weg
    client.put(url, json=body, headers=OP)
    assert client.delete(f"/tenants/{tid}?confirm=Kunde A", headers=ADMIN).status_code == 200
    s.expire_all()
    assert s.query(ZonePlanArrangement).count() == 0
    s.close()
