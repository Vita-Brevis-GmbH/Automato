# ruff: noqa: S314 - geparst wird nur die eigene Ausgabe des Exports
"""Zonenplan aus draw.io zurueck einlesen (0.83.0, ADR 0024 Nr. 20).

Export des Zonenplans, in der Datei Zone verschoben, umbenannt, neuer Pfeil und neue Form - die
Vorschau zeigt es, Uebernehmen setzt Anordnung, Name, Absicht und (auf Wunsch) neue Zonen. Die
Linien des Exports selbst sind keine neue Absicht.
"""

import xml.etree.ElementTree as ET

from test_zones import OP, VIEW, _model, _seed


def _drawing(client, tid) -> ET.Element:
    r = client.get("/zones/plan/export", params={"tenant_id": tid, "format": "drawio"})
    assert r.status_code == 200, r.text
    return ET.fromstring(r.content)


def _text(mx: ET.Element) -> str:
    return ET.tostring(mx, encoding="unicode")


def _box(plan: dict, title: str) -> dict:
    return next(b for b in plan["layout"]["zones"] if b["title"] == title)


def _preview(client, tid, content, headers=OP):
    return client.post("/zones/plan/import/preview", json={"tenant_id": tid, "content": content},
                       headers=headers)  # fmt: skip


def test_lage_name_absicht_und_neue_zone(client):
    tid, ids = _seed(client)
    z = _model(client, tid, ids)
    before = client.get("/zones/plan", params={"tenant_id": tid}).json()
    mx = _drawing(client, tid)
    root = mx.find("diagram/mxGraphModel/root")
    title = root.find("UserObject[@id='title']")
    assert title.get("automato_view") == "all" and title.get("automato_offset")
    # unveraendert: nichts zu tun, alle Zonen an ihrem Platz
    out = _preview(client, tid, _text(mx)).json()
    assert (out["renames"], out["intents"], out["new_zones"], out["moved"]) == ([], [], [], [])
    assert out["view"] == "all" and out["positions"] >= 5

    # in draw.io: DC nach rechts, Office umbenannt, Pfeil Public -> Office „gesperrt“, neue Form
    dc = root.find(f"UserObject[@id='zone-{z['dc']['id']}']/mxCell/mxGeometry")
    dc.set("x", str(float(dc.get("x")) + 300))
    office = root.find(f"UserObject[@id='zone-{z['office']['id']}']")
    office.set("label", "<b>Office Zürich</b><br><i>Access</i>")
    edge = ET.SubElement(root, "mxCell", {
        "id": "neu-1", "value": "gesperrt", "edge": "1", "parent": "1",
        "style": "endArrow=classic;", "source": f"zone-{z['public']['id']}",
        "target": f"zone-{z['office']['id']}",
    })  # fmt: skip
    ET.SubElement(edge, "mxGeometry", {"relative": "1", "as": "geometry"})
    shape = ET.SubElement(root, "mxCell", {"id": "neu-2", "value": "Labor", "vertex": "1",
                                           "parent": "1", "style": "rounded=1;"})  # fmt: skip
    ET.SubElement(shape, "mxGeometry", {"x": "10", "y": "10", "width": "100", "height": "40",
                                        "as": "geometry"})  # fmt: skip
    content = _text(mx)

    assert _preview(client, tid, content, VIEW).status_code == 403
    out = _preview(client, tid, content).json()
    assert str(z["dc"]["id"]) in out["moved"]
    assert out["renames"] == [{"id": z["office"]["id"], "from": "Office", "to": "Office Zürich"}]
    (intent,) = out["intents"]
    assert (intent["src"], intent["dst"], intent["intent"], intent["current"]) == (
        "Public", "Office", "deny", None)  # fmt: skip
    assert out["new_zones"] == [{"name": "Labor", "kind": "internal", "code": None}]

    r = client.post("/zones/plan/import", json={"tenant_id": tid, "content": content}, headers=OP)
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["positions"] == out["positions"] and done["new_zones"] == []
    assert [x["to"] for x in done["renames"]] == ["Office Zürich"]
    after = client.get("/zones/plan", params={"tenant_id": tid}).json()
    assert after["arrangement"]["saved"]
    # DC liegt 300 Pixel weiter rechts als vorher, gemessen an Public
    shift = (_box(after, "DC")["x"] - _box(after, "Public")["x"]) - (
        _box(before, "DC")["x"] - _box(before, "Public")["x"])  # fmt: skip
    assert abs(shift - 300) < 1
    names = {x["name"] for x in client.get("/zones", params={"tenant_id": tid}).json()}
    assert "Office Zürich" in names and "Labor" not in names
    m = client.get("/zones/matrix", params={"tenant_id": tid}).json()
    pair = (z["public"]["id"], z["office"]["id"])
    (p,) = (x for x in m["intents"] if (x["src"], x["dst"]) == pair)
    assert p["intent"] == "deny" and p["note"] == "Zonenplan (draw.io): gesperrt"

    # dieselbe Datei nochmals: Name und Absicht sind schon so
    again = _preview(client, tid, content).json()
    assert again["renames"] == [] and again["intents"] == [] and again["moved"] == []
    # neue Zonen nur auf Wunsch
    body = {"tenant_id": tid, "content": content, "new_zones": True}
    assert client.post("/zones/plan/import", json=body, headers=OP).json()["new_zones"] == ["Labor"]
    names = {x["name"] for x in client.get("/zones", params={"tenant_id": tid}).json()}
    assert "Labor" in names


def test_seit_dem_export_umbenannt_und_fehler(client):
    tid, ids = _seed(client)
    z = _model(client, tid, ids)
    mx = _drawing(client, tid)
    root = mx.find("diagram/mxGraphModel/root")
    root.find(f"UserObject[@id='zone-{z['office']['id']}']").set("label", "<b>Office Bern</b>")
    # die Linie eines Uebergangs aus dem Export bleibt ohne automato_intent keine Absicht
    line = next(o for o in root.iter("UserObject") if o.get("automato_transition"))
    line.find("mxCell").set("style", "endArrow=classic;strokeColor=#FF0000;")
    # in Automato inzwischen umbenannt: der Name aus der Zeichnung gilt nicht
    zone = next(x for x in client.get("/zones", params={"tenant_id": tid}).json()
                if x["id"] == z["office"]["id"])  # fmt: skip
    body = {k: zone[k] for k in ("name", "code", "kind", "customer_id", "position", "description")}
    body |= {"tenant_id": tid, "name": "Office Basel", "selectors": zone["selectors"]}
    assert client.put(f"/zones/{zone['id']}", json=body, headers=OP).status_code == 200
    out = _preview(client, tid, _text(mx)).json()
    assert out["renames"] == [] and out["intents"] == []
    assert any("heisst in Automato inzwischen „Office Basel“" in w for w in out["warnings"])
    # mit automato_intent wird dieselbe Linie eine Absicht (Richtung nach dem Pfeil)
    line.set("automato_intent", "deny")
    out = _preview(client, tid, _text(mx)).json()
    assert [x["intent"] for x in out["intents"]] == ["deny"]

    assert _preview(client, tid, "<x/>").status_code == 422
    dtd = '<!DOCTYPE x [<!ENTITY a "b">]><mxfile/>'
    assert _preview(client, tid, dtd).status_code == 422
