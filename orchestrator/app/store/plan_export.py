"""Zonenplan zeichnen und exportieren (0.52.0, ADR 0024): ein Layout fuer Oberflaeche, SVG,
draw.io und PDF.

- `layout(plan)`: Zonen links (intern, Management, Transfer) bzw. rechts (DMZ, extern,
  geteilt), die gelesenen Knoten in der Mitte, Linien = Zone am Knoten (gestrichelt: nur ueber
  eine Route), beschriftet mit den Interfaces. Die Oberflaeche zeichnet dasselbe Layout.
- `svg`, `drawio` (unkomprimiertes mxGraph-XML, in diagrams.net bearbeitbar; Zonen und Knoten
  tragen Art, Netze bzw. Interfaces als Eigenschaften) und `pdf` (Plan, Zonenmatrix, Zonen an
  den Knoten, Netze ohne Zone).

Ohne zusaetzliche Abhaengigkeit: XML ueber `xml.etree`, das PDF schreibt ein kleiner eigener
Schreiber mit den Standardschriften Helvetica (WinAnsi). Reine Funktionen ueber dem Plan - fuer
den aktuellen Stand wie fuer eine gespeicherte Version.
"""

import xml.etree.ElementTree as ET
import zlib
from collections import defaultdict
from datetime import datetime

from app.store import zones as zone_store

ZONE_KIND = {
    "dc": "Datacenter",
    "edge": "Edge",
    "transfer": "Transfer",
    "core": "Core",
    "distribution": "Distribution",
    "access": "Access",
    "external": "extern",
    "internal": "intern",
    "dmz": "DMZ",
    "management": "Management",
    "shared": "geteilt",
    "customer": "Kunde",
    "none": "ohne Zone",
    "outside": "ausserhalb",
}
# Spalten des Zonenplans (0.56.0) von links nach rechts: DC, Edge, Transfer, Core,
# Distribution, Access, Public
LAYER = zone_store.LAYER
GAP, LINE_H, MAX_LINES = 150, 15, 6
RIGHT_KINDS = {"external", "dmz", "shared"}
NODE_KIND = {"vrf": "ACI-VRF", "vdom": "VDOM", "firewall": "Firewall"}
INTENT = {"allow": "erlaubt", "limited": "eingeschränkt", "deny": "gesperrt"}

W, ROW, ZW, NW = 980, 58, 230, 250
BOX_H = ROW - 16
HEAD = 56  # Kopf mit Titel in SVG und PDF

# Farben der Exporte (hell, druckbar)
FG, MUTED, LINE, NODE_FILL, NODE_STROKE, ZONE_STROKE = (
    "#171b22", "#5a6475", "#8a93a3", "#eef1f6", "#0e7c86", "#c7cedb",
)  # fmt: skip
DEFAULT_ZONE = "#e5e9f0"


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _rgb_of(color: str | None) -> list[int] | None:
    c = (color or "").lstrip("#")
    if len(c) != 6:
        return None
    try:
        return [int(c[i : i + 2], 16) for i in (0, 2, 4)]
    except ValueError:
        return None


def _light(color: str | None) -> bool:
    """Helle Farbe (Pastell der Vorlage, wie in draw.io): als Fuellung direkt verwendbar."""
    rgb = _rgb_of(color)
    return rgb is not None and (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255 > 0.8


def _tint(color: str | None, amount: float = 0.25) -> str:
    """Zonenfarbe aufgehellt (wie in der Oberflaeche mit 22 % Deckkraft); helle Farben der
    Vorlage bleiben, wie sie sind."""
    rgb = _rgb_of(color)
    if rgb is None:
        return DEFAULT_ZONE
    if _light(color):
        return "#" + "".join(f"{v:02x}" for v in rgb)
    return "#" + "".join(f"{round(v * amount + 255 * (1 - amount)):02x}" for v in rgb)


def _stroke(color: str | None) -> str:
    """Rahmen: die Zonenfarbe, bei hellen Farben der neutrale Rahmen."""
    return ZONE_STROKE if not color or _light(color) else color


def layout(plan: dict) -> dict:
    """Positionen (Pixel, Ursprung oben links): ab 0.56.0 Zonen in Spalten nach Ebene mit ihren
    Knoten, Uebergaenge als Linien; Plaene aelterer Versionen im Layout von 0.52.0."""
    if plan.get("format") == 2:
        return _layout2(plan)
    return _layout1(plan)


def _box_lines(z: dict, nodes: dict[str, str]) -> list[str]:
    if z.get("collapsed"):
        return [m for m in (z.get("members") or [])][:MAX_LINES]
    labels = [nodes.get(n, n) for n in z.get("nodes") or []]
    if len(labels) > MAX_LINES:
        return labels[: MAX_LINES - 1] + [f"… {len(labels) - MAX_LINES + 1} weitere"]
    return labels


def _sub(z: dict) -> str:
    kind = z.get("kind")
    if kind == "customer":
        return f"Kunde · {_plural(len(z.get('members') or []), 'Zone', 'Zonen')}"
    if kind == "none":
        return _plural(len(z.get("nodes") or []), "Knoten", "Knoten")
    if kind == "outside":
        return "Router ohne gelesenes Gerät"
    return f"{ZONE_KIND.get(kind, kind)} · {_plural(z.get('nets') or 0, 'Netz', 'Netze')}"


def _layout2(plan: dict) -> dict:
    """Zonen in Spalten nach Ebene (DC links, Public rechts), je Zone ihre Knoten; Uebergaenge
    als Linien zwischen den Zonen, beschriftet mit dem Namen („DC-EINET“)."""
    zones = plan.get("zones") or []
    nodes = {n["id"]: n["label"] for n in plan.get("nodes") or []}
    # Spalte je Ebene; eine Zone ohne Knoten mit Uebergang zu einer Zone derselben Ebene (z. B.
    # DMZ hinter der Edge-Firewall) rueckt in die Spalte rechts daneben
    col = {z["id"]: LAYER.get(z.get("kind"), 0) * 2 for z in zones}
    by_id = {z["id"]: z for z in zones}
    for t in plan.get("transitions") or []:
        a, b = by_id.get(t["a"]), by_id.get(t["b"])
        if a is None or b is None or col[a["id"]] != col[b["id"]] or t.get("contact"):
            continue
        leaf = [z for z in (a, b) if not z.get("nodes") and isinstance(z["id"], int)]
        if len(leaf) == 1:
            col[leaf[0]["id"]] += 1
    cols: dict[int, list[dict]] = defaultdict(list)
    for z in zones:
        cols[col[z["id"]]].append(z)
    order = sorted(cols)
    boxes, pos = [], {}
    height = 0.0

    def box_h(z: dict) -> float:
        lines = _box_lines(z, nodes)
        return 42 + len(lines) * LINE_H + (6 if lines else 0)

    def first(c: int) -> list[dict]:
        return sorted(cols[c], key=lambda z: (not isinstance(z["id"], int),
                                              z.get("position") or 0, z["name"]))  # fmt: skip

    # Zwischenspalten beginnen unter der ersten Zeile - Uebergaenge zwischen den Ebenen bleiben frei
    top = max((box_h(first(c)[0]) for c in order if c % 2 == 0), default=42) + 34
    for ci, layer in enumerate(order):
        y = 12.0 if layer % 2 == 0 else 12.0 + top
        group = first(layer)
        for z in group:
            lines, h = _box_lines(z, nodes), box_h(z)
            box = {
                "id": z["id"], "x": 16 + ci * (ZW + GAP), "y": y, "w": ZW, "h": h,
                "title": z["name"], "sub": _sub(z), "lines": lines, "kind": z.get("kind"),
                "color": z.get("color"), "fill": _tint(z.get("color")) if z.get("color")
                else "#f4f6f9", "collapsed": bool(z.get("collapsed")),
                "pseudo": not isinstance(z["id"], int), "samples": z.get("samples") or [],
                "code": z.get("code"),
            }  # fmt: skip
            boxes.append(box)
            pos[z["id"]] = (ci, box)
            y += h + 22
        height = max(height, y)
    # Anschlusspunkte je Box und Seite gleichmaessig verteilen
    ends: dict[tuple, list] = defaultdict(list)
    plans = []
    for t in plan.get("transitions") or []:
        if t["a"] not in pos or t["b"] not in pos:
            continue
        (ca, a), (cb, b) = pos[t["a"]], pos[t["b"]]
        if cb < ca:
            (ca, a), (cb, b) = (cb, b), (ca, a)
        sa, sb = ("r", "l") if ca != cb else ("r", "r")
        plans.append((t, a, b, sa, sb, ca == cb))
        ends[(a["id"], sa)].append(t["id"])
        ends[(b["id"], sb)].append(t["id"])

    def at(box: dict, side: str, tid: str) -> tuple[float, float]:
        ids = ends[(box["id"], side)]
        k = ids.index(tid)
        y = box["y"] + 14 + (box["h"] - 20) * (k + 1) / (len(ids) + 1)
        return (box["x"] + box["w"] if side == "r" else box["x"]), y

    edges = []
    for n, (t, a, b, sa, sb, same) in enumerate(plans):
        x1, y1 = at(a, sa, t["id"])
        x2, y2 = at(b, sb, t["id"])
        if same:
            bend = x1 + 24 + (n % 4) * 8
            points = [[x1, y1], [bend, y1], [bend, y2], [x2, y2]]
            lx, ly = bend + 4, (y1 + y2) / 2
        else:
            points = [[x1, y1], [x2, y2]]
            lx, ly = (x1 + x2) / 2, (y1 + y2) / 2 - 5
        edges.append({
            "id": t["id"], "a": t["a"], "b": t["b"], "points": points,
            "x1": x1, "y1": y1, "x2": x2, "y2": y2, "lx": lx, "ly": ly,
            "label": "" if t.get("contact") else t["name"], "count": t.get("count") or 0,
            "networks": [x["network"] for x in t.get("networks") or []],
            "dashed": "outside" in (t["a"], t["b"]) or "none" in (t["a"], t["b"]),
            "contact": bool(t.get("contact")),
        })  # fmt: skip
    width = 16 + len(order) * (ZW + GAP) - GAP + 16 if order else W
    return {"format": 2, "width": width, "height": height + 12, "zones": boxes, "nodes": [],
            "edges": edges}  # fmt: skip


def _layout1(plan: dict) -> dict:
    """Layout von 0.52.0 (Versionen bis 0.55.0): Zonen links/rechts, Knoten in der Mitte."""
    zones, nodes = plan.get("zones") or [], plan.get("nodes") or []
    left = [z for z in zones if z.get("kind") not in RIGHT_KINDS]
    right = [z for z in zones if z.get("kind") in RIGHT_KINDS]
    rows = max(len(left), len(right), len(nodes), 1)

    def y(i: int, n: int) -> float:
        return 12 + (rows - n) * (ROW / 2) + i * ROW

    out_zones, out_nodes, edges = [], [], []
    pos: dict[str, dict] = {}
    for side, group, x in (("l", left, 16), ("r", right, W - ZW - 16)):
        for i, z in enumerate(group):
            box = {
                "id": z["id"], "x": x, "y": y(i, len(group)), "w": ZW, "h": BOX_H, "side": side,
                "title": z["name"], "color": z.get("color"), "fill": _tint(z.get("color")),
                "sub": f"{ZONE_KIND.get(z.get('kind'), z.get('kind'))} · "
                       f"{_plural(z.get('nets') or 0, 'Netz', 'Netze')}",
                "kind": z.get("kind"), "samples": z.get("samples") or [],
            }  # fmt: skip
            out_zones.append(box)
            pos[f"z{z['id']}"] = box
    for i, n in enumerate(nodes):
        box = {
            "id": n["id"], "x": (W - NW) / 2, "y": y(i, len(nodes)), "w": NW, "h": BOX_H,
            "title": n["label"],
            "sub": f"{NODE_KIND.get(n.get('kind'), 'Firewall')} · "
                   f"{_plural(n.get('zones') or 0, 'Zone', 'Zonen')}",
            "kind": n.get("kind"),
        }  # fmt: skip
        out_nodes.append(box)
        pos[n["id"]] = box
    for a in plan.get("attachments") or []:
        z, n = pos.get(f"z{a['zone']}"), pos.get(a["node"])
        if z is None or n is None:
            continue
        ifaces = a.get("interfaces") or []
        label = ", ".join(ifaces[:2]) + (" …" if len(ifaces) > 2 else "")
        edges.append({
            "zone": a["zone"], "node": a["node"],
            "x1": z["x"] + ZW if z["side"] == "l" else z["x"], "y1": z["y"] + BOX_H / 2,
            "x2": n["x"] if z["side"] == "l" else n["x"] + NW, "y2": n["y"] + BOX_H / 2,
            "label": label, "dashed": list(a.get("via") or []) == ["route"],
            "interfaces": ifaces, "networks": a.get("networks") or [],
        })  # fmt: skip
    return {"width": W, "height": rows * ROW + 24, "zones": out_zones, "nodes": out_nodes,
            "edges": edges}  # fmt: skip


def _stamp(title: str, when: datetime | None, version: dict | None) -> str:
    stand = f"Stand {when:%d.%m.%Y %H:%M}" if when else ""
    if version:
        label = f" – {version['label']}" if version.get("label") else ""
        return f"{title} · Version {version['number']}{label} · {stand}".rstrip(" ·")
    return f"{title} · {stand}".rstrip(" ·")


# -- SVG --------------------------------------------------------------------------------


def svg(plan: dict, title: str, when: datetime | None = None, version: dict | None = None) -> str:
    lay = layout(plan)
    width, height = lay["width"], lay["height"] + HEAD
    root = ET.Element("svg", {
        "xmlns": "http://www.w3.org/2000/svg", "viewBox": f"0 0 {width} {height}",
        "width": str(width), "height": str(height), "font-family": "Helvetica, Arial, sans-serif",
    })  # fmt: skip
    ET.SubElement(root, "title").text = title
    ET.SubElement(root, "rect", {"width": str(width), "height": str(height), "fill": "#ffffff"})
    head = ET.SubElement(root, "text", {"x": "16", "y": "26", "font-size": "16",
                                        "font-weight": "bold", "fill": FG})  # fmt: skip
    head.text = title
    sub = ET.SubElement(root, "text", {"x": "16", "y": "44", "font-size": "11", "fill": MUTED})
    sub.text = _stamp("Zonenplan", when, version)
    g = ET.SubElement(root, "g", {"transform": f"translate(0 {HEAD})"})
    if lay.get("format") == 2:
        _svg2(g, lay)
        return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")
    for e in lay["edges"]:
        attrs = {"x1": _n(e["x1"]), "y1": _n(e["y1"]), "x2": _n(e["x2"]), "y2": _n(e["y2"]),
                 "stroke": LINE, "stroke-width": "1.6"}  # fmt: skip
        if e["dashed"]:
            attrs["stroke-dasharray"] = "5 4"
        ET.SubElement(g, "line", attrs)
        if e["label"]:
            t = ET.SubElement(g, "text", {
                "x": _n((e["x1"] + e["x2"]) / 2), "y": _n((e["y1"] + e["y2"]) / 2 - 4),
                "text-anchor": "middle", "font-size": "10.5", "fill": MUTED,
                "font-family": "Menlo, monospace",
            })  # fmt: skip
            t.text = e["label"]
    for z in lay["zones"]:
        _svg_box(g, z, z["fill"], ZONE_STROKE, 8, "start")
    for n in lay["nodes"]:
        _svg_box(g, n, NODE_FILL, NODE_STROKE, 6, "middle")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def _n(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


def _text(g, x: float, y: float, text: str, size: str, color: str = FG, weight: str = "normal",
          anchor: str = "start", mono: bool = False) -> None:  # fmt: skip
    attrs = {"x": _n(x), "y": _n(y), "text-anchor": anchor, "font-size": size,
             "font-weight": weight, "fill": color}  # fmt: skip
    if mono:
        attrs["font-family"] = "Menlo, monospace"
    ET.SubElement(g, "text", attrs).text = text


def _svg2(g, lay: dict) -> None:
    """Zonenmodell (0.56.0): Uebergaenge als Linien, Zonen mit ihren Knoten."""
    for e in lay["edges"]:
        attrs = {"points": " ".join(f"{_n(x)},{_n(y)}" for x, y in e["points"]), "fill": "none",
                 "stroke": LINE, "stroke-width": "1.6"}  # fmt: skip
        if e["dashed"]:
            attrs["stroke-dasharray"] = "5 4"
        line = ET.SubElement(g, "polyline", attrs)
        ET.SubElement(line, "title").text = f"{e['label']}: " + ", ".join(e["networks"])
        bent = len(e["points"]) > 2
        _text(g, e["lx"], e["ly"], e["label"], "10.5", MUTED, anchor="start" if bent else "middle",
              mono=True)  # fmt: skip
    for z in lay["zones"]:
        attrs = {"x": _n(z["x"]), "y": _n(z["y"]), "width": _n(z["w"]), "height": _n(z["h"]),
                 "rx": "8", "fill": z["fill"], "stroke": _stroke(z.get("color")),
                 "stroke-width": "1.4"}  # fmt: skip
        if z["pseudo"]:
            attrs["stroke-dasharray"] = "4 3"
        ET.SubElement(g, "rect", attrs)
        _text(g, z["x"] + 12, z["y"] + 18, z["title"], "12.5", FG, "bold")
        _text(g, z["x"] + 12, z["y"] + 33, z["sub"], "10.5", MUTED)
        for k, line in enumerate(z["lines"]):
            _text(g, z["x"] + 12, z["y"] + 52 + k * LINE_H, line, "10.5", FG)


def _svg_box(g, b: dict, fill: str, stroke: str, radius: int, anchor: str) -> None:
    ET.SubElement(g, "rect", {"x": _n(b["x"]), "y": _n(b["y"]), "width": _n(b["w"]),
                              "height": _n(b["h"]), "rx": str(radius), "fill": fill,
                              "stroke": stroke, "stroke-width": "1.4"})  # fmt: skip
    x = b["x"] + (b["w"] / 2 if anchor == "middle" else 12)
    for text, dy, size, weight, color in ((b["title"], 18, "12.5", "bold", FG),
                                          (b["sub"], 33, "10.5", "normal", MUTED)):  # fmt: skip
        t = ET.SubElement(
            g,
            "text",
            {
                "x": _n(x),
                "y": _n(b["y"] + dy),
                "text-anchor": anchor,
                "font-size": size,
                "font-weight": weight,
                "fill": color,
            },
        )
        t.text = text


# -- draw.io ----------------------------------------------------------------------------


def drawio(plan: dict, title: str, when: datetime | None = None,
           version: dict | None = None) -> str:  # fmt: skip
    """mxGraph-Datei fuer diagrams.net/draw.io: Zonen und Knoten als Formen mit Eigenschaften
    (`automato_*`), Verbindungen als Kanten zwischen ihnen - in draw.io weiter bearbeitbar."""
    lay = layout(plan)
    stamp = _stamp("Zonenplan", when, version)
    if lay.get("format") == 2:
        return _drawio2(lay, title, stamp)
    mxfile = ET.Element("mxfile", {"host": "Automato", "type": "device"})
    diagram = ET.SubElement(mxfile, "diagram", {"id": "zonenplan", "name": "Zonenplan"})
    model = ET.SubElement(diagram, "mxGraphModel", {
        "grid": "1", "gridSize": "10", "guides": "1", "tooltips": "1", "connect": "1",
        "arrows": "0", "fold": "1", "page": "1", "pageScale": "1",
        "pageWidth": str(lay["width"]), "pageHeight": str(lay["height"] + HEAD),
        "math": "0", "shadow": "0",
    })  # fmt: skip
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", {"id": "0"})
    ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})
    head = ET.SubElement(root, "mxCell", {
        "id": "title", "value": f"<b>{_html(title)}</b><br>{_html(stamp)}", "vertex": "1",
        "parent": "1", "style": "text;html=1;align=left;verticalAlign=top;fontSize=14;",
    })  # fmt: skip
    _geometry(head, 16, 8, 600, 40)
    for z in lay["zones"]:
        obj = ET.SubElement(root, "UserObject", {
            "id": f"zone-{z['id']}", "label": f"<b>{_html(z['title'])}</b><br>{_html(z['sub'])}",
            "automato_zone": z["title"], "automato_kind": ZONE_KIND.get(z["kind"], z["kind"] or ""),
            "automato_networks": ", ".join(z["samples"]),
            "tooltip": "\n".join(z["samples"]),
        })  # fmt: skip
        cell = ET.SubElement(obj, "mxCell", {
            "vertex": "1", "parent": "1",
            "style": f"rounded=1;whiteSpace=wrap;html=1;align=left;spacingLeft=10;"
                     f"fillColor={z['fill']};strokeColor={ZONE_STROKE};fontSize=12;",
        })  # fmt: skip
        _geometry(cell, z["x"], z["y"] + HEAD, z["w"], z["h"])
    for n in lay["nodes"]:
        obj = ET.SubElement(root, "UserObject", {
            "id": f"node-{_id(n['id'])}",
            "label": f"<b>{_html(n['title'])}</b><br>{_html(n['sub'])}",
            "automato_node": n["title"], "automato_kind": NODE_KIND.get(n["kind"], "Firewall"),
        })  # fmt: skip
        cell = ET.SubElement(obj, "mxCell", {
            "vertex": "1", "parent": "1",
            "style": f"rounded=1;arcSize=12;whiteSpace=wrap;html=1;fillColor={NODE_FILL};"
                     f"strokeColor={NODE_STROKE};strokeWidth=1.5;fontSize=12;",
        })  # fmt: skip
        _geometry(cell, n["x"], n["y"] + HEAD, n["w"], n["h"])
    for i, e in enumerate(lay["edges"]):
        style = f"endArrow=none;html=1;strokeColor={LINE};fontSize=10;fontFamily=Courier New;"
        if e["dashed"]:
            style += "dashed=1;"
        obj = ET.SubElement(root, "UserObject", {
            "id": f"edge-{i}", "label": _html(e["label"]),
            "automato_interfaces": ", ".join(e["interfaces"]),
            "automato_networks": ", ".join(e["networks"]),
        })  # fmt: skip
        cell = ET.SubElement(obj, "mxCell", {
            "edge": "1", "parent": "1", "style": style,
            "source": f"zone-{e['zone']}", "target": f"node-{_id(e['node'])}",
        })  # fmt: skip
        ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(mxfile, encoding="unicode")


def _drawio2(lay: dict, title: str, stamp: str) -> str:
    """draw.io des Zonenmodells: Zonen (mit Knoten) und Uebergaenge als Kanten mit den Netzen;
    als Grundschema wieder einlesbar (Zonen ja, Uebergaenge und Hilfsformen nicht)."""
    mxfile = ET.Element("mxfile", {"host": "Automato", "type": "device"})
    diagram = ET.SubElement(mxfile, "diagram", {"id": "zonenplan", "name": "Zonenplan"})
    model = ET.SubElement(diagram, "mxGraphModel", {
        "grid": "1", "gridSize": "10", "guides": "1", "tooltips": "1", "connect": "1",
        "arrows": "0", "fold": "1", "page": "1", "pageScale": "1",
        "pageWidth": _n(lay["width"]), "pageHeight": _n(lay["height"] + HEAD), "math": "0",
        "shadow": "0",
    })  # fmt: skip
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", {"id": "0"})
    ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})
    head = ET.SubElement(root, "mxCell", {
        "id": "title", "value": f"<b>{_html(title)}</b><br>{_html(stamp)}", "vertex": "1",
        "parent": "1", "style": "text;html=1;align=left;verticalAlign=top;fontSize=14;",
    })  # fmt: skip
    _geometry(head, 16, 8, 600, 40)
    for z in lay["zones"]:
        lines = "".join(f"<br>{_html(x)}" for x in z["lines"])
        attrs = {"id": f"zone-{_id(str(z['id']))}",
                 "label": f"<b>{_html(z['title'])}</b><br><i>{_html(z['sub'])}</i>{lines}",
                 "automato_kind": ZONE_KIND.get(z["kind"], z["kind"] or "")}  # fmt: skip
        if z["pseudo"]:
            attrs["automato_ignore"] = "1"  # Hilfsform: kein Teil des Grundschemas
        else:
            attrs.update({"automato_zone": z["title"], "automato_code": z.get("code") or "",
                          "automato_networks": ", ".join(z["samples"]),
                          "automato_nodes": ", ".join(z["lines"])})  # fmt: skip
        obj = ET.SubElement(root, "UserObject", attrs)
        style = (
            f"rounded=1;whiteSpace=wrap;html=1;align=left;verticalAlign=top;spacingLeft=10;"
            f"fillColor={z['fill']};strokeColor={_stroke(z.get('color'))};fontSize=12;"
        )
        if z["pseudo"]:
            style += "dashed=1;"
        cell = ET.SubElement(obj, "mxCell", {"vertex": "1", "parent": "1", "style": style})
        _geometry(cell, z["x"], z["y"] + HEAD, z["w"], z["h"])
    for e in lay["edges"]:
        style = f"endArrow=none;html=1;strokeColor={LINE};fontSize=10;fontFamily=Courier New;"
        if e["dashed"]:
            style += "dashed=1;"
        obj = ET.SubElement(root, "UserObject", {
            "id": f"transition-{_id(e['id'])}", "label": _html(e["label"]),
            "automato_transition": e["label"], "automato_networks": ", ".join(e["networks"]),
        })  # fmt: skip
        cell = ET.SubElement(obj, "mxCell", {
            "edge": "1", "parent": "1", "style": style,
            "source": f"zone-{_id(str(e['a']))}", "target": f"zone-{_id(str(e['b']))}",
        })  # fmt: skip
        ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(mxfile, encoding="unicode")


def _id(node: str) -> str:
    return node.replace("|", "-").replace(":", "-").replace(" ", "_")


def _html(text: str) -> str:
    """Text fuer ein HTML-Label (draw.io) - ET maskiert danach fuer das Attribut."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _geometry(cell, x: float, y: float, w: float, h: float) -> None:
    ET.SubElement(cell, "mxGeometry", {"x": _n(x), "y": _n(y), "width": _n(w), "height": _n(h),
                                       "as": "geometry"})  # fmt: skip


# -- PDF --------------------------------------------------------------------------------

# Breiten der Standardschrift Helvetica (AFM, 1/1000 em) fuer die Zeichen 32..126
_WIDTHS = [
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556,
    556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722,
    722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722,
    667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556,
    556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500,
    500, 334, 260, 334, 584,
]  # fmt: skip
_REPLACE = {"→": "->", "←": "<-", "↔": "<->", "✓": "+", "✕": "x", "≠": "!="}


def text_width(text: str, size: float, bold: bool = False) -> float:
    w = sum(_WIDTHS[ord(c) - 32] if 32 <= ord(c) <= 126 else 556 for c in text)
    return w * size / 1000 * (1.05 if bold else 1)


def _fit(text: str, size: float, width: float, bold: bool = False) -> str:
    if text_width(text, size, bold) <= width:
        return text
    while text and text_width(text + "…", size, bold) > width:
        text = text[:-1]
    return text + "…"


def _rgb(color: str) -> str:
    c = color.lstrip("#")
    return " ".join(f"{int(c[i : i + 2], 16) / 255:.3f}" for i in (0, 2, 4))


def _rounded(x: float, y: float, w: float, h: float, r: float) -> list[str]:
    """Pfad eines Rechtecks mit runden Ecken (Bezier, Ursprung unten links)."""
    k = r * 0.5523  # Naeherung des Viertelkreises

    def pt(*v: float) -> str:
        return " ".join(f"{c:.2f}" for c in v)

    x2, y2 = x + w, y + h
    return [
        f"{pt(x + r, y)} m", f"{pt(x2 - r, y)} l",
        f"{pt(x2 - r + k, y, x2, y + r - k, x2, y + r)} c", f"{pt(x2, y2 - r)} l",
        f"{pt(x2, y2 - r + k, x2 - r + k, y2, x2 - r, y2)} c", f"{pt(x + r, y2)} l",
        f"{pt(x + r - k, y2, x, y2 - r + k, x, y2 - r)} c", f"{pt(x, y + r)} l",
        f"{pt(x, y + r - k, x + r - k, y, x + r, y)} c", "h",
    ]  # fmt: skip


class _Page:
    def __init__(self, width: float, height: float):
        self.width, self.height = width, height
        self.ops: list[str] = []

    def _y(self, y: float) -> float:
        return self.height - y

    def rect(self, x, y, w, h, fill=None, stroke=None, radius=0.0, line=1.0) -> None:
        y0 = self._y(y + h)
        if radius:
            path = _rounded(x, y0, w, h, radius)
        else:
            path = [f"{x:.2f} {y0:.2f} {w:.2f} {h:.2f} re"]
        paint = "B" if fill and stroke else ("f" if fill else "S")
        self.ops.append("q " + (f"{_rgb(fill)} rg " if fill else "")
                        + (f"{_rgb(stroke)} RG {line:.2f} w " if stroke else "")
                        + " ".join(path) + f" {paint} Q")  # fmt: skip

    def line(self, x1, y1, x2, y2, color=LINE, width=1.0, dashed=False) -> None:
        dash = "[5 4] 0 d " if dashed else ""
        self.ops.append(f"q {_rgb(color)} RG {width:.2f} w {dash}{x1:.2f} {self._y(y1):.2f} m "
                        f"{x2:.2f} {self._y(y2):.2f} l S Q")  # fmt: skip

    def text(self, x, y, text, size=10.0, color=FG, bold=False, anchor="start",
             width: float | None = None) -> None:  # fmt: skip
        for a, b in _REPLACE.items():
            text = text.replace(a, b)
        if width is not None:
            text = _fit(text, size, width, bold)
        if anchor == "middle":
            x -= text_width(text, size, bold) / 2
        raw = text.encode("cp1252", errors="replace")
        esc = raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
        self.ops.append(
            f"BT /{'F2' if bold else 'F1'} {size:.2f} Tf {_rgb(color)} rg "
            f"{x:.2f} {self._y(y):.2f} Td (" + esc.decode("latin-1") + ") Tj ET"
        )


class _Pdf:
    """Kleiner PDF-Schreiber: Seiten mit Linien, Rechtecken und Text (Helvetica, WinAnsi)."""

    def __init__(self, title: str):
        self.title = title
        self.pages: list[_Page] = []

    def page(self, width: float = 842, height: float = 595) -> _Page:  # A4 quer
        p = _Page(width, height)
        self.pages.append(p)
        return p

    def bytes(self) -> bytes:
        objs: list[bytes] = []

        def add(body: bytes) -> int:
            objs.append(body)
            return len(objs)

        add(b"")  # 1: Katalog (unten)
        add(b"")  # 2: Seiten (unten)
        f1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                 b"/Encoding /WinAnsiEncoding >>")  # fmt: skip
        f2 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
                 b"/Encoding /WinAnsiEncoding >>")  # fmt: skip
        kids = []
        for p in self.pages:
            data = zlib.compress("\n".join(p.ops).encode("latin-1"))
            content = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data) + data
                          + b"\nendstream")  # fmt: skip
            kids.append(add(
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] " % (p.width, p.height)
                + b"/Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> /Contents %d 0 R >>"
                % (f1, f2, content)
            ))  # fmt: skip
        title = self.title.encode("cp1252", errors="replace")
        title = title.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
        info = add(b"<< /Producer (Automato) /Title (" + title + b") >>")
        objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
        objs[1] = (b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids)
                   + b"] /Count %d >>" % len(kids))  # fmt: skip
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objs, start=1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
        out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
        out += (b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
                % (len(objs) + 1, info, xref))  # fmt: skip
        return bytes(out)


def pdf(plan: dict, matrix: dict | None, title: str, when: datetime | None = None,
        version: dict | None = None) -> bytes:  # fmt: skip
    """PDF: Seite 1 der Plan, danach Zonenmatrix, Zonen an den Knoten und Netze ohne Zone."""
    doc = _Pdf(title)
    lay = layout(plan)
    stamp = _stamp("Zonenplan", when, version)
    p = doc.page(lay["width"], lay["height"] + HEAD)
    p.text(16, 26, title, 16, bold=True)
    p.text(16, 44, stamp, 10, MUTED)
    if lay.get("format") == 2:
        _pdf2(doc, p, plan, lay, matrix, stamp)
        return doc.bytes()
    for e in lay["edges"]:
        p.line(e["x1"], e["y1"] + HEAD, e["x2"], e["y2"] + HEAD, LINE, 1.4, e["dashed"])
    for e in lay["edges"]:
        if e["label"]:
            p.text((e["x1"] + e["x2"]) / 2, (e["y1"] + e["y2"]) / 2 - 4 + HEAD, e["label"], 9,
                   MUTED, anchor="middle")  # fmt: skip
    for z in lay["zones"]:
        p.rect(z["x"], z["y"] + HEAD, z["w"], z["h"], z["fill"], ZONE_STROKE, 8)
        p.text(z["x"] + 12, z["y"] + HEAD + 18, z["title"], 11, bold=True, width=z["w"] - 20)
        p.text(z["x"] + 12, z["y"] + HEAD + 32, z["sub"], 9, MUTED, width=z["w"] - 20)
    for n in lay["nodes"]:
        cx = n["x"] + n["w"] / 2
        p.rect(n["x"], n["y"] + HEAD, n["w"], n["h"], NODE_FILL, NODE_STROKE, 6, 1.3)
        p.text(cx, n["y"] + HEAD + 18, n["title"], 11, bold=True, anchor="middle",
               width=n["w"] - 16)  # fmt: skip
        p.text(cx, n["y"] + HEAD + 32, n["sub"], 9, MUTED, anchor="middle", width=n["w"] - 16)
    if matrix:
        _matrix_page(doc, matrix, stamp)
    _table_pages(doc, "Zonen an den Knoten", stamp, ["Zone", "Knoten", "über", "Interfaces",
                 "Netze"], _attachment_rows(plan), [130, 170, 110, 150, 222])  # fmt: skip
    unzoned = plan.get("unzoned") or {}
    rows = [[u["network"], u.get("node") or "", u.get("where") or ""]
            for u in unzoned.get("items") or []]  # fmt: skip
    if rows:
        more = unzoned.get("total", 0) - len(rows)
        _table_pages(
            doc,
            "Netze ohne Zone" + (f" (die ersten {len(rows)})" if more > 0 else ""),
            stamp,
            ["Netz", "Knoten", "Interface / Ort"],
            rows,
            [200, 250, 332],
        )
    return doc.bytes()


def _pdf2(doc: "_Pdf", p: "_Page", plan: dict, lay: dict, matrix: dict | None,
          stamp: str) -> None:  # fmt: skip
    """PDF des Zonenmodells: Plan, Matrix, Zonen mit Knoten, Uebergaenge mit Netzen."""
    for e in lay["edges"]:
        pts = e["points"]
        for (x1, y1), (x2, y2) in zip(pts, pts[1:], strict=False):
            p.line(x1, y1 + HEAD, x2, y2 + HEAD, LINE, 1.4, e["dashed"])
        p.text(e["lx"], e["ly"] + HEAD, e["label"], 9, MUTED,
               anchor="start" if len(pts) > 2 else "middle")  # fmt: skip
    for z in lay["zones"]:
        p.rect(z["x"], z["y"] + HEAD, z["w"], z["h"], z["fill"], _stroke(z.get("color")), 8)
        p.text(z["x"] + 12, z["y"] + HEAD + 18, z["title"], 11, bold=True, width=z["w"] - 20)
        p.text(z["x"] + 12, z["y"] + HEAD + 32, z["sub"], 9, MUTED, width=z["w"] - 20)
        for k, line in enumerate(z["lines"]):
            p.text(z["x"] + 12, z["y"] + HEAD + 52 + k * LINE_H, line, 9, width=z["w"] - 20)
    if matrix:
        _matrix_page(doc, matrix, stamp)
    nodes = {n["id"]: n["label"] for n in plan.get("nodes") or []}
    zone_rows = [[z["name"], ZONE_KIND.get(z.get("kind"), z.get("kind") or ""),
                  ", ".join(nodes.get(n, n) for n in z.get("nodes") or []) or "-",
                  ", ".join(z.get("samples") or []) + (f" ... ({z['nets']})"
                  if (z.get("nets") or 0) > len(z.get("samples") or []) else "")]
                 for z in plan.get("zones") or [] if isinstance(z["id"], int)]  # fmt: skip
    _table_pages(doc, "Zonen", stamp, ["Zone", "Art", "Knoten", "Netze in der Zone"], zone_rows,
                 [150, 90, 260, 282])  # fmt: skip
    rows = []
    for t in plan.get("transitions") or []:
        for n in t.get("networks") or []:
            via = ", ".join(
                f"{leg['label']} ({'L3Out' if leg['l3out'] else 'Interface'} "
                f"{leg.get('interface') or '?'})"
                for leg in n.get("legs") or []
            )
            routers = ", ".join((r.get("names") or [r["ip"]])[0] for r in n.get("routers") or [])
            rows.append([t["name"], n["network"], via, routers])
    _table_pages(doc, "Übergänge", stamp, ["Übergang", "Netz", "über", "Router"], rows,
                 [140, 130, 360, 152])  # fmt: skip
    unzoned = plan.get("unzoned") or {}
    rest = [[u["network"], u.get("node") or "", u.get("where") or ""]
            for u in unzoned.get("items") or []]  # fmt: skip
    if rest:
        _table_pages(doc, "Netze ohne Zone", stamp, ["Netz", "Knoten", "Interface / Ort"], rest,
                     [200, 250, 332])  # fmt: skip


_VIA = {"interface": "Interface", "fabric": "Fabric", "exit": "Anbindung", "route": "Route"}


def _attachment_rows(plan: dict) -> list[list[str]]:
    zones = {z["id"]: z["name"] for z in plan.get("zones") or []}
    nodes = {n["id"]: n["label"] for n in plan.get("nodes") or []}
    rows = []
    for a in plan.get("attachments") or []:
        nets = ", ".join(a.get("networks") or [])
        if (a.get("count") or 0) > len(a.get("networks") or []):
            nets += f" … ({a['count']})"
        via = ", ".join(_VIA.get(v, v) for v in a.get("via") or [])
        if a.get("default"):
            via += " (Default)"
        rows.append([zones.get(a["zone"], "?"), nodes.get(a["node"], a["node"]), via,
                     ", ".join(a.get("interfaces") or []), nets])  # fmt: skip
    seen = {a["zone"] for a in plan.get("attachments") or []}
    rows += [[z["name"], "an keinem gelesenen Knoten", "", "", ""]
             for z in plan.get("zones") or [] if z["id"] not in seen]  # fmt: skip
    return rows


def _table_pages(doc: _Pdf, heading: str, stamp: str, header: list[str], rows: list[list[str]],
                 widths: list[float]) -> None:  # fmt: skip
    per_page = 34
    for start in range(0, max(len(rows), 1), per_page):
        p = doc.page()
        p.text(28, 34, heading, 14, bold=True)
        p.text(28, 50, stamp, 9, MUTED)
        x, y = 28.0, 74.0
        for h, w in zip(header, widths, strict=True):
            p.text(x, y, h, 8.5, MUTED, bold=True, width=w - 6)
            x += w
        p.line(28, y + 5, 814, y + 5, ZONE_STROKE, 0.8)
        for row in rows[start : start + per_page]:
            y += 14.5
            x = 28.0
            for cell, w in zip(row, widths, strict=True):
                p.text(x, y, cell, 8.5, width=w - 6)
                x += w
        if not rows:
            p.text(28, y + 16, "keine", 9, MUTED)


def _matrix_page(doc: _Pdf, matrix: dict, stamp: str) -> None:
    zones = {z["id"]: z for z in matrix.get("zones") or []}
    axes = matrix.get("axes") or []

    def name(c) -> str:
        return {"any": "any", "none": "ohne Zone"}.get(c, zones.get(c, {}).get("name", f"#{c}"))

    cells = {(c["src"], c["dst"]): c for c in matrix.get("cells") or []}
    p = doc.page()
    p.text(28, 34, "Zonenmatrix", 14, bold=True)
    s = matrix.get("summary") or {}
    p.text(28, 50, f"{stamp} · {s.get('assigned', 0)} von {s.get('rules', 0)} Regeln zugeordnet "
                   f"· {s.get('violations', 0)} gegen die Absicht", 9, MUTED)  # fmt: skip
    p.text(
        28,
        64,
        "Je Zelle erlaubende / sperrende Regeln; Hintergrund = Absicht (grün erlaubt, "
        "gelb eingeschränkt, rot gesperrt), rot umrandet = gegen die Absicht",
        8,
        MUTED,
    )
    label_w = 120.0
    col = max(26.0, min(70.0, (842 - 56 - label_w) / max(len(axes), 1)))
    size = 8 if col >= 40 else 6.5
    top = 84.0
    row_h = 18.0 if len(axes) <= 26 else max(11.0, (595 - top - 30) / (len(axes) + 1))
    fills = {"allow": "#d8efe0", "limited": "#f6ecd2", "deny": "#f7dcd9"}
    for j, d in enumerate(axes):  # Kopf: Zielzonen
        p.text(28 + label_w + j * col + col / 2, top + 10, name(d), size, MUTED, bold=True,
               anchor="middle", width=col - 2)  # fmt: skip
    for i, src in enumerate(axes):
        y = top + (i + 1) * row_h
        p.text(28, y + row_h / 2 + 3, name(src), size, bold=True, width=label_w - 6)
        for j, dst in enumerate(axes):
            x = 28 + label_w + j * col
            c = cells.get((src, dst)) or {}
            fill = fills.get(c.get("intent") or "", "#eef1f6")
            stroke = "#bb362c" if c.get("violations") else None
            p.rect(x + 1, y + 1, col - 2, row_h - 2, fill, stroke, 2, 1.2)
            if c.get("allow") or c.get("deny"):
                p.text(x + col / 2, y + row_h / 2 + 3, f"{c.get('allow', 0)} / {c.get('deny', 0)}",
                       size, FG, anchor="middle", width=col - 4)  # fmt: skip
    notes = [c for c in matrix.get("cells") or [] if c.get("note")]
    y = top + (len(axes) + 1) * row_h + 18
    for c in notes[:12]:
        if y > 580:
            break
        p.text(28, y, f"{name(c['src'])} -> {name(c['dst'])}: {INTENT.get(c.get('intent'), '')} "
                      f"– {c['note']}", 8, MUTED, width=786)  # fmt: skip
        y += 12
