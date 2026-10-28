# ruff: noqa: S314 - geparst wird nur die eigene Ausgabe des Exports
"""Applikationen, Doku und Word-Export auf Englisch (0.87.0, ADR 0028 Schritt 2/3): Texte folgen
der Sprache der Anfrage, Schluessel und Werte, die verglichen werden, bleiben deutsch.

Umgebung aus `test_app_paths`: EPG_web -> apic-01 -> fw-01 -> fw-02 -> 10.60.0.5; tcp/443
erlaubt, tcp/8080 im ACI gesperrt."""

import io
import xml.etree.ElementTree as ET
import zipfile

import test_aci_direction as tdir
import test_aci_relevance as ar
import test_app_paths as tap
import test_application_doc as tad
import test_word_export as tw
from app.i18n import use
from app.models import NatRule
from app.store import app_paths, app_plan, appdoc, nat
from test_reference_sources import _tenant

OP = {"X-Dev-Roles": "operator"}
EN = {"Accept-Language": "en"}
SHOWN = {"Aktion": "Action", "Gerät": "Device", "Schutzmechanismen": "Protection",
         "NAT Externe IPv4": "NAT external IPv4",
         "Externer DNS Name": "External DNS name"}  # fmt: skip


def _app(client) -> tuple[int, int]:
    tid = tap._seed(client)
    body = {"name": "Web", "members": [{"kind": "epg", "ref": ar.WEB}]}
    r = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP)
    assert r.status_code == 201, r.text
    return tid, r.json()["id"]


def test_kategorien_spalten_und_marker():
    cols = appdoc.ALL_COLUMNS  # seit 0.106.0 nach den Zeilen (`table_columns`)
    assert appdoc.table_title(1) == "Internet Zugriff"
    assert appdoc.column_labels(cols) == cols  # Deutsch: Anzeige = Schluessel
    with use("en"):
        assert appdoc.table_title(1) == "Internet access"
        assert appdoc.table_title(10) == "Unspecific access to the application"
        labels = appdoc.column_labels(cols)
        assert labels == [SHOWN.get(c, c) for c in cols]
        assert labels[cols.index(appdoc.SOURCE)] == "Source"
        assert appdoc.reverse_mark() == "(reverse direction)"
        assert appdoc.both_mark() == "(↔ both directions)"
    # die Auswertung erkennt beide Fassungen der Marker
    assert app_paths._named("tcp/any, udp/53 (reverse direction)") == [("tcp", None), ("udp", 53)]
    assert app_paths._named("tcp/any (Rückrichtung)") == [("tcp", None)]
    assert appdoc.has_both("tcp/22 (↔ both directions)")
    assert appdoc.has_both("tcp/22 (↔ beide Richtungen)") and not appdoc.has_both("tcp/22")


def test_plan_implizit_gesperrt_als_merkmal():
    node = {"rules": []}
    check = {"label": "fw", "verdict": "implicit_deny", "rules": []}
    with use("en"):
        app_plan._add_rules(node, {"checks": [check]})
        (rule,) = node["rules"]
        assert rule["name"] == "implicitly denied" and rule["implicit"] is True
        node["rules"].insert(0, rule)  # Reihenfolge: erlaubende zuerst
        node["rules"][1] = {"name": "web (#1)", "on": None, "action": "allow",
                            "services": ["tcp/443"]}  # fmt: skip
        assert app_plan._rule_lines(node) == ["web (#1) · tcp/443", "✕ implicitly denied"]
        assert app_plan.verdict_text("depends") == "depends"
        assert app_plan.kind_text("object") == "Peer"
    # das Merkmal entscheidet, nicht der (uebersetzte) Name
    assert app_plan._implicit({"implicit": True, "name": "implicitly denied"})
    assert not app_plan._implicit({"name": "implizit gesperrt"})
    assert app_plan.verdict_text("depends") == "abhängig"


def test_nat_einstellungen():
    fw = NatRule(name="V", raw={"type": "static-nat", "arp-reply": "enable"}, attrs={})
    assert nat.settings(fw) == {"Typ": "static-nat", "ARP-Antwort": "enable"}
    avi = NatRule(name="vs", raw={}, attrs={"platform": "avi", "snat": "client", "enabled": False})
    assert nat.settings(avi) == {"Quelle zum Server": "Client-IP bleibt", "Aktiv": "nein"}
    with use("en"):
        assert nat.settings(fw) == {"Type": "static-nat", "ARP reply": "enable"}
        assert nat.settings(avi) == {"Source to server": "client IP preserved", "Enabled": "no"}


def test_doku_englisch_mit_labels(client):
    _tid, app = tad._app(client)
    de = client.get(f"/applications/{app['id']}/doc").json()
    en = client.get(f"/applications/{app['id']}/doc", headers=EN).json()
    t_de = {t["index"]: t for t in de["tables"]}
    assert t_de[1]["title"] == "Internet Zugriff"
    assert {t["index"]: t["title"] for t in en["tables"]}[1] == "Internet access"
    for t in en["tables"]:
        # Spalten bleiben Schluessel der Zeilen, die Anzeige steht parallel in `labels`
        assert t["columns"] == t_de[t["index"]]["columns"]
        assert t["labels"] == [SHOWN.get(c, c) for c in t["columns"]]
        assert t_de[t["index"]]["labels"] == t["columns"]
    rows = [r for t in en["tables"] for r in t["rows"]]
    assert rows and {r["Aktion"] for r in rows} <= {"Allow", "Deny"}  # Werte bleiben
    assert {r["Description"] for r in rows} >= {"net-in", "CT_app:SJ"}
    web = next(s for s in en["systems"] if s["name"] == "web01-007-ka")
    assert web["member"].startswith("Port group TE_ka|AP_007|EG_web01 → EPG ")
    via = {h["via"] for h in en["hits"]}
    assert "direct" in via and "direkt" not in via, via
    assert {h["via"] for h in de["hits"]} >= {"direkt"}


def test_verbindungen_und_plan_englisch(client):
    _tid, aid = _app(client)
    de = client.get(f"/applications/{aid}/connections").json()
    out = client.get(f"/applications/{aid}/connections", headers=EN).json()
    # Schluessel der Verbindungen bleiben (Filter, Export mit `key`)
    assert [c["key"] for c in out["connections"]] == [c["key"] for c in de["connections"]]
    conns = {(c["src"]["ip"], c["dst"]["ip"], c["service"]): c for c in out["connections"]}
    https = conns[(tap.WEB, tap.SRV, "tcp/443")]
    states = {s["rule_id"]: s for s in https["rules"]}
    texts = {s["state"]: s["text"] for s in states.values()}
    assert texts["decides"] == "decides on the path"
    assert texts["off_path"] == "fw-03 is not on the path of this connection"
    assert texts["other"].endswith(" decides earlier")
    assert "Policy 31 “web-lan-alt”" in {s["label"] for s in states.values()}
    blocked = conns[(tap.WEB, tap.SRV, "tcp/8080")]
    assert blocked["mismatch"][0].startswith("Policy 12 “web-lan-8080” allows – the path is ")
    plan = out["plan"]
    assert [c["title"] for c in plan["columns"]] == ["Application", "Path", "", "", "Peers"]
    aci = next(n for n in plan["nodes"] if n["kind"] == "l3out")
    assert aci["sub"] == "L3Out · depends"
    assert aci["rule_lines"] == ["Contract CT_lan · tcp/443", "✕ implicitly denied"]


def _web(client, tid) -> int:
    body = {"name": "Web", "members": [{"kind": "epg", "ref": ar.WEB}]}
    r = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_rueckrichtung_englisch(client):
    """ACI zustandslos: Marker der Rueckrichtung auf Englisch, die Verbindungen der Rueckrichtung
    entstehen weiter aus dem Dienst-Text der Treffer (`app_paths._named`)."""
    aid = _web(client, tdir._seed(client))
    doc = client.get(f"/applications/{aid}/doc", headers=EN).json()
    (open_row,) = (r for t in doc["tables"] for r in t["rows"]
                   if r["Description"].startswith("CT_open"))  # fmt: skip
    assert open_row["TCP/UDP"].endswith("(↔ both directions)")
    back = [h for h in doc["hits"] if h["reverse"]]
    assert back and all(h["via"].endswith(" · reverse direction") for h in back)
    assert all(h["services"].endswith("(reverse direction)") for h in back)
    out = client.get(f"/applications/{aid}/connections", headers=EN).json()
    de = client.get(f"/applications/{aid}/connections").json()
    assert sorted(c["key"] for c in out["connections"]) == sorted(
        c["key"] for c in de["connections"])  # fmt: skip
    icmp = next(
        c
        for c in out["connections"]
        if c["reverse"] and c["src"]["ip"] == "10.50.2.20" and c["service"] == "icmp"
    )
    assert icmp["verdict"] == "allow"


def test_nur_antworten_englisch(client):
    aid = _web(client, tdir._seed(client, tdir.REPLY))
    doc = client.get(f"/applications/{aid}/doc", headers=EN).json()
    rows = {r["Description"]: r for t in doc["tables"] for r in t["rows"]}
    reply = next(r for k, r in rows.items() if k.startswith("CT_reply"))
    assert reply["TCP/UDP"] == "tcp/443 (reverse direction)"
    none = next(r for k, r in rows.items() if k.startswith("CT_none"))
    assert none["TCP/UDP"] == "replies only (filter with source port only)"
    out = client.get(f"/applications/{aid}/connections", headers=EN).json()["connections"]
    names = {(x["label"], c["reverse"]) for c in out for x in c["rules"]}
    assert ("Contract CT_reply", True) in names
    assert ("Contract CT_reply", False) not in names and ("Contract CT_none", False) not in names


def test_exporte_englisch(client):
    _tid, aid = _app(client)
    r = client.get(f"/applications/{aid}/export", params={"format": "docx", "lang": "en"})
    assert r.status_code == 200, r.text
    name = r.headers["content-disposition"].split('filename="')[1]
    assert name.startswith("application-web-")
    text, heads = tw._text(r.content)
    assert heads[0] == "Application Web"
    for h in ("Systems", "Rules & contracts", "End-to-end connections", "Network diagram",
              "Documentation by category"):  # fmt: skip
        assert h in heads, heads
    assert "Tenant Kunde A" in text and "Action" in text and "Aktion" not in text
    styles = zipfile.ZipFile(io.BytesIO(r.content)).read("word/styles.xml").decode()
    assert 'w:val="en-US"' in styles
    # Netzwerkplan als SVG bzw. draw.io
    base = f"/applications/{aid}/connections/plan/export"
    svg = client.get(base, params={"lang": "en"})
    assert 'filename="network-diagram-web-' in svg.headers["content-disposition"]
    texts = [t.text for t in ET.fromstring(svg.text).iter("{http://www.w3.org/2000/svg}text")]
    assert "Network diagram Web" in texts and "Application" in texts
    doc = ET.fromstring(client.get(base, params={"format": "drawio", "lang": "en"}).text)
    cells = {c.get("id"): c for c in doc.iter("mxCell") if c.get("id")}
    assert cells["regeln"].get("value") == "Rules"  # Id der Ebene bleibt
    assert any("2 connections" in (c.get("value") or "") for c in doc.iter("mxCell"))
    kinds = {o.get("automato_kind") for o in doc.iter("UserObject") if o.get("automato_kind")}
    assert "Peer" in kinds or "external EPG" in kinds or "L3Out" in kinds, kinds


def test_import_englisch(client):
    tid = _tenant(client)
    head = {**OP, **EN}
    r = client.get("/applications/import/template", headers=head)
    assert 'filename="applications-template.csv"' in r.headers["content-disposition"]
    template = r.content.decode("utf-8-sig")
    assert template.startswith("Name;External name;Internal name;Purpose;Code;")
    # die englische Vorlage laesst sich wieder einlesen
    plan = client.post(f"/applications/import?tenant_id={tid}", json={"csv": template},
                       headers=head).json()  # fmt: skip
    assert len(plan["columns"]) == 9 and not plan["ignored"]
    assert plan["counts"]["create"] == 1 and plan["rows"][0]["changes"]["criticality"][1] == "high"
    bad = client.post(f"/applications/import?tenant_id={tid}", json={"csv": "Foo;Bar\n1;2\n"},
                      headers=head)  # fmt: skip
    assert bad.status_code == 422
    assert bad.json()["detail"] == "column “Name” is missing in the header"
    rows = client.post(
        f"/applications/import?tenant_id={tid}",
        json={"csv": "Name;Purpose\nA;foo\n;dev\n"},
        headers=head,
    ).json()["rows"]
    assert rows[0]["errors"] == ["purpose “foo” unknown (dev, test, prod)"]
    assert rows[1]["errors"] == ["name missing"]
    # ohne Sprache weiterhin deutsch
    de = client.post(f"/applications/import?tenant_id={tid}", json={"csv": "Foo\n1\n"}, headers=OP)
    assert de.json()["detail"] == "Spalte „Name“ fehlt in der Kopfzeile"
