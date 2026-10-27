"""Netzwerkplan je Applikation (0.67.0, ADR 0021 Nr. 6): die Verbindungen Ende zu Ende als Plan.

- **Knoten:** links die Systeme der Applikation, in der Mitte die Knoten ihrer Wege (Firewall,
  VDOM, ACI; Verbund als ein Knoten wie im Weg), rechts die Gegenstellen (Host, Netz, EPG,
  externe EPG). Je Knoten das Urteil ueber alle Verbindungen (z. B. gesperrt im ACI).
- **Linien:** je Knotenpaar auf den Wegen eine Linie mit den Diensten in beide Richtungen
  („→ tcp/443 · ← tcp/22“; Richtung von der Applikation aus) und an den Enden den Interfaces
  der Knoten (Ein- bzw. Ausgang des Wegs). Farbe nach dem Urteil der Verbindungen darueber,
  gestrichelt, wo der Weg nicht bekannt ist (vor dem Ziel zu Ende, Quelle an keinem gelesenen
  Geraet).
- **Kompakt (0.74.0, Anwender: zu gross, zu viele Linien):** die Applikation als Rahmen je
  Application Profile (Tenant/AP) mit einem Kasten je EPG, darin ihre VMs bzw. Server - die
  Linien beginnen an der EPG statt an jeder VM; Systeme ohne EPG im Rahmen „ohne EPG“. Das ACI
  steht als der genutzte **L3Out** da (Routing bzw. externe EPG, `app_paths.L3Outs`), sonst als
  VRF. Unter jedem Knoten des Wegs ein kleiner Kasten mit den entscheidenden Policies bzw.
  Contracts und ihren Diensten - eine eigene **Ebene „Regeln“** (Oberflaeche ausblendbar,
  draw.io eigene Ebene, SVG/draw.io mit `rules=False` ohne).
- **Load Balancer (0.85.0, Anwender):** nach dem Hop, an dem die VIP eines Virtual Service
  liegt, ein eigener Knoten - benannt nach der EPG der VIP (bzw. dem Load Balancer, wenn sie an
  einer Firewall liegt), mit den Virtual Services als Zeilen - und von dort die Linie zu den
  EPGs der Server. Geht der Weg danach am selben Knoten weiter (VRF bzw. Firewall), steht dieser
  nicht ein zweites Mal da; seine Regeln (z. B. Contract VIP-EPG → Server-EPG) stehen im Kasten
  des Load Balancers mit dem Gerät davor. VIPs der Firewall (DNAT) als Zeilen am Knoten.
- **Layout:** Spalten nach der Entfernung von der Applikation, Reihen nach den Nachbarn links
  (Baryzentrum), die Applikation nach AP und EPG; Linien weich wie im Zonenplan.

Reine Funktion ueber dem Ergebnis von `app_paths.build` (`connections`, `members`): Oberflaeche,
SVG und draw.io zeichnen dasselbe Layout; `keys` = nur diese Verbindungen (Filter der Ansicht
beim Export). Nur lesend, nicht fuer Kunden-Benutzer (zeigt die Infrastruktur, ADR 0018).
"""

import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime

from app.i18n import L
from app.store.plan_export import CURVE_STEPS, _bezier, _geometry, _html, _id, _n, _rel, _text

NODE_W, NODE_H = 190, 44
COL_GAP, ROW_GAP, TOP = 120, 18, 34
MAX_SERVICES = 4  # Dienste je Richtung an einer Linie
MAX_PORTS = 2  # Interfaces je Ende
HEAD = 56
LINE_H = 14  # Zeile: VM in der EPG, Regel im Kasten
MAX_LINES = 6  # VMs je EPG
MAX_RULES = 5  # Regeln je Kasten
RULE_GAP, RULE_PAD = 6, 5
GROUP_HEAD, GROUP_PAD, GROUP_GAP = 20, 8, 14
X0 = 16 + GROUP_PAD
NO_EPG = "ohne EPG"

KIND = {"system": "System der Applikation", "firewall": "Firewall", "vdom": "VDOM",
        "vrf": "ACI-VRF", "l3out": "L3Out", "object": "Gegenstelle", "epg": "EPG",
        "external": "externe EPG", "lb": "Load Balancer", "l3": "Router"}  # fmt: skip
VERDICT = {"allow": "erlaubt", "deny": "gesperrt", "depends": "abhängig", "unknown": "unbekannt",
           "blocked": "vorher gesperrt"}  # fmt: skip
# Anzeige auf Englisch (0.87.0, ADR 0028); die Schluessel bleiben
_KIND_EN = {"system": "Application system", "vrf": "ACI VRF", "object": "Peer",
            "external": "external EPG"}  # fmt: skip
_VERDICT_EN = {"allow": "allowed", "deny": "blocked", "depends": "depends", "unknown": "unknown",
               "blocked": "blocked earlier"}  # fmt: skip
IMPLICIT = "implizit gesperrt"  # Anzeige ohne entscheidende Regel (Merkmal `implicit`)
BLOCK = ("deny", "implicit_deny")
FG, MUTED, LINE = "#171b22", "#5a6475", "#8a93a3"
COLOR = {"allow": "#2e7d32", "deny": "#c62828", "depends": "#ef6c00", "unknown": LINE,
         "blocked": "#b8bfca"}  # fmt: skip
FILL = {"own": "#e8f0fe", "hop": "#eef1f6", "peer": "#ffffff"}
STROKE = {"own": "#2563eb", "hop": "#0e7c86", "peer": LINE}
GROUP_FILL, GROUP_STROKE = "#f7f9fc", "#9aa5b8"
RULE_FILL, RULE_STROKE = "#fffbea", "#d6b656"


def kind_text(kind: str) -> str:
    """Art eines Knotens in der Sprache der Anfrage."""
    de = KIND.get(kind, kind)
    return L(de, _KIND_EN.get(kind, de))


def verdict_text(verdict: str) -> str:
    """Urteil in der Sprache der Anfrage."""
    return L(VERDICT[verdict], _VERDICT_EN[verdict])


def _implicit(r: dict) -> bool:
    """Ohne entscheidende Regel gesperrt (`_add_rules`, 0.87.0: Merkmal statt Name)."""
    return bool(r.get("implicit"))


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


class _Place:
    """Wo ein System der Applikation steht (0.74.0): in seiner EPG (Kasten im Rahmen des APs)
    bzw. ohne EPG als eigener Kasten. Ohne `members` (reine Tests) je System ein Kasten."""

    def __init__(self, members: dict | None):
        m = members or {}
        self.epgs = {e["label"]: e for e in m.get("epgs") or []}
        self.of_key = {e["key"]: e["label"] for e in m.get("epgs") or []}
        self.vms: dict[str, list[str]] = defaultdict(list)
        for x in m.get("systems") or []:
            labels = [label for label in x.get("epgs") or [] if label in self.epgs]
            if labels:
                self.of_key.setdefault(x["key"], labels[0])
            for label in labels:
                if x["name"] not in self.vms[label]:
                    self.vms[label].append(x["name"])

    def own(self, side: dict) -> tuple[str, str | None]:
        for key in side.get("keys") or []:
            label = self.of_key.get(key)
            if label:
                return f"epg:{label}", label
        return f"sys:{side['label']}", None


def _l3outs(h: dict, direction: str) -> list[str]:
    """L3Out zur Gegenstelle hin: nach dem Routing der Ausgang (ausgehend) bzw. der Eingang
    (eingehend), sonst die L3Outs der externen EPGs der Pruefung."""
    side = "l3out_out" if direction == "out" else "l3out_in"
    checks = h.get("checks") or []
    names = [c[side] for c in checks if c.get(side)]
    return list(dict.fromkeys(names or [n for c in checks for n in c.get("l3outs") or []]))


def _chain(c: dict, place: _Place | None = None) -> list[dict]:
    """Knoten einer Verbindung von der Applikation zur Gegenstelle: je Knoten Id, Art, Name und
    die Interfaces nach links (zur Applikation) bzw. rechts. Das ACI als L3Out (0.74.0): ueber
    das Routing bzw. die Pruefung, sonst - zur externen EPG als Gegenstelle - deren L3Out."""
    place = place or _Place(None)
    out = c["direction"] == "out"
    own, peer = (c["src"], c["dst"]) if out else (c["dst"], c["src"])
    oid, epg = place.own(own)
    chain = [{"id": oid, "role": "own", "side": own, "epg": epg, "left": [], "right": []}]
    steps = _steps(c["hops"])  # in Verkehrsrichtung, mit den Load Balancern (0.85.0)
    steps = steps if out else list(reversed(steps))
    last = max((i for i, s in enumerate(steps) if "lb" not in s), default=-1)
    for i, s in enumerate(steps):
        if "lb" in s:
            chain.append(_lb_step(s))
            continue
        h = s["hop"]
        left, right = _facing(h, c["direction"])
        x = {"id": f"hop:{h['key']}", "role": "hop", "hop": h, "left": left, "right": right}
        if h["kind"] == "vrf":
            names = _l3outs(h, c["direction"])
            if not names and i == last and peer.get("l3out"):
                names = [peer["l3out"]]
            if names:
                x["l3out"] = " / ".join(names)
                x["id"] = f"l3o:{h['key']}|{x['l3out']}"
        chain.append(x)
    chain.append({"id": f"peer:{peer['ip']}", "role": "peer", "side": peer, "left": [],
                  "right": []})  # fmt: skip
    return chain


def _steps(hops: list[dict]) -> list[dict]:
    """Hops in Verkehrsrichtung; nach einem Hop mit Load Balancer dessen Knoten. Geht der Weg
    danach am selben Knoten weiter, gehoert dieser Hop zum Load Balancer (seine Regeln)."""
    out: list[dict] = []
    i = 0
    while i < len(hops):
        h = hops[i]
        out.append({"hop": h})
        if h.get("lb"):
            nxt = hops[i + 1] if i + 1 < len(hops) else None
            same = nxt is not None and nxt["key"] == h["key"]
            out.append({"lb": h["lb"], "at": h, "then": nxt if same else None})
            i += 1 if same else 0
        i += 1
    return out


def _lb_step(s: dict) -> dict:
    """Knoten des Load Balancers: je EPG der VIP (bzw. VIP an der Firewall) einer."""
    lb = s["lb"]
    where = lb.get("epg") or lb.get("ext") or lb["name"]
    then = s["then"] or {"verdict": None, "checks": [], "label": s["at"]["label"]}
    return {"id": f"lb:{where}", "role": "hop", "kind": "lb", "lb": lb, "hop": then,
            "at": s["at"], "left": [], "right": []}  # fmt: skip


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


def build(connections: list[dict], keys: set[str] | None = None,
          members: dict | None = None) -> dict:  # fmt: skip
    """Plan (Knoten, Linien, Lage) aus den Verbindungen; `keys` = nur diese; `members` = Aufbau
    der Applikation (`app_paths.members`: EPGs mit Tenant/AP, Systeme mit ihren EPGs)."""
    conns = [c for c in connections if keys is None or c["key"] in keys]
    place = _Place(members)
    nodes: dict[str, dict] = {}
    edges: dict[tuple[str, str], dict] = {}
    col: dict[str, int] = {}
    for c in conns:
        chain = _chain(c, place)
        opened = _open_segments(c, len(chain))
        behind, block = _after_block(c, chain)
        for i, x in enumerate(chain):
            node = nodes.get(x["id"])
            if node is None:
                node = nodes[x["id"]] = _node(x, place)
            # hinter dem sperrenden Hop kommt der Verkehr nicht an: kein Urteil dort
            reached = block is None or (i <= block if c["direction"] == "out" else i >= block)
            _add(node, x, c, reached)
            if x["role"] == "hop":
                col[x["id"]] = max(col.get(x["id"], 0), i)
        svc = c["service"] or L("alle", "all")
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


def _node(x: dict, place: _Place) -> dict:
    base = {"id": x["id"], "role": x["role"], "hops": [], "conns": [], "ips": [], "systems": [],
            "lines": [], "more": 0, "rules": [], "group": None}  # fmt: skip
    if x["role"] == "hop":
        h = x["hop"]
        if x.get("kind") == "lb":
            lb = x["lb"]
            label = lb.get("epg") or lb["name"]
            return base | {"kind": "lb", "label": label, "short": label.split("/")[-1],
                           "vips": [], "where": lb.get("interface"),
                           "via": x["at"]["label"]}  # fmt: skip
        if x.get("l3out"):
            return base | {"kind": "l3out", "label": x["l3out"], "via": h["label"]}
        return base | {"kind": h["kind"], "label": h["label"]}
    s = x["side"]
    if x.get("epg"):  # EPG mit ihren VMs bzw. Servern im Rahmen ihres APs
        e = place.epgs[x["epg"]]
        vms = place.vms.get(x["epg"], [])
        group = "/".join(v for v in (e.get("tenant"), e.get("ap")) if v) or "ACI"
        return base | {"kind": "epg", "label": x["epg"], "short": e.get("name") or x["epg"],
                       "vrf": e.get("vrf"), "group": group, "lines": vms[:MAX_LINES],
                       "more": max(0, len(vms) - MAX_LINES), "name": None}  # fmt: skip
    kind = "system" if x["role"] == "own" else s["kind"]
    group = NO_EPG if x["role"] == "own" and place.epgs else None
    return base | {"kind": kind, "label": s["label"], "name": s.get("name"), "group": group}


def _rule_text(r: dict) -> str:
    """Entscheidende Policy bzw. Contract: „Contract CT_web“, „web-lan (#11)“."""
    if r.get("kind") == "contract":
        return r.get("label") or f"Contract {r.get('name')}"
    name, ext = r.get("name"), r.get("external_id")
    if name and ext:
        return f"{name} (#{ext})"
    return r.get("label") or name or f"Policy {ext}"


def _add_balancer(node: dict, lb: dict) -> None:
    """Virtual Service als Zeile im Knoten des Load Balancers: „vs-web :443 · Pool pool-web“."""
    port = f":{lb['port']}" if lb.get("port") else ""
    pool = f" · Pool {lb['pool']}" if lb.get("pool") else ""
    line = f"{lb['name']} {lb.get('ext') or ''}{port}{pool}".replace("  ", " ")
    if line not in node["lines"]:
        node["lines"].append(line)
    vip = f"{lb.get('ext')}{port}" if lb.get("ext") else ""
    if vip and vip not in node["vips"]:
        node["vips"].append(vip)


def _add_nat(node: dict, h: dict) -> None:
    """VIP der Firewall (DNAT) als Zeile am Knoten (0.85.0): „VIP V-EXT_shop → 10.30.0.50“."""
    for k in h.get("checks") or []:
        nat = k.get("nat") or {}
        if not nat.get("name"):
            continue
        to = ", ".join(nat.get("to") or [])
        line = f"VIP {nat['name']}" + (f" → {to}" if to else "")
        if line not in node["lines"]:
            node["lines"].append(line)


def _add_rules(node: dict, h: dict, on_device: str | None = None) -> None:
    """Regeln des Kastens (0.74.0): je Hop die entscheidenden Policies bzw. Contracts mit ihren
    Diensten; ohne entscheidende Regel und gesperrt „implizit gesperrt“. Im Verbund mit der
    Firewall, am Load Balancer mit dem Gerät, auf dem die Regel steht (0.85.0)."""
    checks = h.get("checks") or []
    for k in checks:
        on = k["label"] if len(checks) > 1 or on_device else None
        deciding = [r for r in k.get("rules") or [] if r.get("decides")]
        if not deciding and k.get("verdict") in BLOCK:
            deciding = [{"name": L(IMPLICIT, "implicitly denied"), "implicit": True,
                         "action": "deny", "services": []}]  # fmt: skip
        for r in deciding:
            implicit = _implicit(r)
            text = r["name"] if implicit else _rule_text(r)
            entry = next((x for x in node["rules"] if (x["name"], x["on"]) == (text, on)), None)
            if entry is None:
                entry = {"name": text, "on": on, "action": r.get("action") or "allow",
                         "services": [], "rule_id": r.get("rule_id")}  # fmt: skip
                if implicit:
                    entry["implicit"] = True
                node["rules"].append(entry)
            for svc in r.get("services") or []:
                if svc not in entry["services"]:
                    entry["services"].append(svc)


def _add(node: dict, x: dict, c: dict, reached: bool) -> None:
    if c["key"] not in node["conns"]:
        node["conns"].append(c["key"])
    if x["role"] == "hop":
        if x.get("kind") == "lb":
            _add_balancer(node, x["lb"])
        else:
            _add_nat(node, x["hop"])
        if reached:
            node["hops"].append(x["hop"].get("verdict"))
            _add_rules(node, x["hop"], x["hop"].get("label") if x.get("kind") == "lb" else None)
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
    if n["kind"] == "lb":
        vips = n.get("vips") or []
        text = "Load Balancer" + (f" · VIP {vips[0]}" if vips else "")
        return text + (f" +{len(vips) - 1}" if len(vips) > 1 else "")
    if n["kind"] == "l3out":
        return f"L3Out · {verdict_text(n['verdict'])}"
    if n["role"] == "hop":
        return f"{kind_text(n['kind'])} · {verdict_text(n['verdict'])}"
    ips = n["ips"]
    text = ips[0] + (f" +{len(ips) - 1}" if len(ips) > 1 else "")
    if n["kind"] == "epg":
        return " · ".join(v for v in (f"VRF {n['vrf']}" if n.get("vrf") else "", text) if v)
    if n["role"] == "own" and len(n["systems"]) > 1:
        text += L(f" · {len(n['systems'])} Systeme", f" · {len(n['systems'])} systems")
    elif n.get("name"):
        text += f" · {n['name']}"
    return text


def _rule_lines(n: dict) -> list[str]:
    lines = []
    shown = sorted(n["rules"], key=_implicit)  # erlaubende zuerst
    for r in shown[:MAX_RULES]:
        svc = _services(r["services"]) if r["services"] else ""
        on = f"{r['on']}: " if r["on"] else ""
        mark = "✕ " if r["action"] in BLOCK else ""
        lines.append(f"{mark}{on}{r['name']}" + (f" · {svc}" if svc else ""))
    if len(n["rules"]) > MAX_RULES:
        lines.append(
            L(f"+{len(n['rules']) - MAX_RULES} weitere", f"+{len(n['rules']) - MAX_RULES} more")
        )
    return lines


def _sizes(n: dict) -> None:
    """Hoehe des Kastens (EPG mit ihren VMs) und Platz in der Spalte (mit dem Regel-Kasten)."""
    lines = len(n["lines"]) + (1 if n["more"] else 0)
    n["h"] = NODE_H + LINE_H * lines + (4 if lines else 0)
    n["rule_lines"] = _rule_lines(n) if n["role"] == "hop" else []
    rule_h = 2 * RULE_PAD + LINE_H * len(n["rule_lines"]) if n["rule_lines"] else 0
    n["rule_h"] = rule_h
    n["stack"] = n["h"] + (RULE_GAP + rule_h if rule_h else 0)


def _layout(nodes: list[dict], edges: list[dict]) -> dict:
    """Spalten wie berechnet; eine Linie ueber mehrere Spalten bekommt je Spalte dazwischen eine
    Spur (Platzhalter wie im Sugiyama-Verfahren) und laeuft an den Knoten vorbei. Reihen nach dem
    Mittel der Nachbarn (links, rechts, wieder links), die Applikation nach AP und EPG mit einem
    Rahmen je AP; Linien weich mit waagrechten Enden."""
    for n in nodes:
        if n["role"] == "hop":  # ohne erreichende Verbindung: vorher gesperrt
            n["verdict"] = _verdict(n["hops"]) if n["hops"] else "blocked"
            if n["kind"] == "lb" and set(n["hops"]) <= {None}:
                n["verdict"] = "unknown" if n["hops"] else "blocked"  # ohne Regel danach
        else:
            n["verdict"] = None
        _sizes(n)
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
            lane = {"id": f"lane:{e['id']}:{c}", "role": "lane", "vc": c, "label": hi["label"],
                    "h": LANE_H, "stack": LANE_H, "group": None}  # fmt: skip
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
        heights[c] = (
            _top(col)
            + sum(x["stack"] for x in col)
            + sum(_gap(x, y) for x, y in zip(col, col[1:], strict=False))
        )
        tall = max(tall, heights[c])
    for c, col in items.items():
        y = TOP + (tall - heights[c]) / 2 + _top(col)
        for i, x in enumerate(col):
            if i:
                y += _gap(col[i - 1], x)
            x["x"], x["y"] = X0 + c * (NODE_W + COL_GAP), y
            y += x["stack"]
    boxes = []
    for n in nodes:
        box = {k: v for k, v in n.items() if k not in ("hops", "col", "vc", "stack", "rule_h")}
        title = n.get("short") or n["label"]
        box |= {"w": NODE_W, "title": title, "sub": _sub(n)}
        box["rules_box"] = ({"x": n["x"], "y": n["y"] + n["h"] + RULE_GAP, "w": NODE_W,
                             "h": n["rule_h"]} if n["rule_h"] else None)  # fmt: skip
        boxes.append(box)
    routed = _route(by_id, [{**e, "label": edge_label(e)} for e in edges], lanes)
    width = X0 + len(order) * (NODE_W + COL_GAP) - COL_GAP + 16 if order else 400
    width = max([width] + [x + 60 for e in routed for x, _ in e["points"]])
    columns = []
    for ci, c in enumerate(order):  # Kopf je Spalte (der Weg nur ueber der ersten)
        title = (L("Applikation", "Application") if c == 0
                 else L("Gegenstellen", "Peers") if ci == len(order) - 1
                 else L("Weg", "Path") if ci == 1 else "")  # fmt: skip
        columns.append({"x": X0 + ci * (NODE_W + COL_GAP), "title": title})
    return {"width": width, "height": TOP + tall + 24, "nodes": boxes, "edges": routed,
            "columns": columns, "groups": _groups(nodes)}  # fmt: skip


def _groups(nodes: list[dict]) -> list[dict]:
    """Rahmen je AP (bzw. „ohne EPG“) um die Kaesten der Applikation."""
    by: dict[str, list[dict]] = defaultdict(list)
    for n in nodes:
        if n["role"] == "own" and n.get("group"):
            by[n["group"]].append(n)
    out = []
    for title in sorted(by, key=_group_rank):
        members = by[title]
        top = min(n["y"] for n in members)
        bottom = max(n["y"] + n["stack"] for n in members)
        shown = L(NO_EPG, "without EPG") if title == NO_EPG else title  # NO_EPG bleibt Schluessel
        out.append({"title": shown, "x": members[0]["x"] - GROUP_PAD,
                    "y": top - GROUP_HEAD - GROUP_PAD, "w": NODE_W + 2 * GROUP_PAD,
                    "h": bottom - top + GROUP_HEAD + 2 * GROUP_PAD,
                    "nodes": [n["id"] for n in members]})  # fmt: skip
    return out


def _group_rank(title: str | None) -> tuple:
    return (title is None, title == NO_EPG, title or "")


LANE_H, LANE_GAP = 12, 12


def _top(col: list[dict]) -> float:
    """Platz ueber dem ersten Kasten fuer den Kopf seines Rahmens."""
    return GROUP_HEAD + GROUP_PAD if col and col[0].get("group") else 0


def _gap(x: dict, y: dict) -> float:
    if "lane" in (x["role"], y["role"]):
        return LANE_GAP
    if x.get("group") != y.get("group") and (x.get("group") or y.get("group")):
        return GROUP_GAP + 2 * GROUP_PAD + (GROUP_HEAD if y.get("group") else 0)
    return ROW_GAP


def _order(items: dict[int, list[dict]], left: dict, right: dict) -> None:
    """Reihenfolge je Spalte: die Applikation nach AP und EPG, die uebrigen nach dem Mittel der
    Nachbarn links, rechts und wieder links (weniger Kreuzungen)."""
    rank: dict[str, float] = {}
    cols = sorted(items)

    def place(c: int, nbrs: dict | None) -> None:
        def key(x: dict) -> tuple:
            seen = [rank[i] for i in (nbrs or {}).get(x["id"], []) if i in rank]
            own = rank.get(x["id"], 0.0)
            return (sum(seen) / len(seen) if seen else own if nbrs is right else 1e9, own,
                    x["label"])  # fmt: skip

        if nbrs is None:
            items[c].sort(key=lambda x: (_group_rank(x.get("group")), x["label"]))
        else:
            items[c].sort(key=key)
        for i, x in enumerate(items[c]):
            rank[x["id"]] = i

    for c in cols:
        place(c, left if c else None)
    for c in reversed(cols[:-1]):
        if c:  # die Applikation bleibt nach AP und EPG geordnet
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
    if when:
        parts = [L(f"Stand {when:%d.%m.%Y %H:%M}", f"As of {when:%d/%m/%Y %H:%M}")]
    else:
        parts = [L("Stand", "As of")]
    count = L(f"{s['connections']} Verbindungen", f"{s['connections']} connections")
    if s["connections"] != s["total"]:
        count += L(f" (von {s['total']}, gefiltert)", f" (of {s['total']}, filtered)")
    parts += [count, L(f"{s['nodes']} Knoten", f"{s['nodes']} nodes")]
    return " · ".join(parts)


def _style(n: dict) -> tuple[str, str]:
    role = n["role"]
    stroke = COLOR[n["verdict"]] if role == "hop" and n["verdict"] != "unknown" else STROKE[role]
    return FILL[role], stroke


def svg(plan: dict, title: str, when: datetime | None = None, rules: bool = True) -> str:
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
    for f in plan.get("groups") or []:  # Rahmen je AP
        ET.SubElement(g, "rect", {"x": _n(f["x"]), "y": _n(f["y"]), "width": _n(f["w"]),
                                  "height": _n(f["h"]), "rx": "8", "fill": GROUP_FILL,
                                  "stroke": GROUP_STROKE, "stroke-dasharray": "5 3"})  # fmt: skip
        _text(g, f["x"] + 8, f["y"] + 14, _short(f["title"], 32), "10.5", MUTED, "bold")
    for e in plan["edges"]:
        attrs = {"points": " ".join(f"{_n(x)},{_n(y)}" for x, y in e["points"]), "fill": "none",
                 "stroke": COLOR[e["verdict"]], "stroke-width": "1.8"}  # fmt: skip
        if e["dashed"]:
            attrs["stroke-dasharray"] = "5 4"
        line = ET.SubElement(g, "polyline", attrs)
        ET.SubElement(line, "title").text = f"{e['label']} – {verdict_text(e['verdict'])}"
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
        for i, line in enumerate(_vm_lines(n)):
            _text(g, n["x"] + 14, n["y"] + NODE_H + 8 + i * LINE_H, _short(line, 32), "10.5", FG)
    if rules:
        layer = ET.SubElement(g, "g", {"id": "regeln"})
        for n in plan["nodes"]:
            b = n.get("rules_box")
            if not b:
                continue
            ET.SubElement(layer, "rect", {"x": _n(b["x"]), "y": _n(b["y"]), "width": _n(b["w"]),
                                          "height": _n(b["h"]), "rx": "3", "fill": RULE_FILL,
                                          "stroke": RULE_STROKE})  # fmt: skip
            for i, line in enumerate(n["rule_lines"]):
                _text(layer, b["x"] + 6, b["y"] + RULE_PAD + 10 + i * LINE_H, _short(line, 34),
                      "9.5", FG, mono=True)  # fmt: skip
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def _vm_lines(n: dict) -> list[str]:
    return [f"• {v}" for v in n.get("lines") or []] + (
        [L(f"+{n['more']} weitere", f"+{n['more']} more")] if n.get("more") else []
    )


def _short(text: str, n: int = 30) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def drawio(plan: dict, title: str, when: datetime | None = None, rules: bool = True) -> str:
    """mxGraph fuer diagrams.net/draw.io: Rahmen je AP, Knoten als Formen (`automato_*` mit IPs,
    Systemen, Urteil), Linien als Kanten mit den Diensten und den Interfaces an den Enden; die
    Regel-Kaesten auf der eigenen Ebene „Regeln“ (0.74.0)."""
    mxfile = ET.Element("mxfile", {"host": "Automato", "type": "device"})
    diagram = ET.SubElement(
        mxfile, "diagram", {"id": "applikation", "name": L("Applikation", "Application")}
    )
    model = ET.SubElement(diagram, "mxGraphModel", {
        "grid": "1", "gridSize": "10", "guides": "1", "tooltips": "1", "connect": "1",
        "arrows": "0", "fold": "1", "page": "1", "pageScale": "1",
        "pageWidth": _n(max(plan["width"], 400)), "pageHeight": _n(plan["height"] + HEAD),
        "math": "0", "shadow": "0",
    })  # fmt: skip
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", {"id": "0"})
    ET.SubElement(root, "mxCell", {"id": "1", "value": "Plan", "parent": "0"})
    head = ET.SubElement(root, "mxCell", {
        "id": "title", "value": f"<b>{_html(title)}</b><br>{_html(_stamp(plan, when))}",
        "vertex": "1", "parent": "1",
        "style": "text;html=1;align=left;verticalAlign=top;fontSize=14;",
    })  # fmt: skip
    _geometry(head, 16, 8, 600, 40)
    for i, f in enumerate(plan.get("groups") or []):  # Rahmen je AP, hinter den Kaesten
        frame = ET.SubElement(root, "mxCell", {
            "id": f"g-{i}", "value": _html(f["title"]), "vertex": "1", "parent": "1",
            "style": f"rounded=1;arcSize=4;whiteSpace=wrap;html=1;dashed=1;fillColor={GROUP_FILL};"
                     f"strokeColor={GROUP_STROKE};verticalAlign=top;align=left;spacingLeft=8;"
                     "fontStyle=1;fontSize=11;fontColor=#5a6475;",
        })  # fmt: skip
        _geometry(frame, f["x"], f["y"] + HEAD, f["w"], f["h"])
    boxes = {n["id"]: n for n in plan["nodes"]}
    for n in plan["nodes"]:
        fill, stroke = _style(n)
        vms = "".join(f"<br>{_html(v)}" for v in _vm_lines(n))
        obj = ET.SubElement(root, "UserObject", {
            "id": f"n-{_id(n['id'])}",
            "label": f"<b>{_html(n['title'])}</b><br>{_html(n['sub'])}{vms}",
            "automato_kind": kind_text(n["kind"]),
            "automato_ips": ", ".join(n["ips"]), "automato_systems": ", ".join(n["systems"]),
            "automato_verdict": verdict_text(n["verdict"]) if n["verdict"] else "",
            "tooltip": ", ".join(n["systems"] or n["ips"]),
        })  # fmt: skip
        style = ("rounded=1;arcSize=12;whiteSpace=wrap;html=1;align=left;spacingLeft=8;"
                 f"verticalAlign=top;fillColor={fill};strokeColor={stroke};"
                 "fontSize=12;")  # fmt: skip
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
            "automato_verdict": verdict_text(e["verdict"]),
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
    if rules:  # eigene Ebene „Regeln“ - in draw.io ein- und ausblendbar
        ET.SubElement(root, "mxCell", {"id": "regeln", "value": L("Regeln", "Rules"),
                                       "parent": "0"})  # fmt: skip
        for n in plan["nodes"]:
            b = n.get("rules_box")
            if not b:
                continue
            obj = ET.SubElement(root, "UserObject", {
                "id": f"r-{_id(n['id'])}",
                "label": "<br>".join(_html(line) for line in n["rule_lines"]),
                "automato_rules": "; ".join(r["name"] for r in n["rules"]),
            })  # fmt: skip
            cell = ET.SubElement(obj, "mxCell", {
                "vertex": "1", "parent": "regeln",
                "style": f"rounded=0;whiteSpace=wrap;html=1;align=left;verticalAlign=top;"
                         f"spacingLeft=6;fillColor={RULE_FILL};strokeColor={RULE_STROKE};"
                         "fontSize=10;fontFamily=Courier New;",
            })  # fmt: skip
            _geometry(cell, b["x"], b["y"] + HEAD, b["w"], b["h"])
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(mxfile, encoding="unicode")
