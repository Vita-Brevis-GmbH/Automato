"""Netzwerkplan je Applikation (0.67.0, ADR 0021 Nr. 6): die Verbindungen Ende zu Ende als Plan.

- **Knoten:** links die Systeme der Applikation (VM, Server bzw. EPG), in der Mitte die Knoten
  ihrer Wege (Firewall, VDOM, ACI-VRF; Verbund als ein Knoten wie im Weg), rechts die
  Gegenstellen (Host, Netz, EPG, externe EPG). Je Knoten das Urteil ueber alle Verbindungen
  (z. B. gesperrt an der ACI-VRF).
- **Linien:** je Knotenpaar auf den Wegen eine Linie mit den Diensten in beide Richtungen
  („→ tcp/443 · ← tcp/22“; Richtung von der Applikation aus) und an den Enden den Interfaces
  der Knoten (Ein- bzw. Ausgang des Wegs). Farbe nach dem Urteil der Verbindungen darueber,
  gestrichelt, wo der Weg nicht bekannt ist (vor dem Ziel zu Ende, Quelle an keinem gelesenen
  Geraet).
- **Layout:** Spalten nach der Entfernung von der Applikation, Reihen nach den Nachbarn links
  (Baryzentrum), Linien weich wie im Zonenplan (`plan_export.route`).

Reine Funktion ueber dem Ergebnis von `app_paths.build`: Oberflaeche, SVG und draw.io zeichnen
dasselbe Layout; `keys` = nur diese Verbindungen (Filter der Ansicht beim Export). Nur lesend,
nicht fuer Kunden-Benutzer (zeigt die Infrastruktur, ADR 0018).
"""

import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime

from app.store.plan_export import CURVE_STEPS, _bezier, _geometry, _html, _id, _n, _rel, _text

NODE_W, NODE_H = 210, 46
COL_GAP, ROW_GAP, TOP = 150, 22, 34
MAX_SERVICES = 4  # Dienste je Richtung an einer Linie
MAX_PORTS = 2  # Interfaces je Ende
HEAD = 56

KIND = {"system": "System der Applikation", "firewall": "Firewall", "vdom": "VDOM",
        "vrf": "ACI-VRF", "object": "Gegenstelle", "epg": "EPG",
        "external": "externe EPG"}  # fmt: skip
VERDICT = {"allow": "erlaubt", "deny": "gesperrt", "depends": "abhängig", "unknown": "unbekannt",
           "blocked": "vorher gesperrt"}  # fmt: skip
BLOCK = ("deny", "implicit_deny")
FG, MUTED, LINE = "#171b22", "#5a6475", "#8a93a3"
COLOR = {"allow": "#2e7d32", "deny": "#c62828", "depends": "#ef6c00", "unknown": LINE,
         "blocked": "#b8bfca"}  # fmt: skip
FILL = {"own": "#e8f0fe", "hop": "#eef1f6", "peer": "#ffffff"}
STROKE = {"own": "#2563eb", "hop": "#0e7c86", "peer": LINE}


def _verdict(values) -> str:
    """Urteil ueber mehrere Verbindungen bzw. Hops: ein Urteil oder „abhaengig“."""
    v = {"deny" if x == "implicit_deny" else x for x in values if x}
    v = {x if x in VERDICT and x != "blocked" else "unknown" for x in v}
    if not v:
        return "unknown"
    return v.pop() if len(v) == 1 else "depends"


def _facing(h: dict, direction: str) -> tuple[list[str], list[str]]:
    """Interfaces eines Hops zur Applikation hin bzw. zur Gegenstelle hin (Verbund: alle)."""
    ing = [c["ingress"] for c in h.get("checks") or [] if c.get("ingress")]
    eg = [c["egress"] for c in h.get("checks") or [] if c.get("egress")]
    return (ing, eg) if direction == "out" else (eg, ing)


def _chain(c: dict) -> list[dict]:
    """Knoten einer Verbindung von der Applikation zur Gegenstelle: je Knoten Id, Art, Name und
    die Interfaces nach links (zur Applikation) bzw. rechts."""
    out = c["direction"] == "out"
    own, peer = (c["src"], c["dst"]) if out else (c["dst"], c["src"])
    chain = [{"id": f"sys:{own['label']}", "role": "own", "side": own, "left": [], "right": []}]
    for h in c["hops"] if out else list(reversed(c["hops"])):
        left, right = _facing(h, c["direction"])
        chain.append({"id": f"hop:{h['key']}", "role": "hop", "hop": h, "left": left,
                      "right": right})  # fmt: skip
    chain.append({"id": f"peer:{peer['ip']}", "role": "peer", "side": peer, "left": [],
                  "right": []})  # fmt: skip
    return chain


def _open_segments(c: dict, n: int) -> set[int]:
    """Abschnitte (Index des linken Knotens) ohne bekannten Weg: Ende vor dem Ziel, Quelle an
    keinem gelesenen Geraet (Weg ab dem Geraet der Regel), gar kein Weg."""
    if not c["hops"]:
        return {0}
    out: set[int] = set()
    to_dst, to_src = (n - 2, 0) if c["direction"] == "out" else (0, n - 2)
    if not c["reached"]:  # vor dem Ziel zu Ende
        out.add(to_dst)
    if c.get("start"):  # die Quelle haengt an keinem gelesenen Geraet
        out.add(to_src)
    return out


def _after_block(c: dict, chain: list[dict]) -> tuple[set[int], int | None]:
    """Abschnitte, die die Verbindung nicht mehr erreicht (hinter dem ersten sperrenden Hop in
    Richtung des Verkehrs), und der Index dieses Hops in der Kette."""
    n = len(chain)
    order = range(n) if c["direction"] == "out" else range(n - 1, -1, -1)
    for k in order:
        x = chain[k]
        if x["role"] == "hop" and x["hop"].get("verdict") in BLOCK:
            segs = range(k, n - 1) if c["direction"] == "out" else range(0, k)
            return set(segs), k
    return set(), None


def _svc_key(s: str) -> tuple:
    proto, _, port = s.partition("/")
    return (proto, int(port) if port.isdigit() else -1, s)


def _services(values: list[str]) -> str:
    vals = sorted(dict.fromkeys(values), key=_svc_key)
    more = f" +{len(vals) - MAX_SERVICES}" if len(vals) > MAX_SERVICES else ""
    return ", ".join(vals[:MAX_SERVICES]) + more


def _ports(values) -> str:
    vals = list(dict.fromkeys(values))
    return ", ".join(vals[:MAX_PORTS]) + (" …" if len(vals) > MAX_PORTS else "")


def edge_label(e: dict) -> str:
    """„→ tcp/443, tcp/8080 · ← tcp/22“ - Richtung von der Applikation aus (Linie a -> b)."""
    parts = []
    if e["fwd"]:
        parts.append("→ " + _services(e["fwd"]))
    if e["back"]:
        parts.append("← " + _services(e["back"]))
    if e["blocked"]:  # vorher gesperrt: erreicht diese Linie nicht
        parts.append("✕ " + _services(e["blocked"]))
    return " · ".join(parts)


def build(connections: list[dict], keys: set[str] | None = None) -> dict:
    """Plan (Knoten, Linien, Lage) aus den Verbindungen; `keys` = nur diese."""
    conns = [c for c in connections if keys is None or c["key"] in keys]
    nodes: dict[str, dict] = {}
    edges: dict[tuple[str, str], dict] = {}
    col: dict[str, int] = {}
    for c in conns:
        chain = _chain(c)
        opened = _open_segments(c, len(chain))
        behind, block = _after_block(c, chain)
        for i, x in enumerate(chain):
            node = nodes.get(x["id"])
            if node is None:
                node = nodes[x["id"]] = _node(x)
            # hinter dem sperrenden Hop kommt der Verkehr nicht an: kein Urteil dort
            reached = block is None or (i <= block if c["direction"] == "out" else i >= block)
            _add(node, x, c, reached)
            if x["role"] == "hop":
                col[x["id"]] = max(col.get(x["id"], 0), i)
        svc = c["service"] or "alle"
        for i in range(len(chain) - 1):
            a, b = chain[i], chain[i + 1]
            pair = (a["id"], b["id"])
            key = pair if pair in edges or pair[::-1] not in edges else pair[::-1]
            e = edges.get(key)
            if e is None:
                e = edges[key] = {"id": f"e{len(edges)}", "a": key[0], "b": key[1], "fwd": [],
                                  "back": [], "blocked": [], "verdicts": [], "conns": [],
                                  "ports_a": [], "ports_b": [], "open": True,
                                  "mismatch": False}  # fmt: skip
            flip = key != pair
            e["ports_a" if not flip else "ports_b"] += a["right"]
            e["ports_b" if not flip else "ports_a"] += b["left"]
            if c["key"] not in e["conns"]:
                e["conns"].append(c["key"])
            if i in behind:
                e["blocked"].append(svc)
                continue
            forward = (c["direction"] == "out") != flip
            e["fwd" if forward else "back"].append(svc)
            e["verdicts"].append(c["verdict"])
            if i not in opened:
                e["open"] = False
            e["mismatch"] = e["mismatch"] or bool(c["mismatch"])
    # Spalten: Applikation links, Gegenstellen rechts, die Hops nach ihrer Entfernung
    last = max(col.values(), default=0) + 1
    for nid, n in nodes.items():
        n["col"] = 0 if n["role"] == "own" else last if n["role"] == "peer" else col[nid]
    out_edges = [_edge_out(e) for e in edges.values()]
    lay = _layout(list(nodes.values()), out_edges)
    lay["summary"] = {"connections": len(conns), "total": len(connections),
                      "nodes": len(nodes), "edges": len(out_edges)}  # fmt: skip
    return lay


def _node(x: dict) -> dict:
    if x["role"] == "hop":
        h = x["hop"]
        return {"id": x["id"], "role": "hop", "kind": h["kind"], "label": h["label"],
                "hops": [], "conns": [], "ips": [], "systems": []}  # fmt: skip
    s = x["side"]
    kind = "system" if x["role"] == "own" else s["kind"]
    return {"id": x["id"], "role": x["role"], "kind": kind, "label": s["label"],
            "name": s.get("name"), "hops": [], "conns": [], "ips": [], "systems": []}  # fmt: skip


def _add(node: dict, x: dict, c: dict, reached: bool) -> None:
    if c["key"] not in node["conns"]:
        node["conns"].append(c["key"])
    if x["role"] == "hop":
        if reached:
            node["hops"].append(x["hop"].get("verdict"))
        return
    s = x["side"]
    if s["ip"] not in node["ips"]:
        node["ips"].append(s["ip"])
    for name in s.get("systems") or []:
        if name not in node["systems"]:
            node["systems"].append(name)


def _edge_out(e: dict) -> dict:
    """Linie: Urteil der Verbindungen, die sie erreichen; nur vorher Gesperrte = `blocked`."""
    verdict = _verdict(e["verdicts"]) if e["verdicts"] else "blocked"
    return {"id": e["id"], "a": e["a"], "b": e["b"],
            "fwd": sorted(dict.fromkeys(e["fwd"]), key=_svc_key),
            "back": sorted(dict.fromkeys(e["back"]), key=_svc_key),
            "blocked": sorted(dict.fromkeys(e["blocked"]), key=_svc_key),
            "verdict": verdict, "conns": e["conns"],
            "ports_a": list(dict.fromkeys(e["ports_a"])),
            "ports_b": list(dict.fromkeys(e["ports_b"])),
            "dashed": e["open"], "mismatch": e["mismatch"]}  # fmt: skip


def _sub(n: dict) -> str:
    if n["role"] == "hop":
        return f"{KIND.get(n['kind'], n['kind'])} · {VERDICT[n['verdict']]}"
    ips = n["ips"]
    text = ips[0] + (f" +{len(ips) - 1}" if len(ips) > 1 else "")
    if n["role"] == "own" and len(n["systems"]) > 1:
        text += f" · {len(n['systems'])} Systeme"
    elif n.get("name"):
        text += f" · {n['name']}"
    return text


def _layout(nodes: list[dict], edges: list[dict]) -> dict:
    """Spalten wie berechnet; eine Linie ueber mehrere Spalten bekommt je Spalte dazwischen eine
    Spur (Platzhalter wie im Sugiyama-Verfahren) und laeuft an den Knoten vorbei. Reihen nach dem
    Mittel der Nachbarn (links, rechts, wieder links), Linien weich mit waagrechten Enden."""
    for n in nodes:
        if n["role"] == "hop":  # ohne erreichende Verbindung: vorher gesperrt
            n["verdict"] = _verdict(n["hops"]) if n["hops"] else "blocked"
        else:
            n["verdict"] = None
    order = sorted({n["col"] for n in nodes})
    by_id = {n["id"]: n for n in nodes}
    items: dict[int, list[dict]] = defaultdict(list)  # je Spalte Knoten und Spuren
    for n in nodes:
        n["vc"] = order.index(n["col"])
        items[n["vc"]].append(n)
    lanes: dict[str, list[dict]] = {}
    left: dict[str, list[str]] = defaultdict(list)
    right: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        lo, hi = sorted((by_id[e["a"]], by_id[e["b"]]), key=lambda n: n["vc"])
        chain = [lo]
        for c in range(lo["vc"] + 1, hi["vc"]):
            lane = {"id": f"lane:{e['id']}:{c}", "role": "lane", "vc": c, "label": hi["label"]}
            items[c].append(lane)
            chain.append(lane)
        chain.append(hi)
        lanes[e["id"]] = chain[1:-1]
        for x, y in zip(chain, chain[1:], strict=False):
            if x["vc"] < y["vc"]:
                left[y["id"]].append(x["id"])
                right[x["id"]].append(y["id"])
    _order(items, left, right)
    tall, heights = 0.0, {}
    for c, col in items.items():
        heights[c] = sum(_h(x) for x in col) + sum(
            _gap(x, y) for x, y in zip(col, col[1:], strict=False)
        )
        tall = max(tall, heights[c])
    for c, col in items.items():
        y = TOP + (tall - heights[c]) / 2
        for i, x in enumerate(col):
            if i:
                y += _gap(col[i - 1], x)
            x["x"], x["y"], x["h"] = 16 + c * (NODE_W + COL_GAP), y, _h(x)
            y += x["h"]
    boxes = []
    for n in nodes:
        box = {k: v for k, v in n.items() if k not in ("hops", "col", "vc")}
        boxes.append(box | {"w": NODE_W, "title": n["label"], "sub": _sub(n)})
    routed = _route(by_id, [{**e, "label": edge_label(e)} for e in edges], lanes)
    width = 16 + len(order) * (NODE_W + COL_GAP) - COL_GAP + 16 if order else 400
    width = max([width] + [x + 60 for e in routed for x, _ in e["points"]])
    columns = []
    for ci, c in enumerate(order):  # Kopf je Spalte (der Weg nur ueber der ersten)
        title = ("Applikation" if c == 0 else "Gegenstellen" if ci == len(order) - 1
                 else "Weg" if ci == 1 else "")  # fmt: skip
        columns.append({"x": 16 + ci * (NODE_W + COL_GAP), "title": title})
    return {"width": width, "height": TOP + tall + 24, "nodes": boxes, "edges": routed,
            "columns": columns}  # fmt: skip


LANE_H, LANE_GAP = 12, 12


def _h(x: dict) -> float:
    return LANE_H if x["role"] == "lane" else NODE_H


def _gap(x: dict, y: dict) -> float:
    return LANE_GAP if "lane" in (x["role"], y["role"]) else ROW_GAP


def _order(items: dict[int, list[dict]], left: dict, right: dict) -> None:
    """Reihenfolge je Spalte: erste nach Namen, dann nach dem Mittel der Nachbarn links, rechts
    und wieder links (weniger Kreuzungen)."""
    rank: dict[str, float] = {}
    cols = sorted(items)

    def place(c: int, nbrs: dict | None) -> None:
        def key(x: dict) -> tuple:
            seen = [rank[i] for i in (nbrs or {}).get(x["id"], []) if i in rank]
            own = rank.get(x["id"], 0.0)
            return (sum(seen) / len(seen) if seen else own if nbrs is right else 1e9, own,
                    x["label"])  # fmt: skip

        if nbrs is None:
            items[c].sort(key=lambda x: x["label"])
        else:
            items[c].sort(key=key)
        for i, x in enumerate(items[c]):
            rank[x["id"]] = i

    for c in cols:
        place(c, left if c else None)
    for c in reversed(cols[:-1]):
        place(c, right)
    for c in cols[1:]:
        place(c, left)


def _route(by_id: dict, edges: list[dict], lanes: dict[str, list[dict]]) -> list[dict]:
    """Linien von rechts am linken Knoten durch ihre Spuren nach links am rechten Knoten; in
    derselben Spalte rechts herum. Anschluesse je Seite nach der Lage der Gegenseite."""

    def cy(x: dict) -> float:
        return x["y"] + x["h"] / 2

    ends: dict[tuple, list] = defaultdict(list)
    plans = []
    for e in edges:
        a, b = by_id[e["a"]], by_id[e["b"]]
        via = lanes[e["id"]]
        if a["vc"] == b["vc"]:
            plans.append((e, a, b, "r", "r"))
            ends[(a["id"], "r")].append((cy(b), e["id"]))
            ends[(b["id"], "r")].append((cy(a), e["id"]))
            continue
        lo, hi = (a, b) if a["vc"] < b["vc"] else (b, a)
        ends[(lo["id"], "r")].append((cy(via[0]) if via else cy(hi), e["id"]))
        ends[(hi["id"], "l")].append((cy(via[-1]) if via else cy(lo), e["id"]))
        plans.append((e, lo, hi, "r", "l"))
    order = {k: [eid for _, eid in sorted(v)] for k, v in ends.items()}

    def at(box: dict, side: str, eid: str) -> tuple[float, float]:
        ids = order[(box["id"], side)]
        y = box["y"] + 8 + (box["h"] - 16) * (ids.index(eid) + 1) / (len(ids) + 1)
        return (box["x"] + NODE_W if side == "r" else box["x"]), round(y, 1)

    out = []
    for n, (e, lo, hi, s1, s2) in enumerate(plans):
        p0, p3 = at(lo, s1, e["id"]), at(hi, s2, e["id"])
        if s1 == s2:  # dieselbe Spalte: rechts herum
            reach = 40 + (n % 4) * 10 + abs(p3[1] - p0[1]) * 0.12
            c1, c2 = (p0[0] + reach, p0[1]), (p3[0] + reach, p3[1])
            points = [_bezier(p0, c1, c2, p3, i / CURVE_STEPS) for i in range(CURVE_STEPS + 1)]
            mx, my = _bezier(p0, c1, c2, p3, 0.5)
            label = (mx + 6, my + 4, "start")
            via: list = []
        else:
            via = [(x["x"], cy(x)) for x in lanes[e["id"]]]
            stops = [p0] + [q for x, y in via for q in ((x, y), (x + NODE_W, y))] + [p3]
            points, label = [], None
            for i in range(len(stops) - 1):
                (x0, y0), (x1, y1) = stops[i], stops[i + 1]
                if i % 2:  # durch die Spalte der Spur: gerade
                    seg = [[x0, y0], [x1, y1]]
                else:  # zwischen den Spalten: Kurve mit waagrechten Enden
                    c1, c2 = (x0 + (x1 - x0) / 2, y0), (x1 - (x1 - x0) / 2, y1)
                    seg = [_bezier(stops[i], c1, c2, stops[i + 1], k / CURVE_STEPS)
                           for k in range(CURVE_STEPS + 1)]  # fmt: skip
                    mx, my = _bezier(stops[i], c1, c2, stops[i + 1], 0.5)
                    label = (mx, my - 5, "middle")  # an der letzten Kurve (zur Gegenstelle)
                points += seg if not points else seg[1:]
        flip = lo["id"] != e["a"]
        (x1, y1), (x2, y2) = (p3, p0) if flip else (p0, p3)
        sa, sb = (s2, s1) if flip else (s1, s2)
        lx, ly, anchor = label
        out.append({**e, "points": points, "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                    "lx": lx, "ly": ly, "anchor": anchor, "sides": [sa, sb],
                    "via": [[x + NODE_W / 2, y] for x, y in (via[::-1] if flip else via)],
                    "port_a": _port_label(x1, y1, sa, _ports(e["ports_a"])),
                    "port_b": _port_label(x2, y2, sb, _ports(e["ports_b"]))})  # fmt: skip
    return out


def _port_label(x: float, y: float, side: str, text: str) -> dict | None:
    """Interfaces am Ende einer Linie: neben dem Anschlusspunkt, ausserhalb des Knotens."""
    if not text:
        return None
    if side == "r":
        return {"x": x + 5, "y": y - 4, "anchor": "start", "text": text}
    if side == "l":
        return {"x": x - 5, "y": y - 4, "anchor": "end", "text": text}
    return {"x": x + 5, "y": y + 12 if side == "b" else y - 5, "anchor": "start", "text": text}


# -- Export -----------------------------------------------------------------------------


def _stamp(plan: dict, when: datetime | None) -> str:
    s = plan["summary"]
    parts = [f"Stand {when:%d.%m.%Y %H:%M}" if when else "Stand"]
    count = f"{s['connections']} Verbindungen"
    if s["connections"] != s["total"]:
        count += f" (von {s['total']}, gefiltert)"
    parts += [count, f"{s['nodes']} Knoten"]
    return " · ".join(parts)


def _style(n: dict) -> tuple[str, str]:
    role = n["role"]
    stroke = COLOR[n["verdict"]] if role == "hop" and n["verdict"] != "unknown" else STROKE[role]
    return FILL[role], stroke


def svg(plan: dict, title: str, when: datetime | None = None) -> str:
    width, height = max(plan["width"], 400), plan["height"] + HEAD
    root = ET.Element("svg", {
        "xmlns": "http://www.w3.org/2000/svg", "viewBox": f"0 0 {_n(width)} {_n(height)}",
        "width": _n(width), "height": _n(height), "font-family": "Helvetica, Arial, sans-serif",
    })  # fmt: skip
    ET.SubElement(root, "title").text = title
    ET.SubElement(root, "rect", {"width": _n(width), "height": _n(height), "fill": "#ffffff"})
    _text(root, 16, 26, title, "16", FG, "bold")
    _text(root, 16, 44, _stamp(plan, when), "11", MUTED)
    g = ET.SubElement(root, "g", {"transform": f"translate(0 {HEAD})"})
    for c in plan["columns"]:
        _text(g, c["x"], 18, c["title"], "11", MUTED, "bold")
    for e in plan["edges"]:
        attrs = {"points": " ".join(f"{_n(x)},{_n(y)}" for x, y in e["points"]), "fill": "none",
                 "stroke": COLOR[e["verdict"]], "stroke-width": "1.8"}  # fmt: skip
        if e["dashed"]:
            attrs["stroke-dasharray"] = "5 4"
        line = ET.SubElement(g, "polyline", attrs)
        ET.SubElement(line, "title").text = f"{e['label']} – {VERDICT[e['verdict']]}"
        _text(g, e["lx"], e["ly"], e["label"], "10.5", COLOR[e["verdict"]],
              anchor=e["anchor"], mono=True)  # fmt: skip
        for p in (e["port_a"], e["port_b"]):
            if p:
                _text(g, p["x"], p["y"], p["text"], "9.5", MUTED, anchor=p["anchor"], mono=True)
    for n in plan["nodes"]:
        fill, stroke = _style(n)
        attrs = {"x": _n(n["x"]), "y": _n(n["y"]), "width": _n(n["w"]), "height": _n(n["h"]),
                 "rx": "6", "fill": fill, "stroke": stroke,
                 "stroke-width": "2" if n["role"] == "hop" else "1.4"}  # fmt: skip
        if n["kind"] == "external":
            attrs["stroke-dasharray"] = "4 3"
        ET.SubElement(g, "rect", attrs)
        _text(g, n["x"] + 10, n["y"] + 18, _short(n["title"]), "12", FG, "bold")
        _text(g, n["x"] + 10, n["y"] + 34, _short(n["sub"], 36), "10", MUTED)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def _short(text: str, n: int = 30) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def drawio(plan: dict, title: str, when: datetime | None = None) -> str:
    """mxGraph fuer diagrams.net/draw.io: Knoten als Formen (`automato_*` mit IPs, Systemen,
    Urteil), Linien als Kanten mit den Diensten und den Interfaces an den Enden."""
    mxfile = ET.Element("mxfile", {"host": "Automato", "type": "device"})
    diagram = ET.SubElement(mxfile, "diagram", {"id": "applikation", "name": "Applikation"})
    model = ET.SubElement(diagram, "mxGraphModel", {
        "grid": "1", "gridSize": "10", "guides": "1", "tooltips": "1", "connect": "1",
        "arrows": "0", "fold": "1", "page": "1", "pageScale": "1",
        "pageWidth": _n(max(plan["width"], 400)), "pageHeight": _n(plan["height"] + HEAD),
        "math": "0", "shadow": "0",
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
    boxes = {n["id"]: n for n in plan["nodes"]}
    for n in plan["nodes"]:
        fill, stroke = _style(n)
        obj = ET.SubElement(root, "UserObject", {
            "id": f"n-{_id(n['id'])}",
            "label": f"<b>{_html(n['title'])}</b><br>{_html(n['sub'])}",
            "automato_kind": KIND.get(n["kind"], n["kind"]),
            "automato_ips": ", ".join(n["ips"]), "automato_systems": ", ".join(n["systems"]),
            "automato_verdict": VERDICT[n["verdict"]] if n["verdict"] else "",
            "tooltip": ", ".join(n["systems"] or n["ips"]),
        })  # fmt: skip
        style = (f"rounded=1;arcSize=12;whiteSpace=wrap;html=1;align=left;spacingLeft=8;"
                 f"fillColor={fill};strokeColor={stroke};fontSize=12;")  # fmt: skip
        if n["kind"] == "external":
            style += "dashed=1;"
        cell = ET.SubElement(obj, "mxCell", {"vertex": "1", "parent": "1", "style": style})
        _geometry(cell, n["x"], n["y"] + HEAD, n["w"], n["h"])
    for e in plan["edges"]:
        a, b = boxes[e["a"]], boxes[e["b"]]
        color = COLOR[e["verdict"]]
        style = (
            f"endArrow=none;html=1;strokeColor={color};fontColor={color};fontSize=10;"
            "fontFamily=Courier New;edgeStyle=orthogonalEdgeStyle;curved=1;rounded=1;"
            f"exitX={_rel(e['x1'], a['x'], a['w'])};exitY={_rel(e['y1'], a['y'], a['h'])};"
            f"entryX={_rel(e['x2'], b['x'], b['w'])};entryY={_rel(e['y2'], b['y'], b['h'])};"
            "exitDx=0;exitDy=0;entryDx=0;entryDy=0;"
        )
        if e["dashed"]:
            style += "dashed=1;"
        eid = f"l-{_id(e['id'])}"
        obj = ET.SubElement(root, "UserObject", {
            "id": eid, "label": _html(e["label"]),
            "automato_services_out": ", ".join(e["fwd"]),
            "automato_services_in": ", ".join(e["back"]),
            "automato_ports_a": ", ".join(e["ports_a"]),
            "automato_ports_b": ", ".join(e["ports_b"]),
            "automato_verdict": VERDICT[e["verdict"]],
            "automato_connections": str(len(e["conns"])),
        })  # fmt: skip
        cell = ET.SubElement(obj, "mxCell", {
            "edge": "1", "parent": "1", "style": style,
            "source": f"n-{_id(e['a'])}", "target": f"n-{_id(e['b'])}",
        })  # fmt: skip
        geo = ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
        if e["via"]:  # durch die Spuren an den Knoten vorbei
            arr = ET.SubElement(geo, "Array", {"as": "points"})
            for x, y in e["via"]:
                ET.SubElement(arr, "mxPoint", {"x": _n(x), "y": _n(y + HEAD)})
        # Interfaces als Beschriftung an den Enden der Kante
        for end, pos, ports in (("a", "-0.8", e["ports_a"]), ("b", "0.8", e["ports_b"])):
            if not ports:
                continue
            label = ET.SubElement(root, "mxCell", {
                "id": f"{eid}-{end}", "value": _html(_ports(ports)), "vertex": "1",
                "connectable": "0", "parent": eid,
                "style": f"edgeLabel;html=1;align=center;verticalAlign=bottom;fontSize=9;"
                         f"fontColor={MUTED};fontFamily=Courier New;labelBackgroundColor=#ffffff;",
            })  # fmt: skip
            at = ET.SubElement(label, "mxGeometry", {"x": pos, "relative": "1",
                                                     "as": "geometry"})  # fmt: skip
            ET.SubElement(at, "mxPoint", {"y": "-8", "as": "offset"})
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(mxfile, encoding="unicode")
