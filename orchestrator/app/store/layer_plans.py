"""Plaene je Netzwerkschicht (1.23.0, ADR 0067 Nachtrag 1.23.0) - L1 Physik, L2 Switching und
L3 Routing fuer den ganzen Mandanten, gezeichnet aus den gelesenen Daten, nie erfunden.

Anwender 2026-11-20: „Ist es möglich, dass ich je einen Physical Layer, Layer L2 und einen
Layer L3 Plan erstellt haben könnte von den Daten?“ - „gerne“.

- **L1 - Physik** (`l1`): Geraete der Verkabelung (`store/cabling`: Switches, Router, Firewalls,
  ESXi-Hosts, weitere aus CDP/LLDP) und je Kabel die Ports an beiden Enden und die Kabelnummer
  (`store/cable_labels`, Automato bzw. NetBox); ein Port-Channel ist **eine** Linie mit seinen
  Mitgliedern. Kabel, die nur in NetBox stehen (kein CDP/LLDP dazu), gestrichelt als eigene Ebene.
- **L2 - Switching** (`l2`): weiterleitende Geraete (`Device.switching`) mit Trunks bzw.
  Port-Channels und den erlaubten VLANs (beide Enden; gekuerzt mit „+n“), Spanning-Tree-Root je
  VLAN bzw. MST-Instanz (`store/stp`) und blockierte Ports (Spanning Tree der Switches und die
  blockierten VLANs am Port). Endgeraete an den Switches als ausblendbare Ebene.
- **L3 - Routing** (`l3`): das Topologie-Modell (`store/topo_model`: Firewalls/VDOMs, ACI-VRFs,
  gelesene Router, Segmente, Router ohne Geraet, Anbindungen) mit Transfernetzen, BGP-Sitzungen,
  Routen ueber andere Knoten; dazu die statischen Routen je Kante, die Netze an einem Knoten
  (gefaltet ab `NETS_PER_NODE`) und die IPsec-Tunnel (`store/vpn`) als eigene Linie - zur
  gelesenen Gegenstelle (`peers` bzw. ein Feld `peer` des Tunnels) bzw. zur Adresse der
  Gegenstelle, wenn sie nicht gelesen ist.

**Standort** (Filter, Vorgabe alle): ein gelesenes Element gehoert zum Standort seines
Quellsystems (`sites.source_sites`); was Automato nicht liest (CDP/LLDP-Nachbarn, Segmente,
Router, Netze, Gegenstellen), zum Standort der Elemente, an denen es haengt. Mit Standort zeigt
der Plan dessen Elemente und - blass - die direkten Nachbarn an anderen Standorten
(`foreign`). Gezeichnet wird je Standort ein Rahmen (`groups`).

**Lage serverseitig**: Ebenen je Art bzw. ab dem Kern der Verkabelung (`l2_topo.L2.layers`),
innerhalb der Ebene nach den Nachbarn darueber sortiert und ab `ROW_MAX` umgebrochen. **Keine
stillen Grenzen** (ADR 0048): hoechstens `MAX_NODES` Elemente und `MAX_EDGES` Linien; was
darueber liegt, wird je Art gezaehlt und in `limits` ausgewiesen (Standort waehlen).

**Ebenen** (`layers`): Teile, die die Ansicht bzw. der Export ein- und ausblendet; jedes
Element bzw. jeder Text nennt seine Ebene (ohne = immer sichtbar).

Rein lesend; die Plaene stehen nicht in der Doku einer Applikation (kein Teil im Fingerabdruck,
ADR 0037, bzw. im Datenstand, ADR 0049) - ihre Eingaben aendern ihn bereits.
"""

import ipaddress
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import Site, StpDevice, TopoRoute
from app.store import cable_labels, cabling, l2_topo, stp, topo_model, vpn
from app.store import sites as site_store
from app.store.cabling import End, Link, device_key, port_key, vlan_set

PLAN_LAYERS = ("l1", "l2", "l3")
MAX_NODES = 250  # Elemente je Plan (darueber: gezaehlt, Standort waehlen)
MAX_EDGES = 1200  # Linien je Plan
NETS_PER_NODE = 8  # Netze je L3-Knoten als eigenes Element, der Rest gefaltet
VLAN_RANGES = 4  # VLAN-Bereiche an einer Linie, der Rest als „+n“
ROW_MAX = 8  # Elemente je Zeile, dann umgebrochen
NODE_W, NODE_H, LINE_H = 168, 44, 13
GAP_X, GAP_Y = 40, 90
PAD, HEAD, GROUP_GAP, TOP = 20, 30, 30, 16
NO_SITE = 0  # Filter „ohne Standort“

_SWITCHING = ("switch", "leaf", "spine")


def _kind_text(kind: str) -> str:
    return {
        "switch": "Switch",
        "leaf": L("ACI-Leaf", "ACI leaf"),
        "spine": L("ACI-Spine", "ACI spine"),
        "controller": "APIC",
        "firewall": "Firewall",
        "router": "Router",
        "esxi": L("ESXi-Host", "ESXi host"),
        "unknown": L("nicht gelesen", "not read"),
        "netbox": L("aus NetBox", "from NetBox"),
        "vdom": "VDOM",
        "vrf": L("ACI-VRF", "ACI VRF"),
        "l3": L("Router (gelesen)", "Router (read)"),
        "segment": "Segment",
        "exit": L("Anbindung", "Exit"),
        "net": L("Netz", "Network"),
        "more": L("weitere Netze", "more networks"),
        "vpn_peer": L("VPN-Gegenstelle", "VPN peer"),
    }.get(kind, kind)


def layer_labels(layer: str) -> dict[str, str]:
    """Bezeichnung der Ebenen je Plan (Reihenfolge = Anzeige)."""
    if layer == "l1":
        return {
            "ports": "Ports",
            "labels": L("Kabelnummern", "Cable numbers"),
            "ends": L("Endgeräte (ESXi, nicht gelesen)", "End devices (ESXi, not read)"),
            "netbox": L("Kabel nur in NetBox", "Cables only in NetBox"),
        }
    if layer == "l2":
        return {
            "vlans": L("VLANs", "VLANs"),
            "stp": "Spanning Tree",
            "ports": "Ports",
            "ends": L("Endgeräte an den Switches", "End devices on the switches"),
        }
    return {
        "nets": L("Netze", "Networks"),
        "ports": "Interfaces",
        "static": L("Statische Routen", "Static routes"),
        "bgp": "BGP",
        "ipsec": L("IPsec-Tunnel", "IPsec tunnels"),
    }


def layer_title(layer: str) -> str:
    return {
        "l1": L("L1-Plan · Physik", "L1 plan · Physical"),
        "l2": L("L2-Plan · Switching", "L2 plan · Switching"),
        "l3": L("L3-Plan · Routing", "L3 plan · Routing"),
    }[layer]


def vlan_short(vlans: set[int], limit: int = VLAN_RANGES) -> str:
    """`10,20,30-40,50 +12`: die ersten Bereiche, der Rest als Zahl der VLANs."""
    ordered, parts, i = sorted(vlans), [], 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] == ordered[j] + 1:
            j += 1
        parts.append((ordered[i], ordered[j]))
        i = j + 1
    shown = parts[:limit]
    text = ",".join(str(a) if a == b else f"{a}-{b}" for a, b in shown)
    rest = sum(b - a + 1 for a, b in parts[limit:])
    return f"{text} +{rest}" if rest else text


# -- Bausteine ------------------------------------------------------------------------------


@dataclass
class Node:
    id: str
    label: str
    kind: str
    sub: str = ""
    sites: set[int] = field(default_factory=set)  # Standorte (gelesen: der des Quellsystems)
    read: bool = True
    layer: str | None = None
    badges: list[dict] = field(default_factory=list)  # {text, tone, layer}
    info: list[str] = field(default_factory=list)
    link: dict | None = None  # {view, params} - Detailansicht der Oberflaeche
    level: int = 0
    rank: int = 0  # Vorrang bei der Grenze (klein = zuerst gezeichnet)
    group: int = NO_SITE
    foreign: bool = False
    sourced: bool = False  # gelesen ueber ein Quellsystem: Standort nur von dort, nie geerbt
    x: float = 0.0  # Lage (links oben) und Groesse - aus `_layout`
    y: float = 0.0
    w: float = NODE_W
    h: float = NODE_H


@dataclass
class Edge:
    id: str
    a: str
    b: str
    kind: str
    label: str = ""
    texts: list[dict] = field(default_factory=list)  # {text, layer}
    end_a: str | None = None  # Port bzw. Interface am Ende a (Ebene `ends_layer`)
    end_b: str | None = None
    ends_layer: str = "ports"
    tone: str = "line"
    dash: bool = False
    layer: str | None = None
    info: list[str] = field(default_factory=list)
    link: dict | None = None


class Plan:
    """Elemente und Linien eines Plans - `out` filtert nach Standort, begrenzt und legt aus."""

    def __init__(self, layer: str):
        self.layer = layer
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self.notes: list[str] = []
        self.limits: dict = {}

    def node(self, n: Node) -> Node:
        return self.nodes.setdefault(n.id, n)

    def edge(self, e: Edge) -> Edge | None:
        if e.a not in self.nodes or e.b not in self.nodes or e.a == e.b:
            return None
        self.edges.append(e)
        return e

    # -- Standorte ------------------------------------------------------------------------

    def _spread_sites(self) -> None:
        """Was keinen eigenen Standort hat, gehoert zu den Standorten seiner Nachbarn
        (gezeichnet beim haeufigsten)."""
        near: dict[str, Counter] = defaultdict(Counter)
        for e in self.edges:
            for x, y in ((e.a, e.b), (e.b, e.a)):
                for s in self.nodes[y].sites:
                    near[x][s] += 1
        for n in self.nodes.values():
            if n.sites or n.sourced:
                n.group = min(n.sites) if n.sites else NO_SITE
                continue
            found = near.get(n.id)
            if found:
                n.sites = set(found)
                n.group = max(found, key=lambda s: (found[s], -s))

    def _filter(self, site: int | None) -> tuple[set[str], set[str]]:
        """(eigene, Nachbarn an anderen Standorten) - ohne Standort alle."""
        if site is None:
            return set(self.nodes), set()
        own = {
            k
            for k, n in self.nodes.items()
            if (site in n.sites) or (site == NO_SITE and not n.sites)
        }
        near = set()
        for e in self.edges:
            if e.a in own and e.b not in own:
                near.add(e.b)
            elif e.b in own and e.a not in own:
                near.add(e.a)
        return own, near

    # -- Ausgabe --------------------------------------------------------------------------

    def out(self, site: int | None, sites: dict[int, Site]) -> dict:
        self._spread_sites()
        own, near = self._filter(site)
        for k in near:
            self.nodes[k].foreign = True
        if site is not None:  # gewaehlter Standort: alles, was dazugehoert, in seinem Rahmen
            for k in own:
                self.nodes[k].group = site
        degree: Counter = Counter()
        for e in self.edges:
            degree[e.a] += 1
            degree[e.b] += 1
        cand = sorted(
            own | near,
            key=lambda k: (
                self.nodes[k].foreign,
                self.nodes[k].rank,
                -degree[k],
                self.nodes[k].label.lower(),
                k,
            ),
        )
        kept = set(cand[:MAX_NODES])
        dropped = Counter(self.nodes[k].kind for k in cand[MAX_NODES:])
        shown = own | near
        wanted = [
            e for e in self.edges if e.a in shown and e.b in shown and (e.a in own or e.b in own)
        ]
        edges = [e for e in wanted if e.a in kept and e.b in kept]
        edges.sort(key=lambda e: (e.layer is not None, e.kind, e.a, e.b, e.id))
        edges = edges[:MAX_EDGES]
        self.limits |= {
            "max_nodes": MAX_NODES,
            "max_edges": MAX_EDGES,
            "nodes_total": len(cand),
            "nodes_shown": len(kept),
            "edges_total": len(wanted),
            "edges_shown": len(edges),
            "dropped": {_kind_text(k): v for k, v in sorted(dropped.items())},
        }
        nodes = [self.nodes[k] for k in cand if k in kept]
        groups, width, height = _layout(nodes, edges, sites)
        pos = {n.id: n for n in nodes}
        edge_out = _edges_out(edges, pos)
        counts_nodes = Counter(n.kind for n in nodes if not n.foreign)
        counts_edges = Counter(e.kind for e in edges)
        names = layer_labels(self.layer)
        used = {n.layer for n in nodes} | {e.layer for e in edges}
        used |= {t.get("layer") for e in edges for t in e.texts}
        used |= {b.get("layer") for n in nodes for b in n.badges}
        used |= {e.ends_layer for e in edges if e.end_a or e.end_b}
        return {
            "layer": self.layer,
            "title": layer_title(self.layer),
            "site": site,
            "layers": [{"key": k, "label": v, "used": k in used} for k, v in names.items()],
            "groups": groups,
            "nodes": [_node_out(n) for n in nodes],
            "edges": edge_out,
            "summary": {
                "nodes": dict(counts_nodes),
                "edges": dict(counts_edges),
                "foreign": sum(1 for n in nodes if n.foreign),
            },
            "limits": self.limits,
            "notes": self.notes,
            "width": width,
            "height": height,
        }


def _node_out(n: Node) -> dict:
    return {
        "id": n.id,
        "label": n.label,
        "kind": n.kind,
        "kind_text": _kind_text(n.kind),
        "sub": n.sub,
        "read": n.read,
        "layer": n.layer,
        "badges": n.badges,
        "info": n.info,
        "link": n.link,
        "foreign": n.foreign,
        "group": n.group,
        "sites": sorted(n.sites),
        "x": n.x,
        "y": n.y,
        "w": n.w,
        "h": n.h,
    }


# -- Lage -----------------------------------------------------------------------------------


def _site_order(site: Site | None) -> tuple:
    if site is None:
        return (2, "")
    return (0 if site.kind == "physical" else 1, (site.code or "").lower())


def _group_title(site: Site | None) -> str:
    if site is None:
        return L("ohne Standort", "without site")
    return f"{site.code} · {site.name}" if site.name else site.code


def _layout(nodes: list[Node], edges: list[Edge], sites: dict[int, Site]) -> tuple:
    """Je Standort ein Rahmen (untereinander), darin Zeilen je Ebene; innerhalb der Zeile nach
    dem Mittel der Nachbarn darueber, dann Art und Name; ab `ROW_MAX` umgebrochen."""
    adj: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        adj[e.a].add(e.b)
        adj[e.b].add(e.a)
    by_group: dict[int, list[Node]] = defaultdict(list)
    for n in nodes:
        by_group[n.group].append(n)
        n.w = NODE_W
        n.h = NODE_H + LINE_H * min(len(n.badges), 3)
    order = sorted(by_group, key=lambda g: _site_order(sites.get(g)))
    step = NODE_W + GAP_X
    widths = {}
    for g in order:
        rows = Counter(n.level for n in by_group[g])
        widths[g] = min(ROW_MAX, max(rows.values())) * step - GAP_X + 2 * PAD
    width = max([*widths.values(), 400]) + 2 * PAD
    groups, y = [], TOP
    x_of: dict[str, float] = {}
    for g in order:
        gx, gw = PAD, max(widths[g], 360)
        top = y
        y += HEAD
        levels: dict[int, list[Node]] = defaultdict(list)
        for n in by_group[g]:
            levels[n.level].append(n)

        def place(row: list[Node], gx: float = gx, gw: float = gw) -> None:
            for i in range(0, len(row), ROW_MAX):
                chunk = row[i : i + ROW_MAX]
                x0 = gx + (gw - (len(chunk) * step - GAP_X)) / 2
                for j, n in enumerate(chunk):
                    n.x = x0 + j * step
                    x_of[n.id] = n.x + NODE_W / 2

        def bary(n: Node, only_placed: bool) -> tuple:
            near = [x_of[k] for k in adj.get(n.id, ()) if k in x_of and k != n.id]
            if not near and not only_placed and n.id in x_of:
                near = [x_of[n.id]]
            return (sum(near) / len(near) if near else 1e9, n.rank, n.label.lower(), n.id)

        # von oben nach unten nach den Nachbarn darueber, dann zweimal nach allen Nachbarn
        for lv in sorted(levels):
            levels[lv].sort(key=lambda n: bary(n, True))
            place(levels[lv])
        for _sweep in range(2):
            for lv in sorted(levels):
                levels[lv].sort(key=lambda n: bary(n, False))
                place(levels[lv])
        for lv in sorted(levels):
            row = levels[lv]
            for i in range(0, len(row), ROW_MAX):
                chunk = row[i : i + ROW_MAX]
                for n in chunk:
                    n.y = y
                y += max(n.h for n in chunk) + GAP_Y
        y += PAD - GAP_Y + 10
        site = sites.get(g)
        groups.append(
            {
                "id": g,
                "title": _group_title(site),
                "x": gx,
                "y": top,
                "w": gw,
                "h": y - top,
                "code": site.code if site else None,
            }
        )
        y += GROUP_GAP
    return groups, width, y


def _center(n) -> tuple[float, float]:
    return n.x + n.w / 2, n.y + n.h / 2


def _border(n, dx: float, dy: float) -> float:
    """Anteil der Strecke vom Mittelpunkt bis zum Rand des Rechtecks in Richtung (dx, dy)."""
    tx = (n.w / 2) / abs(dx) if dx else float("inf")
    ty = (n.h / 2) / abs(dy) if dy else float("inf")
    return min(tx, ty)


def _port(x: float, y: float, sign: int, text: str | None, unit: tuple) -> dict | None:
    """Beschriftung eines Endes: etwas vom Rand weg in Richtung der Linie, seitlich versetzt."""
    if not text:
        return None
    ux, uy, nx, ny = unit
    px, py = x + sign * ux * 10 + nx * 9, y + sign * uy * 10 + ny * 9
    anchor = "start" if (sign * ux) >= -0.2 else "end"
    return {"x": round(px, 1), "y": round(py, 1), "text": text, "anchor": anchor}


def _hits(x1: float, y1: float, x2: float, y2: float, n: Node, pad: float = 6) -> bool:
    """Schneidet die Strecke das Rechteck des Elements (mit Rand)? (Liang-Barsky)"""
    left, top, right, bottom = n.x - pad, n.y - pad, n.x + n.w + pad, n.y + n.h + pad
    if max(x1, x2) < left or min(x1, x2) > right or max(y1, y2) < top or min(y1, y2) > bottom:
        return False
    dx, dy = x2 - x1, y2 - y1
    lo, hi = 0.0, 1.0
    for p, q in ((-dx, x1 - left), (dx, right - x1), (-dy, y1 - top), (dy, bottom - y1)):
        if p == 0:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            lo = max(lo, t)
        else:
            hi = min(hi, t)
        if lo > hi:
            return False
    return True


def _toward(n: Node, cx: float, cy: float, tx: float, ty: float) -> tuple[float, float]:
    """Punkt am Rand des Elements (Mittelpunkt cx/cy) in Richtung tx/ty."""
    dx, dy = tx - cx, ty - cy
    t = min(_border(n, dx, dy), 0.45) if (dx or dy) else 0.0
    return cx + dx * t, cy + dy * t


def _route(a: Node, b: Node, start: tuple, end: tuple, normal: tuple, others: list[Node]) -> list:
    """Gerade, wenn sie kein anderes Element schneidet; sonst ueber einen Knickpunkt seitlich
    der Mitte (erst nah, dann weiter weg) - eine Linie fuehrt nie sichtbar durch ein Element."""
    (ax, ay), (bx, by) = start, end
    straight = [_toward(a, ax, ay, bx, by), _toward(b, bx, by, ax, ay)]
    (sx, sy), (ex, ey) = straight
    if not any(_hits(sx, sy, ex, ey, n) for n in others):
        return straight
    mx, my = (ax + bx) / 2, (ay + by) / 2
    for k in (70, -70, 120, -120, 180, -180, 260, -260):
        wx, wy = mx + normal[0] * k, my + normal[1] * k
        p1, p2 = _toward(a, ax, ay, wx, wy), _toward(b, bx, by, wx, wy)
        if not any(_hits(*p1, wx, wy, n) or _hits(wx, wy, *p2, n) for n in others):
            return [p1, (wx, wy), p2]
    return straight


def _unit(p: tuple, q: tuple, normal: tuple) -> tuple:
    """Richtung von p nach q (Einheitsvektor) und die Normale der Linie."""
    ux, uy = q[0] - p[0], q[1] - p[1]
    d = max((ux * ux + uy * uy) ** 0.5, 1.0)
    return (ux / d, uy / d, *normal)


def _edges_out(edges: list[Edge], pos: dict[str, Node]) -> list[dict]:
    """Linien mit Punkten (parallele Linien eines Paars versetzt, um Elemente herum geknickt),
    Beschriftung in der Mitte und den Ports nahe den Enden."""
    pairs: dict[frozenset, list[Edge]] = defaultdict(list)
    for e in edges:
        pairs[frozenset((e.a, e.b))].append(e)
    nodes = list(pos.values())
    out = []
    for e in edges:
        same = pairs[frozenset((e.a, e.b))]
        idx = same.index(e)
        a, b = pos[e.a], pos[e.b]
        (ax, ay), (bx, by) = _center(a), _center(b)
        dx, dy = bx - ax, by - ay
        length = max((dx * dx + dy * dy) ** 0.5, 1.0)
        nx, ny = -dy / length, dx / length
        if e.a > e.b:  # gleiche Richtung des Versatzes fuer beide Richtungen eines Paars
            nx, ny = -nx, -ny
        off = (idx - (len(same) - 1) / 2) * 16
        start, end = (ax + nx * off, ay + ny * off), (bx + nx * off, by + ny * off)
        others = [n for n in nodes if n is not a and n is not b]
        pts = _route(a, b, start, end, (nx, ny), others)
        (sx, sy), (ex, ey) = pts[0], pts[-1]
        if len(pts) == 3:
            mx, my = pts[1]
        else:
            mx, my = (sx + ex) / 2, (sy + ey) / 2
        normal = (nx, ny)
        out.append(
            {
                "id": e.id,
                "a": e.a,
                "b": e.b,
                "kind": e.kind,
                "label": e.label,
                "texts": e.texts,
                "tone": e.tone,
                "dash": e.dash,
                "layer": e.layer,
                "info": e.info,
                "link": e.link,
                "ends_layer": e.ends_layer,
                "end_a": e.end_a,
                "end_b": e.end_b,
                "points": [[round(x, 1), round(y, 1)] for x, y in pts],
                "lx": round(mx + nx * 6, 1),
                "ly": round(my + ny * 6 - 3, 1),
                "pa": _port(sx, sy, 1, e.end_a, _unit(pts[0], pts[1], normal)),
                "pb": _port(ex, ey, 1, e.end_b, _unit(pts[-1], pts[-2], normal)),
            }
        )
    return out


# -- Gemeinsam: Standorte, Geraete der Verkabelung, Kabel je Buendel -------------------------


def _source_site_ids(session: Session, tenant_id: int) -> dict[int, int]:
    return {sid: s.id for sid, s in site_store.source_sites(session, tenant_id).items() if s}


def _device_node(d: cabling.Device, site_of: dict[int, int], layer: str | None, level: int) -> Node:
    rank = (
        0
        if d.read and d.switching
        else 1
        if d.read and d.kind in ("firewall", "router")
        else 2
        if d.read
        else 3
        if d.switching
        else 4
    )
    sub = _kind_text(d.kind)
    if d.kind == "unknown" and d.switching:  # wie es sich per CDP/LLDP meldet
        bridge = d.caps & {"switch", "bridge", "b", "trans-bridge", "source-route-bridge"}
        sub = (L("Switch (nicht gelesen)", "Switch (not read)") if bridge
               else L("Router (nicht gelesen)", "Router (not read)"))  # fmt: skip
    if d.model:
        sub += f" · {d.model}"
    info = [
        x
        for x in (
            L(f"Quelle: {d.source}", f"Source: {d.source}") if d.source else None,
            L(f"Management: {d.mgmt_ip}", f"Management: {d.mgmt_ip}") if d.mgmt_ip else None,
            L(f"Version: {d.version}", f"Version: {d.version}") if d.version else None,
            d.remote_platform if not d.read and d.remote_platform else None,
        )
        if x
    ]
    site = site_of.get(d.source_system_id) if d.source_system_id else None
    return Node(
        id=f"dev:{d.key}",
        label=d.name,
        kind=d.kind,
        sub=sub,
        read=d.read,
        sites={site} if site else set(),
        sourced=d.source_system_id is not None,
        layer=layer,
        info=info,
        level=level,
        rank=rank,
        link={"view": "verkabelung", "params": {"device": d.key}},
    )


@dataclass
class Bundle:
    """Kabel eines Geraetepaars: ein Port-Channel (Mitglieder) bzw. ein einzelnes Kabel."""

    a: cabling.Device
    b: cabling.Device
    lag_a: str | None
    lag_b: str | None
    links: list[tuple[End, End, Link]] = field(default_factory=list)  # Ende a, Ende b, Kabel

    @property
    def key(self) -> str:
        if self.lag_a or self.lag_b:
            pa, pb = port_key(self.lag_a or ""), port_key(self.lag_b or "")
            return f"{self.a.key}|{pa}~{self.b.key}|{pb}"
        return self.links[0][2].id


def bundles(cab: cabling.Cabling, keep=None) -> list[Bundle]:
    """Kabel je Geraetepaar zusammengefasst: Mitglieder desselben Port-Channels (an einem bzw.
    beiden Enden) sind ein Buendel, uebrige Kabel einzeln. `keep(link)` filtert."""
    found: dict[tuple, Bundle] = {}
    for link in sorted(cab.links.values(), key=lambda lk: lk.id):
        if keep is not None and not keep(link):
            continue
        ea, eb = link.a, link.b
        if eb.device.key < ea.device.key:
            ea, eb = eb, ea
        la = ea.iface.lag if ea.iface is not None and ea.iface.lag else None
        lb = eb.iface.lag if eb.iface is not None and eb.iface.lag else None
        key = (
            (ea.device.key, eb.device.key, port_key(la or ""), port_key(lb or ""))
            if la or lb
            else (link.id,)
        )
        bundle = found.get(key)
        if bundle is None:
            bundle = found[key] = Bundle(ea.device, eb.device, la, lb)
        bundle.links.append((ea, eb, link))
    return list(found.values())


def _end_text(lag: str | None, ports: list[str | None]) -> str:
    names = [p or "?" for p in ports]
    if lag:
        return f"{lag}: {', '.join(names)}" if names != [lag] else lag
    return ", ".join(names)


def _speed(b: Bundle) -> str:
    """Speed der Ports (gelesen, beide Enden): `10G`, bei Abweichung `100M/1G`."""
    found = {cabling.speed_mbps(e.iface.speed) for ea, eb, _lk in b.links for e in (ea, eb)
             if e.iface is not None}  # fmt: skip
    values = sorted(v for v in found if v)
    return "/".join(f"{v / 1000:g}G" if v >= 1000 else f"{v}M" for v in values)


def _levels(topo: l2_topo.L2, keys: set[str]) -> dict[str, int]:
    """Ebenen ab dem Kern (`l2_topo.L2.layers`); Geraete ohne Kabel in eine eigene Zeile darunter
    (nicht in die Zeile des Kerns)."""
    cabled = {k for k in keys if topo.adj.get(k)}
    level = topo.layers(sorted(cabled))
    below = max(level.values(), default=-1) + 1
    return level | {k: below for k in keys - cabled}


# -- L1 -------------------------------------------------------------------------------------


def l1(session: Session, tenant_id: int, cab: cabling.Cabling | None = None) -> Plan:
    cab = cab or cabling.build(session, tenant_id)
    topo = l2_topo.L2(session, tenant_id, cab)
    site_of = _source_site_ids(session, tenant_id)
    labels = cable_labels.Index(session, tenant_id)
    plan = Plan("l1")
    keys = {e.device.key for lk in cab.links.values() for e in (lk.a, lk.b)}
    keys |= {
        k
        for k, d in cab.devices.items()
        if d.read and d.kind in (*_SWITCHING, "router", "firewall")
    }
    level = _levels(topo, keys)
    for k in sorted(keys):
        d = cab.devices[k]
        end = not d.switching and (d.kind == "esxi" or not d.read)
        plan.node(_device_node(d, site_of, "ends" if end else None, level.get(k, 0)))
    for b in bundles(cab):
        _l1_edge(plan, b, labels)
    cabled = {e.device.key for lk in cab.links.values() for e in (lk.a, lk.b)}
    alone = [plan.nodes[f"dev:{k}"] for k in sorted(keys - cabled)]
    for n in alone:  # gelesen, aber kein Kabel bekannt (CDP/LLDP aus bzw. nicht geliefert)
        n.badges.append({"text": L("kein Kabel gelesen", "no cable read"), "tone": "muted",
                         "layer": None})  # fmt: skip
    plan.limits["without_cables"] = len(alone)
    # Kabel nur in NetBox (kein CDP/LLDP dazu), an mindestens einem bekannten Geraet
    netbox_only = 0
    for c in labels.cables:
        if c.pair in cab.links:
            continue
        ka, kb = device_key(c.a[0]), device_key(c.b[0])
        if ka not in keys and kb not in keys:
            continue
        for k, name in ((ka, c.a[0]), (kb, c.b[0])):
            if f"dev:{k}" not in plan.nodes:
                d = cab.devices.get(k)
                if d is not None:
                    plan.node(_device_node(d, site_of, None, level.get(k, 0)))
                    continue
                ups = [level.get(x, 0) for x in (ka, kb) if x != k and x in level]
                plan.node(
                    Node(
                        id=f"dev:{k}",
                        label=name,
                        kind="netbox",
                        read=False,
                        sub=_kind_text("netbox"),
                        layer="netbox",
                        level=(min(ups) + 1) if ups else 0,
                        rank=5,
                        info=[L(f"Quelle: {c.source}", f"Source: {c.source}")],
                    )
                )
        edge = plan.edge(
            Edge(
                id=f"nb:{c.source_system_id}:{c.id}",
                a=f"dev:{ka}",
                b=f"dev:{kb}",
                kind="netbox",
                label=c.label or "",
                end_a=c.a[1],
                end_b=c.b[1],
                tone="netbox",
                dash=True,
                layer="netbox",
                info=[
                    L(
                        f"Kabel nur in NetBox ({c.source}) – kein CDP/LLDP dazu",
                        f"Cable only in NetBox ({c.source}) – no CDP/LLDP for it",
                    )
                ],
                link={"view": "verkabelung", "params": {"tab": "labels", "q": c.label or c.a[0]}},
            )
        )
        netbox_only += 1 if edge else 0
    plan.limits["netbox_only"] = netbox_only
    return plan


def _l1_edge(plan: Plan, b: Bundle, labels: cable_labels.Index) -> None:
    numbers: list[str] = []
    issues: list[str] = []
    states = set()
    for _ea, _eb, link in b.links:
        lab = labels.label(link.id)
        if lab:
            mine = (lab.get("automato") or {}).get("label")
            theirs = [c["label"] for c in lab.get("netbox") or [] if c.get("label")]
            for x in [mine, *theirs]:
                if x and x not in numbers:
                    numbers.append(x)
        issues += [i["text"] for i in link.issues]
        states.add(link.state)
    n = len(b.links)
    speed = _speed(b)
    label = " ".join(x for x in (f"{n}×" if n > 1 else "", speed) if x)
    tone = "warn" if issues else "muted" if states == {"unread"} else "line"
    info = [f"{ea.port or '?'} ↔ {eb.port or '?'}" for ea, eb, _lk in b.links]
    if "one_sided" in states:
        info.append(L("nur einseitig gemeldet (CDP/LLDP)", "reported by one side only (CDP/LLDP)"))
    info += issues
    q = numbers[0] if numbers else None
    link = (
        {"view": "verkabelung", "params": {"tab": "labels", "q": q}}
        if q
        else {"view": "verkabelung", "params": {"device": b.a.key}}
    )
    plan.edge(
        Edge(
            id=f"cab:{b.key}",
            a=f"dev:{b.a.key}",
            b=f"dev:{b.b.key}",
            kind="lag" if (b.lag_a or b.lag_b) else "cable",
            label=label,
            texts=[
                {
                    "text": L(f"Nr. {', '.join(numbers)}", f"No. {', '.join(numbers)}"),
                    "layer": "labels",
                }
            ]
            if numbers
            else [],
            end_a=_end_text(b.lag_a, [ea.port for ea, _eb, _lk in b.links]),
            end_b=_end_text(b.lag_b, [eb.port for _ea, eb, _lk in b.links]),
            tone=tone,
            dash=states <= {"one_sided", "unread"} and "unread" not in states,
            info=info,
            link=link,
        )
    )


# -- L2 -------------------------------------------------------------------------------------


def _allowed(iface) -> tuple[str | None, set[int] | None, int | None]:
    """(Modus, VLANs, Native VLAN) eines Ports; None = unbekannt."""
    if iface is None or not iface.mode:
        return None, None, None
    if iface.mode == "access":
        return "access", ({iface.untagged_vlan} if iface.untagged_vlan else None), None
    vs = vlan_set(iface.trunk_vlans) if iface.trunk_vlans else set(iface.tagged_vlans or [])
    native = iface.native_vlan if iface.native_vlan is not None else iface.untagged_vlan
    return "tagged", (vs or None), native


def _side_iface(cab: cabling.Cabling, dev: cabling.Device, lag: str | None, end: End):
    """Konfiguration einer Seite: der Port-Channel (dort stehen VLANs) bzw. der Port."""
    if lag:
        po = cab.ifaces.get((dev.key, port_key(lag)))
        if po is not None and po.mode:
            return po
    return end.iface


def _stp_blocked(model: stp.Model) -> dict[tuple, set[str]]:
    """(Geraet, Port) -> blockiert in (VLAN- bzw. MST-Bezeichnungen) aus dem Spanning Tree."""
    out: dict[tuple, set[str]] = defaultdict(set)
    for tree in model.trees.values():
        for m in tree.members:
            for p in m.ports or []:
                if p.get("state") in stp.BLOCKED_STATES and p.get("port"):
                    out[(device_key(m.device), port_key(p["port"]))].add(tree.label)
    return out


def l2(session: Session, tenant_id: int, cab: cabling.Cabling | None = None) -> Plan:
    cab = cab or cabling.build(session, tenant_id)
    topo = l2_topo.L2(session, tenant_id, cab)
    site_of = _source_site_ids(session, tenant_id)
    model = stp.Model(session, tenant_id)
    blocked = _stp_blocked(model)
    plan = Plan("l2")
    switches = set(topo.nodes(ends=False))
    ends = {
        e.device.key
        for lk in cab.links.values()
        for e, o in ((lk.a, lk.b), (lk.b, lk.a))
        if o.device.key in switches and e.device.key not in switches
    }
    level = _levels(topo, switches | ends)
    for k in sorted(switches | ends):
        plan.node(
            _device_node(
                cab.devices[k], site_of, None if k in switches else "ends", level.get(k, 0)
            )
        )
    for k in switches:
        node = plan.nodes[f"dev:{k}"]
        node.link = {"view": "spanningtree", "params": {"q": node.label}}
    # Root je VLAN bzw. Instanz
    roots: dict[str, list] = defaultdict(list)
    for tree in model.trees.values():
        for name in model.root_names(tree):
            roots[device_key(name)].append(tree)
    for k, trees in roots.items():
        node = plan.nodes.get(f"dev:{k}")
        if node is None:
            continue
        vl = {t.number for t in trees if t.kind == "vlan"}
        mst = sorted({t.number for t in trees if t.kind == "mst"})
        parts = ([f"VLAN {vlan_short(vl, 3)}"] if vl else []) + [f"MST {m}" for m in mst[:3]]
        node.badges.append({"text": "Root " + ", ".join(parts), "tone": "root", "layer": "stp"})
    # Spanning Tree je Switch: nicht gelesen bzw. aus - gezaehlt
    status = {
        device_key(d.device): d.status
        for d in session.scalars(select(StpDevice).where(StpDevice.tenant_id == tenant_id))
    }
    unread = 0
    for k in switches:
        dev = cab.devices[k]
        if not dev.read or dev.kind not in ("switch",):
            continue
        st = status.get(k)
        if st == "read":
            continue
        unread += 1
        text = {
            "off": L("STP aus", "STP off"),
            "unsupported": L("STP: Plattform nicht unterstützt", "STP: platform not supported"),
            "unreadable": L("STP nicht lesbar", "STP not readable"),
        }.get(st or "", L("STP nicht gelesen", "STP not read"))
        plan.nodes[f"dev:{k}"].badges.append({"text": text, "tone": "muted", "layer": "stp"})
    plan.limits["stp_unread"] = unread
    if unread:
        plan.notes.append(
            L(
                f"{unread} Switches ohne gelesenen Spanning Tree – Root und "
                "blockierte Ports dort unbekannt.",
                f"{unread} switches without Spanning Tree read – root and blocked "
                "ports unknown there.",
            )
        )
    nodes = switches | ends
    for b in bundles(
        cab,
        keep=lambda lk: lk.a.device.key in nodes
        and lk.b.device.key in nodes
        and (lk.a.device.key in switches or lk.b.device.key in switches),
    ):
        _l2_edge(plan, cab, topo, blocked, b)
    return plan


def _l2_edge(plan: Plan, cab, topo: l2_topo.L2, blocked: dict, b: Bundle) -> None:
    ea0, eb0, _lk = b.links[0]
    ma, va, na = _allowed(_side_iface(cab, b.a, b.lag_a, ea0))
    mb, vb, nb = _allowed(_side_iface(cab, b.b, b.lag_b, eb0))
    info = [f"{ea.port or '?'} ↔ {eb.port or '?'}" for ea, eb, _lk in b.links]
    texts: list[dict] = []
    tone, mismatch = "line", False
    modes = {m for m in (ma, mb) if m}
    if len(modes) > 1:
        mismatch = True
    if va is not None and vb is not None:
        allowed = va & vb
        if va != vb:
            mismatch = True
    else:
        allowed = va if va is not None else vb
    native = {n for n in (na, nb) if n}
    if "tagged" in modes:
        text = "Trunk " + (
            vlan_short(allowed) if allowed else L("VLANs unbekannt", "VLANs unknown")
        )
        if native - {1}:
            text += f" · Native {'/'.join(str(n) for n in sorted(native))}"
    elif "access" in modes:
        text = L("Access VLAN ", "Access VLAN ") + (
            "/".join(str(v) for v in sorted((va or set()) | (vb or set()))) or "?"
        )
    else:
        text = L("VLANs unbekannt", "VLANs unknown")
    texts.append({"text": text, "layer": "vlans"})
    if mismatch:
        tone = "warn"
        side = lambda m, v: (m or "?") + (f" {vlan_short(v)}" if v else "")  # noqa: E731
        info.append(
            L(
                f"abweichend: {b.a.name} {side(ma, va)} ↔ {b.b.name} {side(mb, vb)}",
                f"differs: {b.a.name} {side(ma, va)} ↔ {b.b.name} {side(mb, vb)}",
            )
        )
    # blockierte Ports (Spanning Tree je Instanz bzw. die blockierten VLANs am Port)
    marks = []
    for dev, lag, idx in ((b.a, b.lag_a, 0), (b.b, b.lag_b, 1)):
        found: set[str] = set()
        vids: set[int] = set()
        for pair in b.links:
            end = pair[idx]
            for p in {end.port, lag} - {None}:
                found |= blocked.get((dev.key, port_key(p)), set())
            vids |= topo.stp_blocked(end)
        if vids:
            found.add(f"VLAN {vlan_short(vids, 3)}")
        if found:
            port = lag or b.links[0][idx].port or "?"
            what = ", ".join(sorted(found))
            marks.append((dev, port, what))
    end_a = _end_text(b.lag_a, [ea.port for ea, _eb, _lk in b.links])
    end_b = _end_text(b.lag_b, [eb.port for _ea, eb, _lk in b.links])
    dash = False
    for dev, port, what in marks:
        texts.append(
            {
                "text": L(
                    f"✕ STP blockiert {dev.name} {port}: {what}",
                    f"✕ STP blocks {dev.name} {port}: {what}",
                ),
                "layer": "stp",
            }
        )
        info.append(
            L(
                f"Spanning Tree blockiert {dev.name} {port} ({what})",
                f"Spanning Tree blocks {dev.name} {port} ({what})",
            )
        )
        tone, dash = "stp", True
    plan.edge(
        Edge(
            id=f"l2:{b.key}",
            a=f"dev:{b.a.key}",
            b=f"dev:{b.b.key}",
            kind="lag"
            if (b.lag_a or b.lag_b)
            else ("trunk" if "tagged" in modes else "access" if "access" in modes else "link"),
            label=f"{len(b.links)}×" if len(b.links) > 1 else "",
            texts=texts,
            end_a=end_a,
            end_b=end_b,
            tone=tone,
            dash=dash,
            info=info,
            link={"view": "verkabelung", "params": {"tab": "ports", "device": b.a.key}},
        )
    )


# -- L3 -------------------------------------------------------------------------------------

_L3_LEVEL = {
    "exit": 0,
    "router": 0,
    "vpn_peer": 0,
    "firewall": 1,
    "vdom": 1,
    "l3": 2,
    "vrf": 2,
    "segment": 3,
    "net": 4,
    "more": 4,
}
_L3_RANK = {
    "firewall": 0,
    "vdom": 0,
    "l3": 0,
    "vrf": 1,
    "exit": 1,
    "segment": 2,
    "router": 3,
    "vpn_peer": 3,
    "net": 5,
    "more": 5,
}


def _l3_node(n: dict, site_of: dict[int, int]) -> Node:
    kind = n["kind"]
    sid = n.get("source_system_id")
    site = site_of.get(sid) if sid else None
    badges, info = [], []
    link = None
    sub = _kind_text(kind)
    if kind in ("firewall", "vdom", "vrf", "l3"):
        link = {"view": "routing", "params": {"tab": "routes", "context": n["id"]}}
        if n.get("source"):
            info.append(L(f"Quelle: {n['source']}", f"Source: {n['source']}"))
        ha = n.get("ha") or {}
        if ha.get("mode") and ha["mode"] != "standalone":
            badges.append({"text": f"HA {ha['mode']}", "tone": "info", "layer": None})
            if ha.get("members"):
                info.append("HA: " + ", ".join(ha["members"]))
        if n.get("group"):
            badges.append(
                {
                    "text": L(f"Verbund {n['group']}", f"Group {n['group']}"),
                    "tone": "info",
                    "layer": None,
                }
            )
        if n.get("missing"):
            info.append(L("nicht lesbar: ", "not readable: ") + ", ".join(n["missing"]))
    elif kind == "segment":
        sub = f"Segment · {n.get('network')}"
        link = {"view": "objekte", "params": {"tab": "subnets", "q": n.get("network") or ""}}
        info.append(", ".join(n.get("members") or []))
    elif kind == "router":
        sub = n.get("ip") or ""
        if n.get("remote_as"):
            sub += f" · AS {n['remote_as']}"
        link = (
            {"view": "routing", "params": {"tab": "peers", "peer": n["ip"]}}
            if n.get("remote_as")
            else {"view": "routing", "params": {"tab": "exits"}}
        )
        info += [x for x in n.get("names") or []]
    elif kind == "exit":
        sub = f"{_kind_text('exit')} · {n.get('exit_kind') or ''}".rstrip(" ·")
        link = {"view": "routing", "params": {"tab": "exits"}}
    if n.get("at_boundary") or n.get("boundary"):
        badges.append(
            {"text": L("Firmengrenze", "Company boundary"), "tone": "warn", "layer": None}
        )
    return Node(
        id=n["id"],
        label=n.get("label") or n["id"],
        kind=kind,
        sub=sub,
        sites={site} if site else set(),
        sourced=bool(sid),
        read=kind in ("firewall", "vdom", "vrf", "l3"),
        badges=badges,
        info=info,
        link=link,
        level=_L3_LEVEL.get(kind, 2),
        rank=_L3_RANK.get(kind, 4),
    )


def _via(edge: topo_model.Edge, node: str) -> str | None:
    found = [v for v in edge.via if v.get("node") == node]
    if not found:
        return None
    return ", ".join(
        f"{v.get('interface') or '?'} {v.get('address', '').split('/')[0]}".strip()
        for v in found[:2]
    )


def _routes_info(e: topo_model.Edge, names: dict[str, str]) -> list[str]:
    out = []
    for way, n in sorted(e.active.items()):
        src, dst = (e.a, e.b) if way == "a>b" else (e.b, e.a)
        out.append(
            L(
                f"{n} Routen {names.get(src, src)} → {names.get(dst, dst)}",
                f"{n} routes {names.get(src, src)} → {names.get(dst, dst)}",
            )
        )
    return out


def l3(session: Session, tenant_id: int) -> Plan:
    m = topo_model.Model(session, tenant_id).build()
    site_of = _source_site_ids(session, tenant_id)
    plan = Plan("l3")
    names = {k: n.get("label") or k for k, n in m.nodes.items()}
    for n in m.nodes.values():
        plan.node(_l3_node(n, site_of))
    by_pair: dict[frozenset, Edge] = {}
    for e in m.edges.values():
        out = _l3_edge(e, names)
        if out is not None and plan.edge(out):
            if e.kind in ("transfer", "route", "uplink", "vdom_link"):
                by_pair.setdefault(frozenset((out.a, out.b)), out)
    _l3_nets(plan, m)
    _l3_static(plan, session, tenant_id, m, by_pair)
    _l3_ipsec(plan, session, tenant_id)
    return plan


def _l3_edge(e: topo_model.Edge, names: dict[str, str]) -> Edge | None:
    base = {
        "id": f"{e.kind}:{e.a}~{e.b}~{e.network or ''}",
        "a": e.a,
        "b": e.b,
        "kind": e.kind,
        "info": _routes_info(e, names),
    }
    if e.kind == "transfer":
        return Edge(
            **base,
            label=e.network or "",
            end_a=_via(e, e.a),
            end_b=_via(e, e.b),
            link={"view": "objekte", "params": {"tab": "subnets", "q": e.network or ""}},
        )
    if e.kind == "member":
        return Edge(**base, end_a=_via(e, e.a), tone="line")
    if e.kind == "vdom_link":
        links = e.attrs.get("links") or []
        return Edge(
            **base,
            label="Inter-VDOM",
            tone="muted",
            end_a=", ".join(x["a"] for x in links[:2]) or None,
            end_b=", ".join(x["b"] for x in links[:2]) or None,
        )
    if e.kind == "route":
        hops = e.attrs.get("next_hops") or []
        return Edge(
            **base,
            label=L("Route über ", "Route via ") + ", ".join(hops[:2]),
            tone="route",
            dash=True,
        )
    if e.kind == "bgp":
        sessions = e.attrs.get("sessions") or []
        up = all((s.get("state") or "").lower() == "established" for s in sessions)
        asn = sorted({str(s["remote_as"]) for s in sessions if s.get("remote_as")})
        info = [
            f"{names.get(s['from'], s['from'])} → {s['peer']} AS {s.get('remote_as') or '?'}"
            f" ({s.get('state') or '?'})"
            for s in sessions
        ]
        return Edge(
            **{**base, "info": info},
            label="BGP" + (f" AS {'/'.join(asn)}" if asn else ""),
            tone="bgp" if up else "bad",
            dash=True,
            layer="bgp",
            link={"view": "routing", "params": {"tab": "peers", "peer": sessions[0]["peer"]}}
            if sessions
            else None,
        )
    if e.kind == "uplink":
        routers = e.attrs.get("routers") or []
        ifaces = sorted({r["interface"] for r in routers if r.get("interface")})
        texts = []
        for r in routers:
            if r.get("remote_as"):
                texts.append(
                    {
                        "text": f"BGP AS {r['remote_as']}"
                        + (f" ({r['state']})" if r.get("state") else ""),
                        "layer": "bgp",
                    }
                )
        status = e.attrs.get("status")
        info = base["info"] + [f"{r['ip']}: {r.get('status')}" for r in routers]
        if e.attrs.get("boundary"):
            info.append(L("Firmengrenze", "Company boundary"))
        return Edge(
            **{**base, "info": info},
            end_a=", ".join(ifaces[:2]) or None,
            label=", ".join(r["ip"] for r in routers[:1]) if routers else "",
            texts=texts,
            tone="line" if status in (None, "active") else "muted",
            dash=status not in (None, "active"),
        )
    if e.kind == "exit_route":
        return Edge(
            **base, label=", ".join(e.attrs.get("prefixes") or [])[:40], tone="muted", dash=True
        )
    if e.kind == "parallel":
        return Edge(
            **base,
            label=L(f"Verbund {e.attrs.get('group')}", f"Group {e.attrs.get('group')}"),
            tone="muted",
            dash=True,
        )
    if e.kind == "sync":
        return Edge(**base, label="Session-Sync", tone="muted", dash=True)
    return None


def _l3_nets(plan: Plan, m: topo_model.Model) -> None:
    """Netze an genau einem Knoten (LAN, Server) als Elemente; je Knoten hoechstens
    `NETS_PER_NODE`, der Rest in einem Element „+n Netze“ (gezaehlt)."""
    per: dict[str, list[tuple[str, list]]] = defaultdict(list)
    for text, addrs in m.components:
        keys = {a.key for a in addrs}
        if len(keys) != 1:
            continue
        nid = topo_model.node_id(next(iter(keys)))
        if nid in plan.nodes:
            per[nid].append((text, addrs))
    folded = 0
    for nid, nets in per.items():
        nets.sort(key=lambda x: _net_order(x[0]))
        for text, addrs in nets[:NETS_PER_NODE]:
            iface = addrs[0].interface
            node = plan.node(
                Node(
                    id=f"lan:{nid}:{text}",
                    label=text,
                    kind="net",
                    layer="nets",
                    sub=", ".join(dict.fromkeys(a.zone for a in addrs if a.zone))
                    or _kind_text("net"),
                    read=False,
                    level=_L3_LEVEL["net"],
                    rank=_L3_RANK["net"],
                    link={"view": "objekte", "params": {"tab": "subnets", "q": text}},
                )
            )
            plan.edge(
                Edge(
                    id=f"att:{node.id}",
                    a=nid,
                    b=node.id,
                    kind="attached",
                    end_a=f"{iface or '?'} {addrs[0].ip}",
                    tone="muted",
                    layer="nets",
                )
            )
        rest = nets[NETS_PER_NODE:]
        if rest:
            folded += len(rest)
            node = plan.node(
                Node(
                    id=f"more:{nid}",
                    label=L(f"+{len(rest)} Netze", f"+{len(rest)} networks"),
                    kind="more",
                    layer="nets",
                    read=False,
                    level=_L3_LEVEL["more"],
                    rank=_L3_RANK["more"],
                    sub=L("nicht einzeln gezeichnet", "not drawn one by one"),
                    info=[t for t, _a in rest[:30]]
                    + ([f"… +{len(rest) - 30}"] if len(rest) > 30 else []),
                    link={"view": "routing", "params": {"tab": "routes", "context": nid}},
                )
            )
            plan.edge(
                Edge(
                    id=f"att:{node.id}",
                    a=nid,
                    b=node.id,
                    kind="attached",
                    tone="muted",
                    dash=True,
                    layer="nets",
                )
            )
    plan.limits["nets_per_node"] = NETS_PER_NODE
    plan.limits["nets_folded"] = folded
    if folded:
        plan.notes.append(
            L(
                f"Je Knoten höchstens {NETS_PER_NODE} Netze einzeln, {folded} weitere "
                "zusammengefasst („+n Netze“).",
                f"At most {NETS_PER_NODE} networks per node one by one, {folded} more folded "
                "(“+n networks”).",
            )
        )


def _net_order(text: str) -> tuple:
    try:
        net = ipaddress.ip_network(text, strict=False)
        return (net.version, int(net.network_address), net.prefixlen)
    except ValueError:
        return (9, 0, 0)


def _l3_static(
    plan: Plan,
    session: Session,
    tenant_id: int,
    m: topo_model.Model,
    by_pair: dict[frozenset, Edge],
) -> None:
    """Statische Routen (konfiguriert, wirksam) je Kante: an der Linie zum Next-Hop (gelesener
    Knoten bzw. Router), sonst eine eigene Linie; Next-Hop ohne Element gezaehlt."""
    owners = m.g.owners()
    count: Counter = Counter()
    samples: dict[tuple, list[str]] = defaultdict(list)
    lost = 0
    for r in session.scalars(
        select(TopoRoute).where(TopoRoute.tenant_id == tenant_id, TopoRoute.table == "static")
    ):
        key = (r.source_system_id, r.context)
        if key not in m.g.nodes:
            continue
        src = topo_model.node_id(key)
        for h in r.next_hops or []:
            ip = str(h.get("ip") or "").split("%")[0]
            targets = [topo_model.node_id(o) for o, _i, _z in owners.get(ip, []) if o != key]
            if not targets and f"router:{ip}" in plan.nodes:
                targets = [f"router:{ip}"]
            if not targets and ip:
                targets = [
                    x
                    for x in plan.nodes
                    if x.startswith("exit:") and ip in (m.nodes.get(x, {}).get("next_hops") or [])
                ]
            if not targets:
                if ip:
                    lost += 1
                continue  # ueber ein Interface (Tunnel): an der IPsec-Linie
            for t in targets:
                count[(src, t)] += 1
                if len(samples[(src, t)]) < 3:
                    samples[(src, t)].append(r.prefix)
    for (src, dst), n in sorted(count.items()):
        names = (plan.nodes[src].label, plan.nodes[dst].label)
        text = L(f"{n} statisch {names[0]} → {names[1]}", f"{n} static {names[0]} → {names[1]}")
        info = L("z. B. ", "e.g. ") + ", ".join(samples[(src, dst)])
        edge = by_pair.get(frozenset((src, dst)))
        if edge is not None:
            edge.texts.append({"text": text, "layer": "static"})
            edge.info.append(f"{text} ({info})")
            continue
        plan.edge(
            Edge(
                id=f"static:{src}~{dst}",
                a=src,
                b=dst,
                kind="static",
                label=L(f"{n} statisch", f"{n} static"),
                tone="static",
                dash=True,
                layer="static",
                info=[f"{text} ({info})"],
                link={"view": "statisch", "params": {"context": src}},
            )
        )
    plan.limits["static_unplaced"] = lost
    if lost:
        plan.notes.append(
            L(
                f"{lost} statische Routen mit Next-Hop ohne Element im Plan "
                "(Liste: Statische Routen).",
                f"{lost} static routes with a next hop without an element in the "
                "plan (list: static routes).",
            )
        )


_SAFE = re.compile(r"[^A-Za-z0-9.:_-]")


def _l3_ipsec(plan: Plan, session: Session, tenant_id: int) -> None:
    """IPsec-Tunnel als eigene Linie: zur gelesenen Gegenstelle (je Paar einmal), sonst zur
    Adresse der Gegenstelle bzw. „Dial-up“ als eigenes Element."""
    listing = vpn.listing(session, tenant_id)
    seen: dict[frozenset, Edge] = {}
    missing = 0
    for t in listing["tunnels"]:
        a = f"{t['source_system_id']}|{t['context']}"
        if a not in plan.nodes:
            missing += 1
            continue
        peers = list(t.get("peers") or [])
        extra = t.get("peer")  # Kopplung beider Enden (gekoppelt bzw. von Hand), falls geliefert
        if isinstance(extra, dict) and extra.get("source_system_id") is not None:
            peers.append(extra)
        targets = []
        for p in peers:
            b = f"{p['source_system_id']}|{p.get('context') or 'root'}"
            if b in plan.nodes and b != a:
                targets.append((b, p.get("tunnel")))
        up = t.get("status") == "up"
        state = vpn.status_text(t.get("status"))
        info = [
            f"{plan.nodes[a].label}: {t['name']} ({state})",
            L(f"Gegenstelle {t.get('remote_gw') or '–'}", f"Peer {t.get('remote_gw') or '–'}"),
        ]
        link = {"view": "vpn", "params": {"tunnel": t["ref"]}}
        if not targets:
            gw = t.get("remote_gw")
            if t.get("kind") == "dialup":
                pid = f"vpn:{a}:{t['name']}"
                label = L(
                    f"Dial-up ({t.get('instances') or 0} verbunden)",
                    f"Dial-up ({t.get('instances') or 0} connected)",
                )
            else:
                pid = f"vpn:{_SAFE.sub('_', gw or t['name'])}"
                label = gw or t["name"]
            plan.node(
                Node(
                    id=pid,
                    label=label,
                    kind="vpn_peer",
                    read=False,
                    layer="ipsec",
                    sub=L("Gegenstelle nicht gelesen", "Peer not read"),
                    level=_L3_LEVEL["vpn_peer"],
                    rank=_L3_RANK["vpn_peer"],
                    link=link,
                )
            )
            targets = [(pid, None)]
        for b, their in targets:
            pair = frozenset((a, b))
            if pair in seen:
                e = seen[pair]
                e.info += info
                if their is None and t["name"] not in e.label:
                    e.label += f" / {t['name']}"
                if not up:
                    e.tone, e.dash = "bad", True
                continue
            e = plan.edge(
                Edge(
                    id=f"ipsec:{t['ref']}",
                    a=a,
                    b=b,
                    kind="ipsec",
                    label=f"IPsec {t['name']}"
                    + (f" / {their}" if their and their != t["name"] else ""),
                    end_a=t.get("interface"),
                    tone="vpn" if up else "bad",
                    dash=not up,
                    layer="ipsec",
                    info=info,
                    link=link,
                )
            )
            if e is not None:
                seen[pair] = e
    plan.limits["ipsec_unplaced"] = missing
    if missing:
        plan.notes.append(
            L(
                f"{missing} IPsec-Tunnel auf Kontexten ohne gelesenes Routing.",
                f"{missing} IPsec tunnels on contexts without routing read.",
            )
        )


# -- Einstieg -------------------------------------------------------------------------------


def site_choices(session: Session, tenant_id: int) -> tuple[dict[int, Site], list[dict]]:
    sites = {s.id: s for s in session.scalars(select(Site).where(Site.tenant_id == tenant_id))}
    rows = sorted(sites.values(), key=_site_order)
    return sites, [site_store.site_out(s) for s in rows]


def build(session: Session, tenant_id: int, layer: str, site: int | None = None) -> dict:
    """Plan einer Schicht; `site` = Standort (None alle, `NO_SITE` ohne Standort)."""
    plan = {"l1": l1, "l2": l2, "l3": l3}[layer](session, tenant_id)
    sites, choices = site_choices(session, tenant_id)
    out = plan.out(site, sites)
    out["sites"] = choices
    return out
