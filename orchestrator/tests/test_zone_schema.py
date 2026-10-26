"""Grundschema der Zonen aus einer draw.io-Zeichnung (0.55.0, ADR 0024 Nr. 11).

Zeichnung lesen (unkomprimiert und komprimiert, Eigenschaften, Gruppen, Pfeile mit Absicht aus
Eigenschaft, Beschriftung, Strichart und Farbe), Fehlerfaelle, Vorlage hin und zurueck; API im
Namensprofil; Vorschlaege und Absichten nach dem Schema.
"""

import base64
import zlib
from urllib.parse import quote

import pytest
from app.store import plan_export, zone_schema
from app.store.zone_schema import SchemaError, parse
from test_zone_detect import _by_key, _get, _sels, _setup
from test_zones import ADMIN, OP, VIEW, _sel, _zone

DRAWING = """<mxfile host="drawio"><diagram id="a" name="Netz"><mxGraphModel><root>
<mxCell id="0"/><mxCell id="1" parent="0"/>
<mxCell id="t" value="Legende" style="text;html=1;" vertex="1" parent="1">
  <mxGeometry x="0" y="0" width="100" height="20" as="geometry"/></mxCell>
<object id="inet" label="&lt;b&gt;Internet&lt;/b&gt;&lt;br&gt;alles draussen" automato_kind="extern"
        automato_match="inet*, Untrust*">
  <mxCell style="ellipse;fillColor=#F8CECC;" vertex="1" parent="1">
    <mxGeometry x="10" y="100" width="120" height="60" as="geometry"/></mxCell></object>
<mxCell id="dmz" value="DMZ" style="rounded=1;fillColor=#fc0;" vertex="1" parent="1">
  <mxGeometry x="200" y="100" width="120" height="60" as="geometry"/></mxCell>
<mxCell id="rz" value="Rechenzentrum" style="swimlane;" vertex="1" parent="1">
  <mxGeometry x="400" y="80" width="300" height="300" as="geometry"/></mxCell>
<object id="srv" label="Server" automato_code="SRV" automato_match="srv*">
  <mxCell style="rounded=1;" vertex="1" parent="rz">
    <mxGeometry x="20" y="40" width="120" height="60" as="geometry"/></mxCell></object>
<mxCell id="mgmt" value="OOB-Netz" style="rounded=1;" vertex="1" parent="rz">
  <mxGeometry x="20" y="160" width="120" height="60" as="geometry"/></mxCell>
<object id="node" label="fw-01" automato_node="fw-01">
  <mxCell style="rounded=1;" vertex="1" parent="1">
    <mxGeometry x="800" y="100" width="120" height="60" as="geometry"/></mxCell></object>
<mxCell id="dup" value="dmz" style="rounded=1;" vertex="1" parent="1">
  <mxGeometry x="200" y="400" width="120" height="60" as="geometry"/></mxCell>
<mxCell id="e1" value="gesperrt" style="endArrow=classic;" edge="1" parent="1" source="inet"
        target="srv"><mxGeometry relative="1" as="geometry"/></mxCell>
<mxCell id="e2" value="" style="endArrow=classic;dashed=1;" edge="1" parent="1" source="inet"
        target="dmz"><mxGeometry relative="1" as="geometry"/></mxCell>
<mxCell id="e3" value="" style="endArrow=classic;strokeColor=#FF0000;" edge="1" parent="1"
        source="dmz" target="mgmt"><mxGeometry relative="1" as="geometry"/></mxCell>
<mxCell id="e4" value="" style="endArrow=none;" edge="1" parent="1" source="dmz" target="srv">
  <mxGeometry relative="1" as="geometry"/></mxCell>
<mxCell id="e4l" value="nur https" style="edgeLabel;" vertex="1" connectable="0" parent="e4">
  <mxGeometry relative="1" as="geometry"/></mxCell>
<object id="e5" label="" automato_intent="allow">
  <mxCell style="endArrow=none;startArrow=classic;" edge="1" parent="1" source="srv"
          target="mgmt"><mxGeometry relative="1" as="geometry"/></mxCell></object>
<mxCell id="e6" value="" style="endArrow=classic;" edge="1" parent="1" source="srv" target="node">
  <mxGeometry relative="1" as="geometry"/></mxCell>
</root></mxGraphModel></diagram>
<diagram id="b" name="Alt"><mxGraphModel><root><mxCell id="0"/></root></mxGraphModel></diagram>
</mxfile>"""


def _links(schema) -> dict[tuple[str, str], tuple[str, str]]:
    return {(x.src, x.dst): (x.intent, x.reason) for x in schema.links}


def test_zeichnung_lesen():
    s = parse(DRAWING)
    assert s.page == "Netz" and s.pages == ["Netz", "Alt"]
    z = {x.name: x for x in s.zones}
    # Text, Gruppe (Rechenzentrum), Knoten aus dem Zonenplan und doppelter Name sind keine Zonen
    assert list(z) == ["Internet", "DMZ", "Server", "OOB-Netz"]
    inet = z["Internet"]
    assert (inet.kind, inet.kind_reason, inet.color) == ("external", "Eigenschaft automato_kind",
                                                         "#f8cecc")  # fmt: skip
    assert inet.match == ["inet*", "untrust*"] and inet.description == "alles draussen"
    assert (z["DMZ"].kind, z["DMZ"].color) == ("dmz", "#ffcc00")
    assert (z["Server"].code, z["Server"].group, z["Server"].kind) == ("SRV", "Rechenzentrum",
                                                                       "internal")  # fmt: skip
    assert z["Server"].geometry == [420.0, 120.0, 120.0, 60.0]  # absolut (in der Gruppe)
    assert (z["OOB-Netz"].kind, z["OOB-Netz"].kind_reason) == ("management", "Name „oob“")
    assert _links(s) == {
        ("Internet", "Server"): ("deny", "Beschriftung"),
        ("Internet", "DMZ"): ("limited", "gestrichelt"),
        ("DMZ", "OOB-Netz"): ("deny", "Linienfarbe"),
        ("DMZ", "Server"): ("limited", "Beschriftung"),  # ohne Pfeil: beide Richtungen
        ("Server", "DMZ"): ("limited", "Beschriftung"),
        ("OOB-Netz", "Server"): ("allow", "Eigenschaft automato_intent"),  # Pfeil am Anfang
    }
    assert any("doppelt" in w for w in s.warnings)
    assert any("1 Verbindung(en) nicht zwischen zwei Zonen" in w for w in s.warnings)
    assert any("2 Seiten" in w for w in s.warnings)
    # Zuordnung ueber Name, Kurzname und Muster
    assert s.match("Z_DMZ").name == "DMZ" and s.match("srv-vl10").name == "Server"
    assert s.match("SRV").name == "Server" and s.match("untrust1").name == "Internet"
    assert s.match("lan") is None


def test_komprimierte_seite_und_fehler():
    inner = DRAWING.split('name="Netz">', 1)[1].split("</diagram>", 1)[0]
    raw = zlib.compressobj(9, zlib.DEFLATED, -15)
    packed = raw.compress(quote(inner).encode()) + raw.flush()
    text = f'<mxfile><diagram name="Zonen">{base64.b64encode(packed).decode()}</diagram></mxfile>'
    s = parse(text)
    assert s.page == "Zonen" and [z.name for z in s.zones][:2] == ["Internet", "DMZ"]
    for bad, msg in (
        ('<!DOCTYPE x [<!ENTITY a "b">]><mxfile/>', "DTD"),
        ("<html><body/></html>", "keine draw.io"),
        ("kein xml", "kein gültiges XML"),
        ('<mxfile><diagram name="x">@@@</diagram></mxfile>', "nicht lesbar"),
        ("<mxGraphModel><root><mxCell id='0'/></root></mxGraphModel>", "keine Zonen"),
        ("<mxfile>" + " " * zone_schema.MAX_BYTES + "</mxfile>", "zu gross"),
    ):
        with pytest.raises(SchemaError, match=msg):
            parse(bad)


def test_vorlage_und_export_wieder_lesen():
    base = zone_schema.default_schema()
    back = parse(zone_schema.drawing(base, "Vorlage"))
    assert [(z.name, z.kind, z.code, z.color, z.match) for z in back.zones] == [
        (z.name, z.kind, z.code, z.color, z.match) for z in base.zones
    ]
    assert {(x.src, x.dst, x.intent) for x in back.links} == {
        (x.src, x.dst, x.intent) for x in base.links
    }
    # Export des Zonenplans (0.52.0): Zonen ja, Knoten nein
    plan = {"zones": [{"id": 1, "name": "LAN", "code": None, "kind": "internal",
                       "color": "#3b82c4", "samples": ["10.60.0.0/24"], "nets": 1, "epgs": 0}],
            "nodes": [{"id": "1|root", "kind": "firewall", "label": "fw-02", "source": "fw-02",
                       "group": None, "zones": 1}],
            "attachments": [{"zone": 1, "node": "1|root", "via": ["interface"],
                             "interfaces": ["lan"], "networks": ["10.60.0.0/24"], "count": 1,
                             "routes": [], "default": False}],
            "links": [], "unzoned": {"total": 0, "items": []}}  # fmt: skip
    s = parse(plan_export.drawio(plan, "Zonenplan"))
    assert [(z.name, z.kind) for z in s.zones] == [("LAN", "internal")]


def test_schema_im_namensprofil(client):
    pid = client.post("/naming-profiles", json={"customer": "Kunde A", "name": "Std"},
                      headers=ADMIN).json()["id"]  # fmt: skip
    url = f"/naming-profiles/{pid}/zone-schema"
    assert client.get(url, headers=VIEW).json() == {"profile_id": pid, "schema": None,
                                                    "drawing": None}  # fmt: skip
    body = {"filename": "zonen.drawio", "content": DRAWING}
    assert client.put(url, json=body, headers=OP).status_code == 403  # nur Admin
    p = client.post(f"{url}/preview", json=body, headers=ADMIN).json()
    assert len(p["schema"]["zones"]) == 4 and p["diff"]["zones"]["added"] == [
        "DMZ", "Internet", "OOB-Netz", "Server"]  # fmt: skip
    r = client.put(url, json=body, headers=ADMIN)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["drawing"]["filename"] == "zonen.drawio"
    assert [z["name"] for z in got["schema"]["zones"]] == ["Internet", "DMZ", "Server", "OOB-Netz"]
    assert len(got["schema"]["links"]) == 6
    # Zeichnung zurueck wie hochgeladen; Vorlage und Ausgangspunkt aus den Zonen
    d = client.get(f"{url}/drawing", headers=VIEW)
    assert d.text == DRAWING and 'filename="zonen.drawio"' in d.headers["content-disposition"]
    t = client.get("/naming-profiles/zone-schema/template", headers=VIEW)
    tpl = parse(t.text)  # Vorlage nach dem Zonenmodell (0.56.0): Ebenen mit festen Farben
    assert "automato_match" in t.text and len(tpl.zones) == 12
    assert len(tpl.links) == len(zone_schema.DEFAULT_LINKS)
    assert [z.name for z in tpl.zones][:3] == ["DC", "Edge Internet", "Edge Office"]
    assert (tpl.zone("Edge Internet").code, tpl.zone("Edge Internet").color) == ("EINET", "#ffcc99")
    # neue Zeichnung: Unterschied zum gespeicherten
    changed = DRAWING.replace('automato_code="SRV"', 'automato_code="SV"').replace(
        'value="gesperrt"', 'value="erlaubt"')  # fmt: skip
    p = client.post(f"{url}/preview", json={"content": changed}, headers=ADMIN).json()
    assert p["diff"]["zones"]["changed"] == [{"name": "Server", "fields": ["code"]}]
    assert [(x["src"], x["intent"], x["before"]) for x in p["diff"]["links"]["changed"]] == [
        ("Internet", "allow", "deny")]  # fmt: skip
    bad = client.post(f"{url}/preview", json={"content": "<x/>"}, headers=ADMIN)
    assert bad.status_code == 422 and "keine draw.io" in bad.json()["detail"]
    # das Woerterbuch zeigt die Eintraege (kopierbar wie jede Art)
    kinds = {e["kind"] for e in client.get(f"/naming-profiles/{pid}/dictionary").json()}
    assert kinds >= {"zone_schema", "zone_schema_link", "zone_schema_meta"}
    assert client.delete(url, headers=ADMIN).status_code == 204
    assert client.get(url).json()["schema"] is None


SCHEMA = """<mxfile><diagram name="Zonenschema"><mxGraphModel><root>
<mxCell id="0"/><mxCell id="1" parent="0"/>
<object id="i" label="Internet" automato_kind="extern" automato_match="internet*, wan*">
  <mxCell vertex="1" parent="1" style="fillColor=#ff0000;"/></object>
<object id="d" label="DMZ" automato_kind="DMZ" automato_match="dmz*">
  <mxCell vertex="1" parent="1"/></object>
<object id="s" label="Server Zone" automato_code="SRV" automato_match="server*, srv*">
  <mxCell vertex="1" parent="1"/></object>
<object id="l" label="Labor" automato_match="port5">
  <mxCell vertex="1" parent="1"/></object>
<object id="t" label="Transit-Netze" automato_kind="Transfer">
  <mxCell vertex="1" parent="1"/></object>
<object id="a" label="Admin" automato_kind="Management"><mxCell vertex="1" parent="1"/></object>
<object id="p" label="Partner" automato_kind="extern"><mxCell vertex="1" parent="1"/></object>
<mxCell id="x1" value="gesperrt" edge="1" parent="1" source="i" target="s"/>
<mxCell id="x2" value="eingeschränkt" edge="1" parent="1" source="d" target="s"/>
</root></mxGraphModel></diagram></mxfile>"""


def test_vorschlaege_und_absichten_nach_dem_schema(client):
    tid, ids = _setup(client)
    pid = client.post("/naming-profiles", json={"customer": "Kunde A", "name": "Zonen"},
                      headers=ADMIN).json()["id"]  # fmt: skip
    assert client.patch(f"/tenants/{tid}", json={"naming_profile_id": pid},
                        headers=ADMIN).status_code == 200  # fmt: skip
    r = client.put(f"/naming-profiles/{pid}/zone-schema", json={"content": SCHEMA}, headers=ADMIN)
    assert r.status_code == 200, r.text

    out = _get(client, tid)
    s = _by_key(out)
    cid = ids["customer"]
    assert set(s) == {f"dc:{cid}", "internet", "backup", "server", "labor"}
    # Name, Art und Farbe aus dem Schema: Firewall-Zonen bzw. Interfaces ueber die Muster
    srv = s["server"]  # „Server Zone“ heisst normalisiert `server` (wie Z_SERVER)
    assert (srv["name"], srv["code"], srv["schema"]) == ("Server Zone", "SRV", "Server Zone")
    assert srv["kind_reason"] == "Grundschema „Server Zone“"
    assert _sels(srv) == {("fw_zone", "server", False)}
    # Anbindung und Firewall-Zone internet = die externe Zone des Schemas
    assert (s["internet"]["color"], s["internet"]["schema"]) == ("#ff0000", "Internet")
    assert ("fw_zone", "internet", False) in _sels(s["internet"])
    assert s["labor"]["selectors"][0]["value"] == "port5"
    # ohne DC im Schema: Datacenter des Kunden mit der Farbe der Art
    dc = s[f"dc:{cid}"]
    assert (dc["name"], dc["kind"], dc["schema"], dc["color"]) == (
        "DC Kunde TE", "dc", None, "#3b82c4")  # fmt: skip
    # ohne Edge im Schema: keine Internet-Firewall, alle Knoten zum Zuordnen
    assert len(out["nodes"]) == 4
    sch = out["schema"]
    assert (sch["profile"], sch["zones"], sch["links"]) == ("Kunde A · Zonen", 7, 2)
    assert [z["name"] for z in sch["missing"]] == ["DMZ", "Transit-Netze", "Admin", "Partner"]

    # Zonen anlegen: die Absichten des Schemas fehlen in der Matrix
    srv = _zone(client, tid, "Server Zone", [_sel("prefix", "10.20.0.0/24")])
    inet = _zone(client, tid, "Internet", [_sel("exit", "Internet")], kind="external")
    dmz = _zone(client, tid, "DMZ-Web", [_sel("prefix", "10.30.0.0/24")], kind="dmz")
    sch = _get(client, tid)["schema"]
    assert sch["matched"] == {str(srv["id"]): "Server Zone", str(inet["id"]): "Internet",
                              str(dmz["id"]): "DMZ"}  # fmt: skip
    assert {(x["src"], x["dst"], x["intent"], x["current"]) for x in sch["intents"]} == {
        ("Internet", "Server Zone", "deny", None),
        ("DMZ-Web", "Server Zone", "limited", None),
    }
    # eine Absicht weicht ab: nur die fehlende wird gesetzt
    policy = {"tenant_id": tid, "src_zone_id": dmz["id"], "dst_zone_id": srv["id"],
              "intent": "allow"}  # fmt: skip
    assert client.put("/zones/policy", json=policy, headers=OP).status_code == 200
    body = {"tenant_id": tid}
    assert client.post("/zones/schema/intents", json=body, headers=VIEW).status_code == 403
    done = client.post("/zones/schema/intents", json=body, headers=OP).json()["changed"]
    assert done == [{"src": inet["id"], "dst": srv["id"], "intent": "deny", "before": None}]
    m = client.get("/zones/matrix", params={"tenant_id": tid}).json()
    intents = {(x["src"], x["dst"]): x for x in m["intents"]}
    assert (
        intents[(inet["id"], srv["id"])]["note"] == "Grundschema: Internet → Server Zone (gesperrt)"
    )
    # abweichende nur ausdruecklich ersetzen
    left = _get(client, tid)["schema"]["intents"]
    assert [(x["src"], x["current"]) for x in left] == [("DMZ-Web", "allow")]
    body = {"tenant_id": tid, "pairs": [[dmz["id"], srv["id"]]], "overwrite": True}
    done = client.post("/zones/schema/intents", json=body, headers=OP).json()["changed"]
    assert done == [{"src": dmz["id"], "dst": srv["id"], "intent": "limited", "before": "allow"}]
    assert _get(client, tid)["schema"]["intents"] == []


def test_schema_in_der_oberflaeche_bearbeiten(client, session_factory):
    """0.70.0 (Anwender: „Anpassungen nach erstmaligem Hochladen auch im Web UI, Anpassen von
    Namen und Absicht usw.“): Zonen umbenennen, Art/Kurzname/Farbe/Erkennung, Reihenfolge,
    Uebergaenge mit Absicht und Diensten; Zeichnung danach aus dem bearbeiteten Stand."""
    pid = client.post("/naming-profiles", json={"customer": "Kunde A", "name": "Std"},
                      headers=ADMIN).json()["id"]  # fmt: skip
    url = f"/naming-profiles/{pid}/zone-schema"
    client.put(url, json={"filename": "zonen.drawio", "content": DRAWING}, headers=ADMIN)
    schema = client.get(url).json()["schema"]
    zones = [{k: z[k] for k in ("name", "kind", "code", "color", "match", "group",
                                "description", "geometry")} for z in schema["zones"]]  # fmt: skip
    links = [{k: x[k] for k in ("src", "dst", "intent", "services", "note")}
             for x in schema["links"]]  # fmt: skip
    # Server -> Applikationsserver (umbenannt), DMZ nach vorne, Farbe und Muster
    srv = next(z for z in zones if z["name"] == "Server")
    srv |= {
        "name": "Applikationsserver",
        "previous": "Server",
        "code": "APP",
        "match": ["app*", "SRV*"],
        "color": "#abc",
    }
    dmz = next(z for z in zones if z["name"] == "DMZ")
    zones = [dmz] + [z for z in zones if z is not dmz]
    for x in links:
        for end in ("src", "dst"):
            if x[end] == "Server":
                x[end] = "Applikationsserver"
        if (x["src"], x["dst"]) == ("Internet", "Applikationsserver"):
            x |= {"intent": "limited", "services": ["tcp/443", "FT_https"], "note": "nur Web"}
    body = {"zones": zones, "links": links}
    assert client.patch(url, json=body, headers=OP).status_code == 403  # nur Admin
    r = client.patch(url, json=body, headers=ADMIN)
    assert r.status_code == 200, r.text
    got = r.json()
    assert [z["name"] for z in got["schema"]["zones"]][:2] == ["DMZ", "Internet"]
    app = next(z for z in got["schema"]["zones"] if z["name"] == "Applikationsserver")
    assert (app["code"], app["color"], app["match"]) == ("APP", "#aabbcc", ["app*", "srv*"])
    assert app["geometry"] == srv["geometry"] and app["kind_reason"] == srv_reason(schema)
    lim = next(x for x in got["schema"]["links"] if x["dst"] == "Applikationsserver"
               and x["src"] == "Internet")  # fmt: skip
    assert (lim["intent"], lim["services"], lim["note"], lim["reason"]) == (
        "limited",
        ["tcp/443", "FT_https"],
        "nur Web",
        "von Hand",
    )
    assert got["drawing"]["edited_at"] and got["drawing"]["filename"] == "zonen.drawio"
    # Download: aus dem bearbeiteten Stand - wieder lesbar, mit dem neuen Namen
    back = parse(client.get(f"{url}/drawing").text)
    assert back.zone("Applikationsserver").code == "APP" and back.zone("Server") is None
    assert _links(back)[("Internet", "Applikationsserver")][0] == "limited"
    # Audit mit dem Unterschied
    from app.models import AuditLog

    entry = session_factory().query(AuditLog).filter(AuditLog.action == "zone_schema.edited").one()
    assert entry.detail["added"] == ["Applikationsserver"] and entry.detail["removed"] == ["Server"]
    # Fehler: Uebergang zu fehlender Zone, doppelte Zone, Farbe, Art
    for bad, text in (
        ({"zones": zones, "links": [{"src": "DMZ", "dst": "Nirgends"}]}, "Zone fehlt"),
        ({"zones": zones + [{"name": "dmz"}], "links": []}, "doppelt"),
        ({"zones": [{"name": "X", "color": "rot"}], "links": []}, "Hex"),
        ({"zones": [{"name": "X", "kind": "magic"}], "links": []}, "Art"),
        ({"zones": [], "links": []}, "mindestens eine Zone"),
    ):
        r = client.patch(url, json=bad, headers=ADMIN)
        assert r.status_code == 422 and text in r.json()["detail"], r.text
    # neue Zeichnung hochladen: die Bearbeitung ist vorbei, Download wie hochgeladen
    client.put(url, json={"filename": "neu.drawio", "content": DRAWING}, headers=ADMIN)
    assert client.get(f"{url}/drawing").text == DRAWING


def srv_reason(schema: dict) -> str:
    return next(z["kind_reason"] for z in schema["zones"] if z["name"] == "Server")
