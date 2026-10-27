"""Zonen je Kunde im Grundschema (0.82.0, ADR 0024 Nr. 19).

Eine Zone des Grundschemas mit `automato_customer` = „je Kunde“ ist eine Vorlage: je Kunde
wird eine eigene Zone „<Zone> <Kunde>“ (Kurzname „<Kurzname>-<Kuerzel>“) mit seinen Treffern
vorgeschlagen; Zonen der Kunden passen ohne Name bzw. Kuerzel des Kunden auf die Vorlage, die
Absichten der Vorlage gelten innerhalb eines Kunden.
"""

from app.store import zone_schema
from test_zone_detect import _by_key, _get, _sels, _setup
from test_zones import ADMIN, OP, _sel, _zone

SCHEMA = """<mxfile host="drawio"><diagram id="a" name="Zonen"><mxGraphModel><root>
<mxCell id="0"/><mxCell id="1" parent="0"/>
<object id="lab" label="Labor" automato_code="LAB" automato_match="port5*, port3*"
        automato_customer="je Kunde">
  <mxCell style="rounded=1;fillColor=#e1d5e7;" vertex="1" parent="1">
    <mxGeometry x="0" y="0" width="120" height="60" as="geometry"/></mxCell></object>
<object id="srv" label="Server" automato_code="SRV" automato_customer="ja">
  <mxCell style="rounded=1;" vertex="1" parent="1">
    <mxGeometry x="200" y="0" width="120" height="60" as="geometry"/></mxCell></object>
<object id="pub" label="Public" automato_kind="extern">
  <mxCell style="rounded=1;" vertex="1" parent="1">
    <mxGeometry x="400" y="0" width="120" height="60" as="geometry"/></mxCell></object>
<mxCell id="e1" value="gesperrt" style="endArrow=classic;" edge="1" parent="1" source="lab"
        target="srv"><mxGeometry relative="1" as="geometry"/></mxCell>
</root></mxGraphModel></diagram></mxfile>"""


def test_vorlage_je_kunde_lesen_und_zeichnen():
    s = zone_schema.parse(SCHEMA)
    by = {z.name: z for z in s.zones}
    assert by["Labor"].per_customer and by["Server"].per_customer
    assert not by["Public"].per_customer
    assert by["Labor"].for_customer("Kunde B", "kb") == ("Labor Kunde B", "LAB-KB")
    # Zeichnung hin und zurueck behaelt die Eigenschaft
    again = {z.name: z for z in zone_schema.parse(zone_schema.drawing(s, "Test")).zones}
    assert again["Labor"].per_customer and not again["Public"].per_customer
    # Zone eines Kunden passt ohne seinen Namen bzw. sein Kuerzel auf die Vorlage
    assert s.of_customer_zone("Labor Kunde B", None, "Kunde B", "kb") is by["Labor"]
    assert s.of_customer_zone("Mein Labor", "LAB-KB", "Kunde B", "kb") is by["Labor"]
    assert s.of_customer_zone("Public Kunde B", None, "Kunde B", "kb") is None  # keine Vorlage


def test_vorschlaege_je_kunde(client):
    tid, ids = _setup(client)
    body = {"tenant_id": tid, "name": "Kunde B", "code": "kb",
            "selectors": [_sel("prefix", "10.32.0.0/24")]}  # fmt: skip
    r = client.post("/customers", json=body, headers=OP)
    assert r.status_code == 201, r.text
    kb = r.json()["id"]
    pid = client.post("/naming-profiles", json={"customer": "Kunde A", "name": "Zonen"},
                      headers=ADMIN).json()["id"]  # fmt: skip
    client.patch(f"/tenants/{tid}", json={"naming_profile_id": pid}, headers=ADMIN)
    r = client.put(f"/naming-profiles/{pid}/zone-schema", json={"content": SCHEMA}, headers=ADMIN)
    assert r.status_code == 200, r.text
    zones = {z["name"]: z for z in r.json()["schema"]["zones"]}
    assert zones["Labor"]["per_customer"] is True

    s = _by_key(_get(client, tid))
    # port5 liegt im Netz von Kunde B: eigener Vorschlag; port3 gehoert keinem Kunden
    mine = s[f"labor:{kb}"]
    assert (mine["name"], mine["code"], mine["customer_id"]) == ("Labor Kunde B", "LAB-KB", kb)
    assert mine["kind_reason"] == "Grundschema „Labor“ je Kunde"
    assert _sels(mine) == {("fw_interface", "port5", False)}
    assert _sels(s["labor"]) == {("fw_interface", "port3", False)}
    assert s["labor"]["customer_id"] is None

    # Zonen der Kunden passen auf die Vorlage; die Absicht gilt nur im selben Kunden
    lab = _zone(client, tid, "Labor Kunde B", [_sel("prefix", "10.32.0.0/24")], code="LAB-KB",
                customer_id=kb)  # fmt: skip
    srv_b = _zone(client, tid, "Server Kunde B", [_sel("prefix", "10.20.0.0/25")],
                  customer_id=kb)  # fmt: skip
    _zone(client, tid, "Server Kunde TE", [_sel("prefix", "10.20.0.128/25")],
          customer_id=ids["customer"])  # fmt: skip
    sch = _get(client, tid)["schema"]
    assert sch["matched"][str(lab["id"])] == "Labor"
    assert sch["matched"][str(srv_b["id"])] == "Server"
    assert sch["outside"] == []
    assert {(x["src"], x["dst"], x["intent"]) for x in sch["intents"]} == {
        ("Labor Kunde B", "Server Kunde B", "deny")}  # fmt: skip
    f = client.get("/findings", params={"tenant_id": tid, "type": "zone_outside_schema"}).json()
    assert (f["items"] if isinstance(f, dict) else f) == []


def test_je_kunde_bearbeiten(client):
    pid = client.post("/naming-profiles", json={"customer": "Kunde A", "name": "Zonen"},
                      headers=ADMIN).json()["id"]  # fmt: skip
    client.put(f"/naming-profiles/{pid}/zone-schema", json={"content": SCHEMA}, headers=ADMIN)
    body = {"zones": [{"name": "Labor", "kind": "internal", "code": "LAB", "per_customer": False},
                      {"name": "Server", "kind": "internal", "per_customer": True}],
            "links": []}  # fmt: skip
    r = client.patch(f"/naming-profiles/{pid}/zone-schema", json=body, headers=ADMIN)
    assert r.status_code == 200, r.text
    got = client.get(f"/naming-profiles/{pid}/zone-schema", headers=ADMIN).json()
    flags = {z["name"]: z["per_customer"] for z in got["schema"]["zones"]}
    assert flags == {"Labor": False, "Server": True}
