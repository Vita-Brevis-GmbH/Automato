# ruff: noqa: S314 - geparst wird nur die eigene Ausgabe des Exports
"""Zonen, Zonenplan und Grundschema auf Englisch (0.87.0, ADR 0028 Schritt 2/3).

Stichproben: Beschriftungen des Plans und der Exporte (SVG, PDF, draw.io), Filtertext, Gruende
der Vorschlaege und des Grundschemas, Fehlermeldungen. Dazu, was sprachunabhaengig bleiben muss:
der Export laesst sich in beiden Sprachen wieder einlesen (Zonenplan und Grundschema), gespeichert
wird deutsch (Version, Hinweise des Grundschemas, Notiz der Absicht), der Fingerabdruck einer
Version haengt nicht an der Sprache.
"""

import xml.etree.ElementTree as ET
from datetime import datetime

import pytest
from app.i18n import use
from app.store import plan_export, plan_view, zone_assist, zone_schema, zone_services, zones
from test_customer_zones import _as, _owned
from test_plan_export import _pdf_objects, _pdf_text
from test_zone_detect import _setup as _detect_setup
from test_zone_schema import DRAWING
from test_zones import ADMIN, OP, VIEW, _model, _seed

EN = {"Accept-Language": "en"}
SVG = "{http://www.w3.org/2000/svg}"

_LIMITED = {"intent": "limited", "services": ["tcp/443"], "pairs": 2, "mixed": True}
_DENY = {"intent": "deny", "services": [], "pairs": 1, "mixed": False}
PLAN = {
    "format": 2,
    "view": {"mode": "all"},
    "zones": [
        {"id": 1, "name": "DC", "kind": "dc", "nets": 1, "nodes": [f"{i}|root" for i in range(8)]},
        {"id": 2, "name": "Edge", "kind": "edge", "nets": 2, "nodes": []},
        {"id": "customer:7", "name": "Kunde C", "kind": "customer", "collapsed": True,
         "members": ["a", "b"], "nodes": []},
        {"id": "none", "name": "Knoten ohne Zone", "kind": "none", "nodes": ["1|root"]},
        {"id": "outside", "name": "ausserhalb (nicht gelesen)", "kind": "outside", "nodes": []},
    ],
    "nodes": [{"id": f"{i}|root", "label": f"fw-{i}"} for i in range(8)],
    "transitions": [{"id": "1~2", "a": 1, "b": 2, "name": "DC-EINET", "count": 1,
                     "contact": False, "networks": [], "intent_ab": _LIMITED,
                     "intent_ba": _DENY}],
    "intents": [{"id": "intent:2~outside", "a": 2, "b": "outside", "intent_ab": _DENY}],
    "flows": [{"id": "flow:1~2", "a": 1, "b": 2, "rules": 3, "ab": 2, "ba": 1}],
}  # fmt: skip
MATRIX = {
    "zones": [{"id": 1, "name": "DC"}, {"id": 2, "name": "Edge"}],
    "axes": [1, 2, "none"],
    "cells": [{"src": 1, "dst": 2, "allow": 1, "deny": 0, "violations": 1, "intent": "deny",
               "note": "nie"}],
    "summary": {"assigned": 1, "rules": 2, "violations": 1},
}  # fmt: skip
WHEN = datetime(2026, 10, 27, 12, 30)


def _subs(plan: dict) -> dict[str, str]:
    return {b["title"]: b["sub"] for b in plan_export.layout(plan)["zones"]}


def test_zonenplan_beschriftungen():
    with use("en"):
        lay = plan_export.layout(PLAN)
        subs = _subs(PLAN)
        rows = plan_export.intent_rows(PLAN)
        svg = plan_export.svg(PLAN, "Zone plan X", WHEN, {"number": 2, "label": "A"})
    assert subs["DC"] == "Data center · 1 network" and subs["Edge"] == "Edge · 2 networks"
    assert subs["Kunde C"] == "Customer · 2 zones" and subs["Knoten ohne Zone"] == "1 node"
    assert subs["ausserhalb (nicht gelesen)"] == "Router without a read device"
    dc = next(b for b in lay["zones"] if b["title"] == "DC")
    assert dc["lines"][-1] == "… 3 more"
    edges = {e["id"]: e for e in lay["edges"]}
    assert edges["1~2"]["intent_text"] == (
        "DC → Edge: limited (tcp/443), partly less strict · Edge → DC: deny"
    )
    assert edges["flow:1~2"]["label"] == "3 rules"
    assert edges["flow:1~2"]["name"] == "DC → Edge: 2, Edge → DC: 1 allowing rules"
    assert edges["intent:2~outside"]["label"] == "deny"
    assert ["DC", "Edge", "limited (partly)", "tcp/443", "DC-EINET"] in rows
    assert {r[-1] for r in rows if r[0] == "Edge" and r[2] == "deny"} == {
        "DC-EINET", "without transition"}  # fmt: skip
    texts = [t.text for t in ET.fromstring(svg).iter(f"{SVG}text")]
    assert "Zone plan · Version 2 – A · As of 27/10/2026 12:30" in texts
    titles = [t.text for t in ET.fromstring(svg).iter(f"{SVG}title")]
    assert "Intent Edge → ausserhalb (nicht gelesen): deny" in titles
    assert any(t.startswith("DC-EINET:  – intent DC → Edge: limited") for t in titles)
    # Deutsch unveraendert
    de = _subs(PLAN)
    assert de["DC"] == "Datacenter · 1 Netz" and de["Kunde C"] == "Kunde · 2 Zonen"
    rows = plan_export.intent_rows(PLAN)
    assert ["DC", "Edge", "eingeschränkt (teils)", "tcp/443", "DC-EINET"] in rows


def test_pdf_englisch():
    with use("en"):
        text = _pdf_text(_pdf_objects(plan_export.pdf(PLAN, MATRIX, "Zone plan X", WHEN)))
    for part in ("Zone matrix", "1 of 2 rules assigned · 1 against the intent", "Zones",
                 "Networks in the zone", "Transitions", "Intent per direction", "From",
                 "DC -> Edge: deny – nie", "no zone", "Data center"):  # fmt: skip
        assert part in text, part
    assert "Zonenmatrix" not in text and "Übergänge" not in text
    de = _pdf_text(_pdf_objects(plan_export.pdf(PLAN, MATRIX, "Zonenplan X", WHEN)))
    assert "Zonenmatrix" in de and "1 von 2 Regeln zugeordnet" in de and "Stand 27.10.2026" in de


def test_filtertext():
    f = plan_view.Filter(q="dc", depth=1, hide_layers={"none"}, hide={"labels"}, flows=True)
    assert plan_view.describe(f) == ("Suche „dc“ + Nachbarn · mit Kommunikation (Regeln) · ohne "
                                     "Knoten ohne Zone, Namen der Übergänge")  # fmt: skip
    with use("en"):
        assert plan_view.describe(f) == (
            "Search “dc” + neighbors · with communication (rules) · without nodes without zone, "
            "names of the transitions")  # fmt: skip
        g = plan_view.Filter(focus=["1"], depth=2)
        assert plan_view.describe(g, {"1": {"name": "DC"}}) == "Focus: DC + 2 steps"


def test_hilfszonen_und_anderer_kunde():
    assert zones.pseudo_name("none") == "Knoten ohne Zone" and zones.other_name() == "anderer Kunde"
    with use("en"):
        assert zones.pseudo_name("none") == "Nodes without zone"
        assert zones.pseudo_name("outside") == "outside (not read)"
        assert zones.other_name() == "other customer"
        assert zones.pseudo_name(5) == "5"


def test_dienste_und_fehler():
    rs = zone_services.RuleServices({1: [("tcp", 22, 22, "ssh")]}, {1})
    allow = zone_services.Allowed(["tcp/443"], [("tcp", 443, 443)])
    assert rs.outside(1, allow) == ["alle ausser ssh"]
    with use("en"):
        assert rs.outside(1, allow) == ["all except ssh"]
        for bad, msg in (("<x/>", "not a draw.io drawing"), ("kein xml", "not valid XML"),
                         ("<mxGraphModel><root><mxCell id='0'/></root></mxGraphModel>",
                          "no zones found")):  # fmt: skip
            with pytest.raises(zone_schema.SchemaError, match=msg):
                zone_schema.parse(bad)


def test_grundschema_vorlage_in_beiden_sprachen():
    base = zone_schema.default_schema()
    base.zones[1].per_customer = True
    de = zone_schema.drawing(base, "Zonenschema")
    with use("en"):
        en = zone_schema.drawing(base, "Zone schema")
    assert 'automato_kind="Data center"' in en and 'automato_customer="per customer"' in en
    assert "Each shape = zone" in en and 'name="Zone schema"' in en
    assert 'automato_kind="extern"' in de and "Jede Form = Zone" in de
    want = [(z.name, z.kind, z.code, z.per_customer) for z in base.zones]
    links = {(x.src, x.dst, x.intent) for x in base.links}
    for content in (de, en):
        for code in ("de", "en"):
            with use(code):
                s = zone_schema.parse(content)
            assert [(z.name, z.kind, z.code, z.per_customer) for z in s.zones] == want
            assert {(x.src, x.dst, x.intent) for x in s.links} == links and not s.warnings
    # Gruende: gespeichert deutsch, angezeigt in der Sprache der Anfrage
    s = zone_schema.parse(DRAWING)
    inet = s.zone("Internet")
    assert inet.kind_reason == "Eigenschaft automato_kind"
    assert inet.out(raw=True)["kind_reason"] == "Eigenschaft automato_kind"
    with use("en"):
        assert inet.out()["kind_reason"] == "property automato_kind"
        assert inet.out(raw=True)["kind_reason"] == "Eigenschaft automato_kind"
        assert s.zone("OOB-Netz").out()["kind_reason"] == "name “oob”"
        reasons = {(x["src"], x["dst"]): x["reason"] for x in s.out()["links"]}
        assert reasons[("Internet", "Server")] == "label"
        assert reasons[("Internet", "DMZ")] == "dashed"
        assert reasons[("DMZ", "OOB-Netz")] == "line color"
        warnings = zone_schema.parse(DRAWING).warnings
    assert any("2 pages" in w for w in warnings) and any("twice" in w for w in warnings)
    assert any("1 connection(s) not between two zones" in w for w in warnings)


def test_vorschlaege_beim_erfassen():
    m = zone_assist.Matcher("Edge Internet", "EINET", "external", None)
    assert m.test("edge-internet").reason == "gleicher Name „edge-internet“"
    with use("en"):
        assert m.test("edge-internet").reason == "same name “edge-internet”"
        assert m.test("x-einet-1").reason == "short name in “x-einet-1”"
        assert m.role("wan").reason == "role wan"


def _drawio(client, tid, lang: str) -> str:
    r = client.get("/zones/plan/export", params={"tenant_id": tid, "format": "drawio",
                                                 "lang": lang})  # fmt: skip
    assert r.status_code == 200, r.text
    return r.text


def _preview(client, tid, content: str, lang: str) -> dict:
    r = client.post("/zones/plan/import/preview", json={"tenant_id": tid, "content": content},
                    headers={**OP, "Accept-Language": lang})  # fmt: skip
    assert r.status_code == 200, r.text
    return r.json()


def test_zonenplan_api_und_rundlauf(client):
    tid, ids = _seed(client)
    z = _model(client, tid, ids)
    plan = client.get("/zones/plan", params={"tenant_id": tid}, headers=EN).json()
    assert {x["id"]: x["name"] for x in plan["zones"]}["none"] == "Nodes without zone"
    box = {b["title"]: b for b in plan["layout"]["zones"]}
    assert box["DC"]["sub"] == "Data center · 1 network"
    de = client.get("/zones/plan", params={"tenant_id": tid}).json()
    assert {x["id"]: x["name"] for x in de["zones"]}["none"] == "Knoten ohne Zone"

    # Rundlauf: Export in einer Sprache, Einlesen in der anderen - nichts zu tun, keine Hinweise
    kinds = {}
    for exp in ("de", "en"):
        content = _drawio(client, tid, exp)
        for imp in ("de", "en"):
            out = _preview(client, tid, content, imp)
            assert (out["renames"], out["intents"], out["new_zones"], out["moved"],
                    out["warnings"]) == ([], [], [], [], []), (exp, imp)  # fmt: skip
            assert out["view"] == "all" and out["positions"] >= 5
        # als Grundschema einlesbar: dieselben Zonen und Arten in beiden Sprachen
        kinds[exp] = [(s.name, s.kind) for s in zone_schema.parse(content).zones]
    assert kinds["de"] == kinds["en"] and ("DC", "dc") in kinds["en"]

    # neuer Pfeil mit englischer Beschriftung in der englischen Datei: Absicht erkannt
    mx = ET.fromstring(_drawio(client, tid, "en"))
    assert mx.find("diagram").get("name") == "Zone plan"
    root = mx.find("diagram/mxGraphModel/root")
    title = root.find("UserObject[@id='title']")
    assert title.get("label").startswith("<b>Zone plan Kunde A</b><br>Zone plan · As of ")
    line = ET.SubElement(root, "mxCell", {
        "id": "new-1", "value": "denied", "edge": "1", "parent": "1", "style": "endArrow=classic;",
        "source": f"zone-{z['public']['id']}", "target": f"zone-{z['office']['id']}",
    })  # fmt: skip
    ET.SubElement(line, "mxGeometry", {"relative": "1", "as": "geometry"})
    title.set("automato_view", "nirgends")
    content = ET.tostring(mx, encoding="unicode")
    out = _preview(client, tid, content, "en")
    ((intent,), warnings) = out["intents"], out["warnings"]
    assert (intent["src"], intent["dst"], intent["intent"], intent["reason"]) == (
        "Public", "Office", "deny", "label")  # fmt: skip
    assert warnings == ["View “nirgends” unknown - arrangement for all zones"]
    assert _preview(client, tid, content, "de")["intents"][0]["reason"] == "Beschriftung"
    # uebernehmen auf Englisch: die Notiz wird deutsch gespeichert
    body = {"tenant_id": tid, "content": content, "positions": False}
    r = client.post("/zones/plan/import", json=body, headers={**OP, **EN})
    assert r.status_code == 200, r.text
    m = client.get("/zones/matrix", params={"tenant_id": tid}, headers=EN).json()
    pair = (z["public"]["id"], z["office"]["id"])
    (p,) = (x for x in m["intents"] if (x["src"], x["dst"]) == pair)
    assert (p["intent"], p["note"]) == ("deny", "Zonenplan (draw.io): denied")

    # Absicht mit falschem Dienst: Meldung auf Englisch
    body = {"tenant_id": tid, "src_zone_id": z["dc"]["id"], "dst_zone_id": z["office"]["id"],
            "intent": "limited", "services": ["quatsch"]}  # fmt: skip
    r = client.put("/zones/policy", json=body, headers={**OP, **EN})
    assert r.status_code == 422 and r.json()["detail"].startswith("Service “quatsch”: tcp/443")
    r = client.put("/zones/policy", json=body, headers=OP)
    assert r.json()["detail"].startswith("Dienst „quatsch“: tcp/443")


def test_versionen_und_exporte_englisch(client):
    tid, ids = _seed(client)
    _model(client, tid, ids)
    # gespeichert wird deutsch: Fingerabdruck in beiden Sprachen gleich
    v = client.post("/zones/plan/versions", json={"tenant_id": tid, "label": "A"},
                    headers={**OP, **EN}).json()  # fmt: skip
    for headers in (EN, VIEW):
        listed = client.get("/zones/plan/versions", params={"tenant_id": tid}, headers=headers)
        assert listed.json()["current_fingerprint"] == v["fingerprint"]
    full = client.get(f"/zones/plan/versions/{v['id']}", headers=EN).json()
    plan = full["data"]["plan"]
    assert "Knoten ohne Zone" in {x["name"] for x in plan["zones"]}  # Stand der Version
    assert "Data center · 1 network" in {b["sub"] for b in plan["layout"]["zones"]}
    diff = client.get(f"/zones/plan/versions/{v['id']}/diff", headers=EN).json()
    assert diff["same"]

    r = client.get("/zones/plan/export", params={"tenant_id": tid, "format": "svg",
                                                 "lang": "en"})  # fmt: skip
    texts = [t.text or "" for t in ET.fromstring(r.content).iter(f"{SVG}text")]
    assert "Zone plan Kunde A" in texts and any(t.startswith("Zone plan · As of ") for t in texts)
    assert 'filename="zonenplan-' in r.headers["content-disposition"]  # Dateiname bleibt
    r = client.get("/zones/plan/export", params={"tenant_id": tid, "format": "pdf",
                                                 "lang": "en"})  # fmt: skip
    text = _pdf_text(_pdf_objects(r.content))
    assert "Zone matrix" in text and "Transitions" in text and "Zonenmatrix" not in text


def test_kunden_benutzer_englisch(client):
    tid, ids = _seed(client)
    _z, a, _b = _owned(client, tid, ids)
    ua = {**_as(tid, a["id"]), **EN}
    plan = client.get("/zones/plan", params={"tenant_id": tid}, headers=ua).json()
    assert {x["name"] for x in plan["zones"]} == {"Server", "other customer"}
    de = client.get("/zones/plan", params={"tenant_id": tid}, headers=_as(tid, a["id"])).json()
    assert {x["name"] for x in de["zones"]} == {"Server", "anderer Kunde"}
    r = client.get("/zones/plan/export", params={"tenant_id": tid, "format": "svg"}, headers=ua)
    texts = [t.text for t in ET.fromstring(r.content).iter(f"{SVG}text")]
    assert "Zone plan Kunde A" in texts and "other customer" in texts


def test_grundschema_api_englisch(client):
    pid = client.post("/naming-profiles", json={"customer": "Kunde A", "name": "Std"},
                      headers=ADMIN).json()["id"]  # fmt: skip
    url = f"/naming-profiles/{pid}/zone-schema"
    body = {"filename": "zonen.drawio", "content": DRAWING}
    admin_en = {**ADMIN, **EN}
    p = client.post(f"{url}/preview", json=body, headers=admin_en).json()
    assert any("2 pages" in w for w in p["schema"]["warnings"])
    bad = client.post(f"{url}/preview", json={"content": "<x/>"}, headers=admin_en)
    assert bad.status_code == 422 and "not a draw.io drawing" in bad.json()["detail"]
    # speichern auf Englisch: Hinweise und Gruende deutsch gespeichert, angezeigt je Sprache
    got = client.put(url, json=body, headers=admin_en).json()
    assert any("2 Seiten" in w for w in got["schema"]["warnings"])
    zone = {z["name"]: z for z in got["schema"]["zones"]}
    assert zone["Internet"]["kind_reason"] == "property automato_kind"
    de = client.get(url, headers=VIEW).json()
    assert {z["name"]: z for z in de["schema"]["zones"]}["Internet"]["kind_reason"] == (
        "Eigenschaft automato_kind")  # fmt: skip
    links = {(x["src"], x["dst"]): x["reason"] for x in de["schema"]["links"]}
    assert links[("Internet", "Server")] == "Beschriftung"
    # Zeichnung aus dem Stand bzw. Vorlage auf Englisch - wieder einlesbar
    tpl = client.get("/naming-profiles/zone-schema/template", headers={**VIEW, **EN})
    assert "Zone schema (template)" in tpl.text and 'automato_kind="Data center"' in tpl.text
    assert len(zone_schema.parse(tpl.text).zones) == 12
    # in der Oberflaeche bearbeiten: Fehler auf Englisch
    edit = {"zones": [{"name": "A", "color": "rot"}], "links": []}
    r = client.patch(url, json=edit, headers=admin_en)
    assert r.status_code == 422 and r.json()["detail"] == (
        "Zone “A”: color “rot” is not a hex value (#rrggbb)")  # fmt: skip
    r = client.patch(url, json=edit, headers=ADMIN)
    assert r.json()["detail"] == "Zone „A“: Farbe „rot“ ist kein Hex-Wert (#rrggbb)"


def test_vorschlaege_api_englisch(client):
    tid, ids = _detect_setup(client)
    view_en = {**VIEW, **EN}
    r = client.get("/zones/suggestions", params={"tenant_id": tid}, headers=view_en)
    s = {x["key"]: x for x in r.json()["items"]}
    edge = s["edge-internet"]  # Name bleibt (wird beim Uebernehmen gespeichert)
    assert (edge["name"], edge["code"]) == ("Edge Internet", "EINET")
    assert edge["kind_reason"] == "Base schema “Edge Internet”"
    assert edge["evidence"] == ["Firewall fw-01 · VDOM root: Exit Internet via wan1"]
    assert s[f"dc:{ids['customer']}"]["name"] == "DC Kunde TE"
    assert s["backup"]["evidence"] == ["IPAM Zone=Backup (1 entry)"]
    # uebernehmen auf Englisch: die Beschreibung der Zone wird deutsch gespeichert
    body = {"tenant_id": tid, "keys": ["edge-internet"]}
    r = client.post("/zones/suggestions/accept", json=body, headers={**OP, **EN})
    assert r.status_code == 200, r.text
    zone = next(z for z in client.get("/zones", params={"tenant_id": tid}).json()
                if z["name"] == "Edge Internet")  # fmt: skip
    assert zone["description"] == "Vorschlag aus Firewall"

    # Vorschlaege beim Erfassen
    r = client.post("/zones/assist", json={"tenant_id": tid, "name": "DMZ", "kind": "dmz"},
                    headers=view_en)  # fmt: skip
    items = {(i["selector"]["kind"], i["selector"]["value"]): i for i in r.json()["items"]}
    mail = items[("fw_interface", "port4")]
    assert mail["source_label"] == "Firewall interface"
    assert mail["reason"] == "name in “DMZ-Mail”"
    assert items[("fw_interface", "port3")]["reason"] == "role dmz"
