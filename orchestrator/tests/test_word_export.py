# ruff: noqa: S314 - geparst wird nur die eigene Ausgabe des Exports
"""Export fuer Confluence (0.71.0, ADR 0026): Applikations-Doku und Zonenplan als Word (.docx)
und draw.io - zum Import von Hand, kein Schreibweg nach Confluence."""

import io
import xml.etree.ElementTree as ET
import zipfile

import test_aci_relevance as ar
import test_app_paths as tap
import test_zones as tz
from app.store.docx_writer import Document

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
    # nicht fuer Kunden-Benutzer
    cust = client.post("/customers", json={"tenant_id": tid, "name": "K", "selectors": []},
                       headers=OP).json()  # fmt: skip
    who = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid), "X-Dev-Customers": str(cust["id"])}
    assert client.get(f"/applications/{app['id']}/export", headers=who).status_code == 403


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
