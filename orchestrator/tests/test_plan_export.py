# ruff: noqa: S314 - geparst wird nur die eigene Ausgabe des Exports
"""Zonenplan exportieren und Versionen (0.52.0, ADR 0024): Layout, SVG, draw.io, PDF (ohne
Bibliothek geprueft: Aufbau, Querverweise, Text), gespeicherte Versionen mit Fingerabdruck und
Vergleich, Rechte.

Umgebung wie in test_zones (fw-01, fw-02, ACI-VRF, Zonen Server/ACI Web/LAN/Internet/Transfer).
"""

import re
import xml.etree.ElementTree as ET
import zlib

from app.store import plan_export
from test_zones import ADMIN, OP, VIEW, _seed, _sel, _zone, _zones

SVG = "{http://www.w3.org/2000/svg}"


def _plan(client, tid) -> dict:
    r = client.get("/zones/plan", params={"tenant_id": tid})
    assert r.status_code == 200, r.text
    return r.json()


def _export(client, tid, fmt, **params):
    r = client.get("/zones/plan/export", params={"tenant_id": tid, "format": fmt, **params})
    assert r.status_code == 200, r.text
    return r


def test_layout_links_rechts_und_route_gestrichelt(client):
    tid, ids = _seed(client)
    z = _zones(client, tid, ids)
    _zone(client, tid, "Remote", [_sel("prefix", "10.99.0.0/16")])
    plan = _plan(client, tid)
    lay = plan["layout"]
    side = {b["title"]: b["x"] < lay["width"] / 2 for b in lay["zones"]}
    assert side["Server"] and side["Transfer"] and not side["Internet"]  # extern rechts
    inet = next(b for b in lay["zones"] if b["title"] == "Internet")
    assert inet["sub"] == "extern · 8 Netze" and inet["fill"] == "#f2cccc"  # aufgehellt
    assert {b["title"] for b in lay["nodes"]} >= {"fw-01 · VDOM root", "fw-02"}
    assert len(lay["edges"]) == len(plan["attachments"])
    server = next(e for e in lay["edges"] if e["zone"] == z["server"]["id"])
    assert server["label"] == "srv" and not server["dashed"]
    remote = [e for e in lay["edges"] if e["zone"] not in {x["id"] for x in z.values()}]
    assert remote and all(e["dashed"] for e in remote)  # nur ueber eine Route
    # keine Ueberlappung: Boxen einer Spalte untereinander
    ys = sorted(b["y"] for b in lay["zones"] if b["x"] < lay["width"] / 2)
    assert all(b - a >= plan_export.BOX_H for a, b in zip(ys, ys[1:], strict=False))


def test_export_svg_und_drawio(client):
    tid, ids = _seed(client)
    _zones(client, tid, ids)
    r = _export(client, tid, "svg")
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert re.match(r'attachment; filename="zonenplan-\d{4}-\d\d-\d\d\.svg"',
                    r.headers["content-disposition"])  # fmt: skip
    root = ET.fromstring(r.content)
    texts = [t.text for t in root.iter(f"{SVG}text")]
    assert "Zonenplan Kunde A" in texts and "Server" in texts and "fw-02" in texts
    assert "srv" in texts  # Interface an der Linie
    assert len(list(root.iter(f"{SVG}line"))) == len(_plan(client, tid)["attachments"])

    r = _export(client, tid, "drawio")
    assert r.headers["content-disposition"].endswith('.drawio"')
    mx = ET.fromstring(r.content)
    assert mx.tag == "mxfile" and mx.find("diagram/mxGraphModel/root") is not None
    objs = {o.get("id"): o for o in mx.iter("UserObject")}
    zones = [o for o in objs.values() if o.get("automato_zone")]
    assert {o.get("automato_zone") for o in zones} == {"Server", "ACI Web", "LAN", "Internet",
                                                       "Transfer"}  # fmt: skip
    inet = next(o for o in zones if o.get("automato_zone") == "Internet")
    assert inet.get("automato_kind") == "extern" and "128.0.0.0/1" in inet.get("automato_networks")
    assert inet.get("label").startswith("<b>Internet</b><br>")  # HTML-Label fuer draw.io
    edges = [o.find("mxCell") for o in objs.values() if o.find("mxCell").get("edge") == "1"]
    assert edges and all(e.get("source") in objs and e.get("target") in objs for e in edges)


def _pdf_objects(data: bytes) -> dict[int, bytes]:
    """Objekte ueber die xref-Tabelle lesen - prueft dabei die Versaetze."""
    start = int(data.rsplit(b"startxref\n", 1)[1].split(b"\n")[0])
    lines = data[start:].split(b"\n")
    assert lines[0] == b"xref"
    count = int(lines[1].split()[1])
    out = {}
    for i in range(1, count):
        offset = int(lines[2 + i].split()[0])
        head = data[offset : offset + 20]
        assert head.startswith(b"%d 0 obj" % i), head
        out[i] = data[offset : data.index(b"endobj", offset)]
    return out


def _pdf_text(objs: dict[int, bytes]) -> str:
    texts = []
    for body in objs.values():
        if b"/FlateDecode" in body:
            raw = body.split(b"stream\n", 1)[1].rsplit(b"\nendstream", 1)[0]
            content = zlib.decompress(raw).decode("latin-1")
            texts += re.findall(r"\((.*?)(?<!\\)\) Tj", content)
    return "\n".join(t.encode("latin-1").decode("cp1252") for t in texts)


def test_export_pdf(client):
    tid, ids = _seed(client)
    z = _zones(client, tid, ids)
    body = {"tenant_id": tid, "src_zone_id": z["inet"]["id"], "dst_zone_id": z["server"]["id"],
            "intent": "deny", "note": "nur über Reverse Proxy"}  # fmt: skip
    assert client.put("/zones/policy", json=body, headers=OP).status_code == 200
    r = _export(client, tid, "pdf")
    assert r.headers["content-type"] == "application/pdf"
    data = r.content
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    objs = _pdf_objects(data)
    pages = [b for b in objs.values() if b"/Type /Page " in b]
    assert len(pages) == 4  # Plan, Matrix, Zonen an den Knoten, Netze ohne Zone
    assert b"/MediaBox [0 0 842.00 595.00]" in pages[1]
    text = _pdf_text(objs)
    for part in ("Zonenplan Kunde A", "Server", "fw-01 · VDOM root", "Zonenmatrix",
                 "Zonen an den Knoten", "Netze ohne Zone", "10.255.1.0/30", "1 / 0",
                 "Internet -> Server: gesperrt – nur über Reverse Proxy"):  # fmt: skip
        assert part in text, part


def test_pdf_schreiber_maskiert_und_kuerzt():
    doc = plan_export._Pdf("Plan (A)")
    page = doc.page(200, 100)
    page.text(10, 20, "a (b) \\ c → d", 10)
    page.text(10, 40, "sehr langer Name " * 10, 10, width=80)
    data = doc.bytes()
    content = zlib.decompress(data.split(b"stream\n")[1].split(b"\nendstream")[0]).decode("latin-1")
    assert r"(a \(b\) \\ c -> d) Tj" in content
    long = re.findall(r"\((sehr.*?)\) Tj", content)[0]
    assert long.endswith("\x85") and plan_export.text_width(long, 10) <= 80  # … in WinAnsi
    assert b"/Title (Plan \\(A\\))" in data


def test_versionen_speichern_vergleichen_exportieren(client):
    tid, ids = _seed(client)
    z = _zones(client, tid, ids)
    body = {"tenant_id": tid, "label": "Erster Entwurf", "note": "vor der Migration"}
    assert client.post("/zones/plan/versions", json=body, headers=VIEW).status_code == 403
    r = client.post("/zones/plan/versions", json=body, headers=OP)
    assert r.status_code == 201, r.text
    v1 = r.json()
    assert (v1["number"], v1["label"], v1["created_by"]) == (1, "Erster Entwurf", "dev")
    assert v1["summary"]["zones"] == 5 and v1["app_version"]
    listed = client.get("/zones/plan/versions", params={"tenant_id": tid}).json()
    assert listed["current_fingerprint"] == v1["fingerprint"]  # unveraendert
    same = client.get(f"/zones/plan/versions/{v1['id']}/diff").json()
    assert same["same"] and same["zones"] == {"added": [], "removed": [], "changed": []}

    # aendern: neue Zone, Art einer Zone, Absicht
    _zone(client, tid, "Remote", [_sel("prefix", "10.99.0.0/16")])
    lan = z["lan"]
    client.put(f"/zones/{lan['id']}", json={"tenant_id": tid, "name": "LAN", "kind": "management",
                                            "selectors": lan["selectors"]}, headers=OP)  # fmt: skip
    pol = {"tenant_id": tid, "src_zone_id": z["inet"]["id"], "dst_zone_id": z["server"]["id"],
           "intent": "deny"}  # fmt: skip
    client.put("/zones/policy", json=pol, headers=OP)
    listed = client.get("/zones/plan/versions", params={"tenant_id": tid}).json()
    assert listed["current_fingerprint"] != v1["fingerprint"]
    d = client.get(f"/zones/plan/versions/{v1['id']}/diff").json()
    assert not d["same"] and d["to"] is None
    assert d["zones"]["added"] == ["Remote"]
    (changed,) = d["zones"]["changed"]
    assert changed["key"] == "LAN" and changed["old"]["kind"] == "internal"
    assert changed["new"]["kind"] == "management"
    assert d["intents"]["added"] == ["Internet → Server"]
    assert "Remote @ fw-01 · VDOM root" in d["attachments"]["added"]
    # Zahlen der Matrix: die Regel 10.20.0.0/16 -> 10.99.1.1 liegt jetzt in der Zone Remote
    cells = {c["cell"]: c for c in d["matrix"]}
    assert cells["Server → Remote"]["new"][0] == 1 and cells["Server → ohne Zone"]["new"][0] == 0

    v2 = client.post("/zones/plan/versions", json={"tenant_id": tid, "label": "Mit Remote"},
                     headers=OP).json()  # fmt: skip
    assert v2["number"] == 2
    between = client.get(f"/zones/plan/versions/{v1['id']}/diff", params={"to": v2["id"]}).json()
    assert between["to"] == 2 and between["zones"]["added"] == ["Remote"]
    full = client.get(f"/zones/plan/versions/{v1['id']}").json()
    assert {x["name"] for x in full["data"]["zones"]} == {"Server", "ACI Web", "LAN", "Internet",
                                                          "Transfer"}  # fmt: skip
    assert full["data"]["plan"]["layout"]["zones"] and full["data"]["matrix"]["cells"]
    sel = next(x for x in full["data"]["zones"] if x["name"] == "Server")["selectors"]
    assert sel == [{"kind": "fw_interface", "value": "srv", "exclude": False, "source": "fw-01"}]

    # Export einer Version: der gespeicherte Stand, ohne Remote
    r = _export(client, tid, "svg", version_id=v1["id"])
    assert 'filename="zonenplan-v1.svg"' in r.headers["content-disposition"]
    texts = [t.text for t in ET.fromstring(r.content).iter(f"{SVG}text")]
    assert "Remote" not in texts and any("Version 1 – Erster Entwurf" in (t or "") for t in texts)
    assert _export(client, tid, "pdf", version_id=v2["id"]).content.startswith(b"%PDF")

    # loeschen nur als Admin; Nummern werden nicht wieder vergeben
    assert client.delete(f"/zones/plan/versions/{v2['id']}", headers=OP).status_code == 403
    assert client.delete(f"/zones/plan/versions/{v2['id']}", headers=ADMIN).status_code == 204
    v3 = client.post("/zones/plan/versions", json={"tenant_id": tid, "label": "Neu"},
                     headers=OP).json()  # fmt: skip
    assert v3["number"] == 3
    assert client.get("/zones/plan/versions/9999").status_code == 404
    assert client.get("/zones/plan/export", params={"tenant_id": tid, "format": "png"}
                      ).status_code == 422  # fmt: skip


def test_versionen_rechte_und_mandant_loeschen(client):
    tid, ids = _seed(client)
    _zones(client, tid, ids)
    v = client.post("/zones/plan/versions", json={"tenant_id": tid, "label": "A"},
                    headers=OP).json()  # fmt: skip
    cust = client.post("/customers", json={"tenant_id": tid, "name": "Kunde A", "selectors": []},
                       headers=OP).json()  # fmt: skip
    who = {"X-Dev-Roles": "customer", "X-Dev-Customers": str(cust["id"])}
    for url in ("/zones/plan/versions", "/zones/plan/export", f"/zones/plan/versions/{v['id']}"):
        assert client.get(url, params={"tenant_id": tid}, headers=who).status_code == 403
    other = client.post("/tenants", json={"name": "Andere"}, headers=ADMIN).json()["id"]
    scoped = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(other)}
    assert client.get(f"/zones/plan/versions/{v['id']}", headers=scoped).status_code == 403
    r = client.delete(f"/tenants/{tid}?confirm=Kunde A", headers=ADMIN)
    assert r.status_code == 200, r.text
    assert client.get(f"/zones/plan/versions/{v['id']}").status_code == 404
