# ruff: noqa: S314 - geparst wird nur die eigene Ausgabe des Exports
"""Export fuer Confluence (0.71.0, ADR 0026): Applikations-Doku und Zonenplan als Word (.docx)
und draw.io - zum Import von Hand, kein Schreibweg nach Confluence."""

import io
import xml.etree.ElementTree as ET
import zipfile

import test_aci_relevance as ar
import test_app_paths as tap
import test_zones as tz
from app.store import word_export
from app.store.docx_writer import Document
from app.store.md_writer import Markdown

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
OP = {"X-Dev-Roles": "operator"}


def _text(docx: bytes) -> tuple[str, list[str]]:
    """(ganzer Text, Ueberschriften) der Word-Datei; prueft, dass alle Teile XML sind."""
    z = zipfile.ZipFile(io.BytesIO(docx))
    assert {"[Content_Types].xml", "word/document.xml", "word/styles.xml"} <= set(z.namelist())
    for n in z.namelist():
        ET.fromstring(z.read(n))
    root = ET.fromstring(z.read("word/document.xml"))
    heads, parts = [], []
    for p in root.iter(f"{W}p"):
        text = "".join(t.text or "" for t in p.iter(f"{W}t"))
        style = p.find(f"{W}pPr/{W}pStyle")
        if style is not None and style.get(f"{W}val", "").startswith(("Heading", "Title")):
            heads.append(text)
        parts.append(text)
    return "\n".join(parts), heads


def test_word_schreiber():
    d = Document("Test")
    d.heading("Titel", 0)
    d.heading("Abschnitt", 1)
    d.paragraph("normal ", ("fett", {"bold": True}))
    d.fields([("Name", "App"), ("leer", ""), ("Steuerzeichen", "a\x01b")])
    d.table(["Spalte A", "Spalte B"], [["1", "zwei\nZeilen"], ["nur eine"]])
    d.bullets(["Punkt"])
    text, heads = _text(d.bytes())
    assert heads == ["Titel", "Abschnitt"]
    assert "fett" in text and "ab" in text and "leer" not in text  # leere Felder entfallen
    assert "Spalte A" in text and "nur eine" in text and "• Punkt" in text
    body = zipfile.ZipFile(io.BytesIO(d.bytes())).read("word/document.xml").decode()
    assert 'w:orient="landscape"' in body and "<w:tblHeader/>" in body and "<w:br/>" in body


def test_dateiname():
    assert word_export.slug("Portal Süd", "x") == "portal-sued"
    assert word_export.slug("Größe/Ärger", "x") == "groesse-aerger"
    assert word_export.slug("§§", "applikation") == "applikation"


def test_applikation_als_word_und_drawio(client):
    tid = tap._seed(client)
    body = {"name": "Web", "members": [{"kind": "epg", "ref": ar.WEB}]}
    app = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP).json()
    r = client.get(f"/applications/{app['id']}/export")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    name = r.headers["content-disposition"].split('filename="')[1].rstrip('"')
    assert name.startswith("applikation-web-") and name.endswith(".zip")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    docx = next(n for n in z.namelist() if n.endswith(".docx"))
    drawio = next(n for n in z.namelist() if n.endswith(".drawio"))
    assert ET.fromstring(z.read(drawio)).tag == "mxfile"
    text, heads = _text(z.read(docx))
    assert heads[0] == "Applikation Web"
    for h in (
        "Systeme",
        "Regeln & Contracts",
        "Verbindungen Ende zu Ende",
        "Netzwerkplan",
        "Doku nach Kategorien",
    ):
        assert h in heads, heads
    assert drawio in text  # Hinweis auf die beiliegende draw.io-Datei
    assert "CT_lan" in text and "erlaubt" in text  # Contract und Urteil der Verbindung
    assert "Mandant Kunde A" in text
    # nur Word
    r = client.get(f"/applications/{app['id']}/export", params={"format": "docx"})
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert _text(r.content)[1][0] == "Applikation Web"
    assert client.get(f"/applications/{app['id']}/export",
                      params={"format": "pdf"}).status_code == 422  # fmt: skip
    # 0.73.0: Abschnitte zur Wahl - ohne Plan nur die Word-Datei im ZIP
    r = client.get(f"/applications/{app['id']}/export", params={"sections": "systems,rules"})
    z = zipfile.ZipFile(io.BytesIO(r.content))
    (only,) = z.namelist()
    _text_, heads = _text(z.read(only))
    assert heads == ["Applikation Web", "Systeme", "Regeln & Contracts"]
    r = client.get(f"/applications/{app['id']}/export",
                   params={"sections": "plan,hidden", "format": "zip"})  # fmt: skip
    assert len(zipfile.ZipFile(io.BytesIO(r.content)).namelist()) == 2
    assert client.get(f"/applications/{app['id']}/export",
                      params={"sections": "abhaengigkeiten"}).status_code == 422  # fmt: skip
    # Kunden-Benutzer (seit 0.104.0 freigegeben): nur Applikationen ihres Ausschnitts
    cust = client.post("/customers", json={"tenant_id": tid, "name": "K", "selectors": []},
                       headers=OP).json()  # fmt: skip
    who = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid), "X-Dev-Customers": str(cust["id"])}
    assert client.get(f"/applications/{app['id']}/export", headers=who).status_code == 404


def _heads(md: str) -> list[str]:
    return [line.lstrip("#").strip() for line in md.splitlines() if line.startswith("#")]


def test_markdown_schreiber():
    out = Markdown("T")
    out.heading("Titel", 0)
    out.heading("Teil", 1)
    out.paragraph(("Übersicht: ", {"bold": True}), "3 VMs")
    out.note("Stand heute")
    out.fields([("Zweck", "Prod"), ("leer", "")])
    out.table(["A", "B"], [["x|y", "1\n2"], ["z"]])
    text = out.bytes().decode()
    assert text.splitlines()[:4] == ["# Titel", "", "## Teil", ""]
    assert "**Übersicht:** 3 VMs" in text and "_Stand heute_" in text
    assert "- **Zweck:** Prod" in text and "leer" not in text
    assert "| A | B |\n| --- | --- |\n| x\\|y | 1<br>2 |\n| z |  |" in text


def test_applikation_als_markdown_wie_word(client):
    """0.104.0 (Modularisierung): Markdown vom Server aus demselben Aufbau wie Word - gleiche
    Abschnitte, gleiche Tabellen; bisher baute die Oberflaeche das Markdown selbst."""
    tid = tap._seed(client)
    body = {"name": "Web", "members": [{"kind": "epg", "ref": ar.WEB}]}
    app = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP).json()
    url = f"/applications/{app['id']}/export"
    r = client.get(url, params={"format": "md"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/markdown")
    name = r.headers["content-disposition"].split('filename="')[1].rstrip('"')
    assert name.startswith("applikation-web-") and name.endswith(".md")
    md = r.text
    word = _text(client.get(url, params={"format": "docx"}).content)[1]
    assert _heads(md) == word  # dieselbe Gliederung
    assert md.startswith("# Applikation Web\n")
    assert "| Richtung | Quelle | Ziel | Dienst | Weg (entscheidende Regel) | Urteil |" in md
    assert "CT_lan" in md and "apic-01" in md
    assert "Netzwerkplan als draw.io-Datei in Automato" in md  # kein Plan beigelegt
    # Abschnitte zur Wahl
    part = client.get(url, params={"format": "md", "sections": "systems,findings"}).text
    assert "## Systeme" in part and "## Regeln & Contracts" not in part
    # Kunden-Benutzer: ihr Ausschnitt, ohne Verbindungen und Plan, ohne Geraetenamen
    sel = [{"kind": "aci_tenant", "value": "TE"}]
    cust = client.post("/customers", json={"tenant_id": tid, "name": "K", "selectors": sel},
                       headers=OP).json()  # fmt: skip
    who = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid), "X-Dev-Customers": str(cust["id"])}
    r = client.get(url, params={"format": "md"}, headers=who)
    assert r.status_code == 200, r.text
    assert "## Systeme" in r.text and "## Regeln & Contracts" in r.text
    assert "## Verbindungen Ende zu Ende" not in r.text and "## Netzwerkplan" not in r.text
    assert "apic-01" not in r.text and "fw-01" not in r.text and "Kunde A" not in r.text
    r = client.get(url, params={"format": "zip"}, headers=who)  # ohne Plan nur Word
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")


def test_zonenplan_als_word_und_drawio(client):
    tid, ids = tz._seed(client)
    z = tz._zones(client, tid, ids)
    body = {"tenant_id": tid, "src_zone_id": z["inet"]["id"], "dst_zone_id": z["server"]["id"],
            "intent": "deny", "note": "nur über Reverse Proxy"}  # fmt: skip
    client.put("/zones/policy", json=body, headers=OP)
    r = client.get("/zones/plan/export", params={"tenant_id": tid, "format": "zip", "all": "true"})
    assert r.status_code == 200, r.text
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert any(n.endswith(".docx") for n in names) and any(n.endswith(".drawio") for n in names)
    text, heads = _text(zf.read(next(n for n in names if n.endswith(".docx"))))
    assert heads[0] == "Zonenplan Kunde A"
    assert {"Zonen", "Zonenmatrix"} <= set(heads)
    for name in ("Server", "ACI Web", "LAN", "Internet"):
        assert name in text
    assert "gesperrt" in text and "nur über Reverse Proxy" in text  # Absicht mit Notiz
    r = client.get("/zones/plan/export", params={"tenant_id": tid, "format": "docx"})
    assert r.status_code == 200 and _text(r.content)[1][0] == "Zonenplan Kunde A"
