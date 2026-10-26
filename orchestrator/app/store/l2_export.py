"""Export des Plans der Verkabelung (0.65.0, ADR 0025 Nr. 10): SVG und draw.io - dieselbe Lage
wie in der Ansicht (`l2_topo.L2.plan`), mit vPC-Paaren, Kabeln je Geraetepaar und, mit VLAN,
dessen Zustand je Kante. draw.io mit `automato_*`-Eigenschaften, dort weiter bearbeitbar."""

import xml.etree.ElementTree as ET
from datetime import datetime

from app.store.plan_export import _geometry, _html, _id, _n

HEAD = 56
NODE_W, NODE_H = 160, 44
FG, MUTED, FAINT = "#1f2933", "#52606d", "#9aa5b1"
FILL_SW, FILL_END, STROKE_SW, STROKE_END = "#f4f6f8", "#ffffff", "#1565c0", "#9aa5b1"
EDGE = {"yes": "#2e7d32", "no": "#c62828", "partial": "#ef6c00", "standby": "#7b1fa2",
        "unknown": "#9aa5b1"}  # fmt: skip
KIND = {"switch": "Switch", "leaf": "ACI-Leaf", "spine": "ACI-Spine", "controller": "APIC",
        "firewall": "Firewall", "esxi": "ESXi-Host", "unknown": "nicht gelesen"}  # fmt: skip


def _stamp(plan: dict, when: datetime | None) -> str:
    parts = [f"Stand {when:%d.%m.%Y %H:%M}" if when else "Stand"]
    parts.append(f"{sum(1 for n in plan['nodes'] if n['switching'])} weiterleitende Geräte")
    parts.append(f"{sum(e['links'] for e in plan['edges'])} Kabel")
    if plan.get("vlan"):
        v = plan["vlan"]
        state = "durchgehend" if len(v["islands"]) <= 1 else f"{len(v['islands'])} Inseln"
        parts.append(f"VLAN {v['vid']}: {state}")
    return " · ".join(parts)


def _color(e: dict) -> tuple[str, str | None]:
    """(Farbe, Strichart) einer Kante."""
    if e.get("vlan"):
        dash = {"no": "6 4", "standby": "3 3", "unknown": "2 4"}.get(e["vlan"])
        return EDGE[e["vlan"]], dash
    if e["issues"]:
        return EDGE["no"], None
    if e.get("stp"):
        return FAINT, "3 3"
    return FAINT if e["state"] == "unread" else MUTED, None


def _edge_label(e: dict) -> str:
    bits = [f"{e['links']}×"] if e["links"] > 1 else []
    bits += e.get("lags") or []
    if e.get("stp"):
        bits.append(f"STP blockiert {e['stp']}")
    return " ".join(bits)


def svg(plan: dict, title: str, when: datetime | None = None) -> str:
    width = max(plan["width"], 400)
    height = plan["height"] + HEAD
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
    sub.text = _stamp(plan, when)
    g = ET.SubElement(root, "g", {"transform": f"translate(0 {HEAD})"})
    pos = {n["key"]: n for n in plan["nodes"]}
    for pr in plan.get("pairs") or []:
        a, b = pos.get(pr["a"]), pos.get(pr["b"])
        if not a or not b:
            continue
        x0, x1 = min(a["x"], b["x"]) - NODE_W / 2 - 8, max(a["x"], b["x"]) + NODE_W / 2 + 8
        y0 = min(a["y"], b["y"]) - NODE_H / 2 - 16
        ET.SubElement(g, "rect", {"x": _n(x0), "y": _n(y0), "width": _n(x1 - x0),
                                  "height": _n(NODE_H + 24), "rx": "10", "fill": "none",
                                  "stroke": STROKE_SW, "stroke-dasharray": "4 3"})  # fmt: skip
        t = ET.SubElement(g, "text", {"x": _n(x0 + 6), "y": _n(y0 + 11), "font-size": "10",
                                      "fill": STROKE_SW})  # fmt: skip
        t.text = f"vPC-Domäne {pr['domain']}"
    for e in plan["edges"]:
        a, b = pos.get(e["a"]), pos.get(e["b"])
        if not a or not b:
            continue
        color, dash = _color(e)
        attrs = {"x1": _n(a["x"]), "y1": _n(a["y"]), "x2": _n(b["x"]), "y2": _n(b["y"]),
                 "stroke": color, "stroke-width": _n(1 + min(e["links"], 4))}  # fmt: skip
        if dash:
            attrs["stroke-dasharray"] = dash
        ET.SubElement(g, "line", attrs)
        label = _edge_label(e)
        if label:
            t = ET.SubElement(g, "text", {"x": _n((a["x"] + b["x"]) / 2 + 6),
                                          "y": _n((a["y"] + b["y"]) / 2 - 4), "font-size": "10",
                                          "fill": MUTED})  # fmt: skip
            t.text = label
    for n in plan["nodes"]:
        x, y = n["x"] - NODE_W / 2, n["y"] - NODE_H / 2
        stroke = "#2e7d32" if n.get("vlan_present") else STROKE_SW if n["switching"] else STROKE_END
        attrs = {"x": _n(x), "y": _n(y), "width": str(NODE_W), "height": str(NODE_H), "rx": "6",
                 "fill": FILL_SW if n["switching"] else FILL_END, "stroke": stroke,
                 "stroke-width": "2.5" if n.get("vlan_present") else "1.5"}  # fmt: skip
        if not n["read"]:
            attrs["stroke-dasharray"] = "4 3"
        ET.SubElement(g, "rect", attrs)
        name = ET.SubElement(g, "text", {"x": _n(n["x"]), "y": _n(y + 19), "font-size": "12",
                                         "font-weight": "bold", "text-anchor": "middle",
                                         "fill": FG})  # fmt: skip
        name.text = n["name"] if len(n["name"]) <= 22 else n["name"][:21] + "…"
        kind = ET.SubElement(g, "text", {"x": _n(n["x"]), "y": _n(y + 35), "font-size": "10",
                                         "text-anchor": "middle", "fill": MUTED})  # fmt: skip
        kind.text = KIND.get(n["kind"], n["kind"])
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def drawio(plan: dict, title: str, when: datetime | None = None) -> str:
    """mxGraph fuer diagrams.net/draw.io: Geraete als Formen, Kabel als Kanten mit Ports."""
    width = max(plan["width"], 400)
    mxfile = ET.Element("mxfile", {"host": "Automato", "type": "device"})
    diagram = ET.SubElement(mxfile, "diagram", {"id": "verkabelung", "name": "Verkabelung"})
    model = ET.SubElement(diagram, "mxGraphModel", {
        "grid": "1", "gridSize": "10", "guides": "1", "tooltips": "1", "connect": "1",
        "arrows": "0", "fold": "1", "page": "1", "pageScale": "1", "pageWidth": _n(width),
        "pageHeight": _n(plan["height"] + HEAD), "math": "0", "shadow": "0",
    })  # fmt: skip
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", {"id": "0"})
    ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})
    head = ET.SubElement(root, "mxCell", {
        "id": "title", "value": f"<b>{_html(title)}</b><br>{_html(_stamp(plan, when))}",
        "vertex": "1", "parent": "1",
        "style": "text;html=1;align=left;verticalAlign=top;fontSize=14;",
    })  # fmt: skip
    _geometry(head, 16, 8, 600, 40)
    pos = {n["key"]: n for n in plan["nodes"]}
    for i, pr in enumerate(plan.get("pairs") or []):
        a, b = pos.get(pr["a"]), pos.get(pr["b"])
        if not a or not b:
            continue
        x0 = min(a["x"], b["x"]) - NODE_W / 2 - 8
        w = abs(a["x"] - b["x"]) + NODE_W + 16
        cell = ET.SubElement(root, "mxCell", {
            "id": f"vpc-{i}", "value": f"vPC-Domäne {_html(str(pr['domain']))}", "vertex": "1",
            "parent": "1",
            "style": f"rounded=1;dashed=1;fillColor=none;strokeColor={STROKE_SW};"
                     "verticalAlign=top;align=left;spacingLeft=6;fontSize=10;html=1;",
        })  # fmt: skip
        _geometry(cell, x0, min(a["y"], b["y"]) - NODE_H / 2 - 16 + HEAD, w, NODE_H + 24)
    for n in plan["nodes"]:
        obj = ET.SubElement(root, "UserObject", {
            "id": f"dev-{_id(n['key'])}",
            "label": f"<b>{_html(n['name'])}</b><br>{_html(KIND.get(n['kind'], n['kind']))}",
            "automato_device": n["name"], "automato_kind": KIND.get(n["kind"], n["kind"]),
            "automato_source": n.get("source") or "",
        })  # fmt: skip
        stroke = STROKE_SW if n["switching"] else STROKE_END
        style = (f"rounded=1;arcSize=12;whiteSpace=wrap;html=1;strokeColor={stroke};"
                 f"fillColor={FILL_SW if n['switching'] else FILL_END};fontSize=12;")  # fmt: skip
        if not n["read"]:
            style += "dashed=1;"
        cell = ET.SubElement(obj, "mxCell", {"vertex": "1", "parent": "1", "style": style})
        _geometry(cell, n["x"] - NODE_W / 2, n["y"] - NODE_H / 2 + HEAD, NODE_W, NODE_H)
    for i, e in enumerate(plan["edges"]):
        if e["a"] not in pos or e["b"] not in pos:
            continue
        color, dash = _color(e)
        style = (f"endArrow=none;html=1;strokeColor={color};"
                 f"strokeWidth={1 + min(e['links'], 4)};fontSize=10;")  # fmt: skip
        if dash:
            style += "dashed=1;"
        ports = ", ".join(f"{p[0]}–{p[1]}" for p in e.get("ports") or [])
        obj = ET.SubElement(root, "UserObject", {
            "id": f"cable-{i}", "label": _html(_edge_label(e)), "automato_ports": ports,
            "automato_links": str(e["links"]), "automato_stp_blocked": e.get("stp") or "",
            "tooltip": ports,
        })  # fmt: skip
        cell = ET.SubElement(obj, "mxCell", {
            "edge": "1", "parent": "1", "style": style,
            "source": f"dev-{_id(e['a'])}", "target": f"dev-{_id(e['b'])}",
        })  # fmt: skip
        ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(mxfile, encoding="unicode")
