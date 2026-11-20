"""Export der Plaene je Schicht als draw.io (1.23.0, ADR 0067 Nachtrag 1.23.0) - dieselbe Lage
wie in der Ansicht (`store/layer_plans`), je Standort ein Rahmen, die Ebenen des Plans als
Ebenen in draw.io (ein- und ausblendbar; was in der Ansicht aus ist, kommt ausgeblendet mit).
Elemente tragen `automato_*`-Eigenschaften und bleiben in draw.io bearbeitbar. Beschriftungen in
der Sprache der Anfrage (ADR 0028); Werte (`automato_kind`) bleiben wie gelesen."""

import re
import xml.etree.ElementTree as ET
from datetime import datetime

from app.i18n import L
from app.store.plan_export import _geometry, _html, _n

HEAD = 56
FG, MUTED = "#1f2933", "#52606d"
TONE = {
    "line": "#52606d",
    "muted": "#9aa5b1",
    "warn": "#ef6c00",
    "bad": "#c62828",
    "stp": "#ef6c00",
    "netbox": "#7b1fa2",
    "bgp": "#6a1b9a",
    "vpn": "#00838f",
    "static": "#1565c0",
    "route": "#8d6e63",
}
FILL = {
    "switch": "#e3f2fd",
    "leaf": "#e3f2fd",
    "spine": "#e3f2fd",
    "firewall": "#fdecea",
    "vdom": "#fdecea",
    "router": "#f3e5f5",
    "l3": "#f3e5f5",
    "vrf": "#e8f5e9",
    "esxi": "#f1f8e9",
    "segment": "#fff8e1",
    "net": "#fffde7",
    "more": "#fffde7",
    "exit": "#ede7f6",
    "vpn_peer": "#e0f7fa",
}
STROKE_GROUP = "#9aa5b1"


def stamp(plan: dict, when: datetime | None, site_text: str) -> str:
    s = plan["summary"]
    parts = [
        f"{L('Stand', 'As of')} {when:%d.%m.%Y %H:%M}" if when else L("Stand", "As of"),
        L(f"Standort: {site_text}", f"Site: {site_text}"),
        L(f"{sum(s['nodes'].values())} Elemente", f"{sum(s['nodes'].values())} elements"),
        L(f"{sum(s['edges'].values())} Linien", f"{sum(s['edges'].values())} lines"),
    ]
    lim = plan["limits"]
    if lim["nodes_shown"] < lim["nodes_total"]:
        parts.append(
            L(
                f"Grenze: {lim['nodes_shown']} von {lim['nodes_total']} Elementen",
                f"Limit: {lim['nodes_shown']} of {lim['nodes_total']} elements",
            )
        )
    return " · ".join(parts)


def _layer_id(key: str | None) -> str:
    return "1" if key is None else f"layer-{re.sub(r'[^a-z0-9-]', '', key)}"


def drawio(
    plan: dict,
    title: str,
    site_text: str,
    when: datetime | None = None,
    hidden: set[str] | frozenset = frozenset(),
) -> str:
    """mxGraph fuer diagrams.net/draw.io: Rahmen je Standort, Elemente, Linien mit Ports und
    Texten - je Ebene des Plans eine Ebene in draw.io."""
    width, height = max(plan["width"], 400), plan["height"] + HEAD
    mxfile = ET.Element("mxfile", {"host": "Automato", "type": "device"})
    diagram = ET.SubElement(mxfile, "diagram", {"id": f"plan-{plan['layer']}", "name": title})
    model = ET.SubElement(
        diagram,
        "mxGraphModel",
        {
            "grid": "1",
            "gridSize": "10",
            "guides": "1",
            "tooltips": "1",
            "connect": "1",
            "arrows": "0",
            "fold": "1",
            "page": "1",
            "pageScale": "1",
            "pageWidth": _n(width),
            "pageHeight": _n(height),
            "math": "0",
            "shadow": "0",
        },
    )
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", {"id": "0"})
    ET.SubElement(root, "mxCell", {"id": "1", "parent": "0", "value": L("Plan", "Plan")})
    for layer in plan["layers"]:
        if not layer["used"]:
            continue
        attrs = {"id": _layer_id(layer["key"]), "parent": "0", "value": layer["label"]}
        if layer["key"] in hidden:
            attrs["visible"] = "0"
        ET.SubElement(root, "mxCell", attrs)
    head = ET.SubElement(
        root,
        "mxCell",
        {
            "id": "title",
            "value": f"<b>{_html(title)}</b><br>{_html(stamp(plan, when, site_text))}",
            "vertex": "1",
            "parent": "1",
            "style": "text;html=1;align=left;verticalAlign=top;fontSize=14;",
        },
    )
    _geometry(head, 16, 8, 900, 40)
    for i, g in enumerate(plan["groups"]):
        cell = ET.SubElement(
            root,
            "mxCell",
            {
                "id": f"site-{i}",
                "value": _html(g["title"]),
                "vertex": "1",
                "parent": "1",
                "style": "rounded=1;arcSize=2;dashed=1;fillColor=#f8fafc;"
                f"strokeColor={STROKE_GROUP};verticalAlign=top;align=left;spacingLeft=8;fontSize=12;fontStyle=1;html=1;",
            },
        )
        _geometry(cell, g["x"], g["y"] + HEAD, g["w"], g["h"])
    ids: dict[str, str] = {}
    for i, n in enumerate(plan["nodes"]):
        ids[n["id"]] = f"n{i}"
        badges = [b["text"] for b in n["badges"]]
        label = f"<b>{_html(n['label'])}</b><br>{_html(n['sub'])}"
        if badges:
            label += "<br><i>" + "<br>".join(_html(b) for b in badges[:3]) + "</i>"
        obj = ET.SubElement(
            root,
            "UserObject",
            {
                "id": ids[n["id"]],
                "label": label,
                "automato_kind": n["kind"],
                "automato_name": n["label"],
                "tooltip": "\n".join(n["info"]),
            },
        )
        style = (
            f"rounded=1;arcSize=12;whiteSpace=wrap;html=1;fontSize=11;"
            f"fillColor={FILL.get(n['kind'], '#ffffff')};strokeColor={FG if n['read'] else MUTED};"
        )
        if not n["read"] or n["foreign"]:
            style += "dashed=1;"
        if n["foreign"]:
            style += "opacity=55;"
        cell = ET.SubElement(
            obj, "mxCell", {"vertex": "1", "parent": _layer_id(n["layer"]), "style": style}
        )
        _geometry(cell, n["x"], n["y"] + HEAD, n["w"], n["h"])
    texts = 0
    for i, e in enumerate(plan["edges"]):
        if e["a"] not in ids or e["b"] not in ids:
            continue
        color = TONE.get(e["tone"], TONE["line"])
        style = f"endArrow=none;html=1;strokeColor={color};strokeWidth=1.6;fontSize=10;"
        if e["dash"]:
            style += "dashed=1;"
        obj = ET.SubElement(
            root,
            "UserObject",
            {
                "id": f"e{i}",
                "label": _html(e["label"]),
                "automato_kind": e["kind"],
                "tooltip": "\n".join(e["info"]),
            },
        )
        cell = ET.SubElement(
            obj,
            "mxCell",
            {
                "edge": "1",
                "parent": _layer_id(e["layer"]),
                "style": style,
                "source": ids[e["a"]],
                "target": ids[e["b"]],
            },
        )
        geo = ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
        # Knickpunkt: um ein Element herum bzw. die Mitte (parallele Linien eines Paars versetzt)
        first, *inner, last = e["points"]
        bends = inner or [((first[0] + last[0]) / 2, (first[1] + last[1]) / 2)]
        pts = ET.SubElement(geo, "Array", {"as": "points"})
        for x, y in bends:
            ET.SubElement(pts, "mxPoint", {"x": _n(x), "y": _n(y + HEAD)})
        # Texte (Ebene je Text) und Ports an den Enden als eigene Textfelder
        extra = [
            (t["text"], t.get("layer"), e["lx"], e["ly"] + 12 * (j + 1), "center")
            for j, t in enumerate(e["texts"])
        ]
        for p in (e["pa"], e["pb"]):
            if p:
                extra.append(
                    (
                        p["text"],
                        e["ends_layer"],
                        p["x"],
                        p["y"],
                        "left" if p["anchor"] == "start" else "right",
                    )
                )
        for text, layer, x, y, align in extra:
            texts += 1
            w = min(260, 7 * len(text) + 8)
            left = x if align == "left" else x - w if align == "right" else x - w / 2
            t = ET.SubElement(
                root,
                "mxCell",
                {
                    "id": f"t{texts}",
                    "value": _html(text),
                    "vertex": "1",
                    "parent": _layer_id(layer),
                    "style": f"text;html=1;fontSize=9;fontColor={color};align={align};"
                    "verticalAlign=middle;whiteSpace=nowrap;",
                },
            )
            _geometry(t, left, y - 7 + HEAD, w, 14)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(mxfile, encoding="unicode")
