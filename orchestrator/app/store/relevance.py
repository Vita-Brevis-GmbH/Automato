"""Relevanz einer Firewall-Regel fuer ein System (0.24.0, ADR 0019 Punkt 6).

Kann die Regel Verkehr von bzw. zu einer IP ueberhaupt sehen? Massgebend ist das Routing der
Firewall (bzw. des VDOMs) der Regel:

- **Ziel:** Die Firewall leitet die IP ueber ein Interface (Route bzw. angeschlossenes Netz,
  ECMP = mehrere). Die Regel kann nur greifen, wenn ihr Ausgang (`dstintf`: Interface, Zone
  oder `any`) dazu passt.
- **Quelle:** Verkehr von der IP kommt ueber das Interface an, ueber das die Firewall die IP
  erreicht (symmetrisches Routing). Die Regel kann nur greifen, wenn ihr Eingang (`srcintf`)
  dazu passt.

Ergebnis je Regel und Seite: `possible`, `impossible` (mit Begruendung) oder `unknown` - z. B.
Routing nicht gelesen, keine Route, Interface unbekannt oder eine Policy-Route bzw. SD-WAN-
Regel, die anders leiten kann. Die Doku blendet `impossible` standardmaessig aus; einblendbar mit
Begruendung. Nur lesend - es zaehlen die gelesenen Routen.
"""

import ipaddress

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import InvInterface, Rule
from app.store.paths import Graph, Key
from app.store.topology import Table

POSSIBLE, IMPOSSIBLE, UNKNOWN = "possible", "impossible", "unknown"


def _names(raw: dict, key: str) -> list[str]:
    return [str(e["name"]) for e in raw.get(key) or [] if isinstance(e, dict) and e.get("name")]


def _ip(value: str | None):
    """Erste Adresse eines Werts (IP, Netz, Bereich a-b)."""
    text = (value or "").split("-")[0].strip()
    try:
        if "/" in text:
            return ipaddress.ip_network(text, strict=False).network_address
        return ipaddress.ip_address(text)
    except ValueError:
        return None


class Relevance:
    """Prueft Regeln gegen das Routing ihrer Firewall; Graph und Routen je Mandant einmal."""

    def __init__(self, session: Session, tenant_id: int):
        self.session = session
        self.tenant_id = tenant_id
        self._graph: Graph | None = None
        self._known: dict[int, set[str]] = {}
        self._cache: dict[tuple, tuple[str, str]] = {}

    @property
    def graph(self) -> Graph:
        if self._graph is None:
            self._graph = Graph(self.session, self.tenant_id)
        return self._graph

    def _node(self, rule: Rule) -> Key | None:
        g = self.graph
        key = (rule.source_system_id, rule.vdom or "")
        if key in g.nodes:
            return key
        mine = [k for k in g.nodes if k[0] == rule.source_system_id]
        return mine[0] if len(mine) == 1 and not rule.vdom else None

    def known(self, sid: int) -> set[str]:
        if sid not in self._known:
            names: set[str] = set()
            for name, zone in self.session.execute(
                select(InvInterface.name, InvInterface.zone).where(
                    InvInterface.source_system_id == sid
                )
            ):
                names |= {n for n in (name, zone) if n}
            self._known[sid] = names
        return self._known[sid]

    def check(self, rule: Rule, side: str, values: list[str], zone: str | None = None) -> dict:
        """Relevanz der Regel fuer die Werte des Systems auf ihrer Seite `src` bzw. `dst`:
        moeglich, wenn ein Wert passt; unmoeglich nur, wenn keiner passt und alles bekannt ist.
        `zone` = Zone der Regel auf dieser Seite, falls die Rohdaten keine Interfaces nennen."""
        raw = rule.raw if isinstance(rule.raw, dict) else {}
        names = _names(raw, "srcintf" if side == "src" else "dstintf") or ([zone] if zone else [])
        results = [self._one(rule, side, names, v) for v in dict.fromkeys(values)]
        results = [r for r in results if r is not None]
        if not results:
            return {"state": UNKNOWN, "text": "keine IP zum Prüfen"}
        for state in (POSSIBLE, UNKNOWN):
            hit = next((r for r in results if r[0] == state), None)
            if hit is not None:
                return {"state": state, "text": hit[1]}
        return {"state": IMPOSSIBLE, "text": results[0][1]}

    def _one(self, rule: Rule, side: str, names: list[str], value: str):
        ip = _ip(value)
        if ip is None:
            return None
        if not names or any(n.lower() == "any" for n in names):
            return POSSIBLE, ""  # ohne Interface bzw. any: jede Richtung
        key = self._node(rule)
        if key is None:
            return UNKNOWN, "Routing der Firewall nicht gelesen"
        cache_key = (key, side, tuple(names), str(ip))
        if cache_key not in self._cache:
            self._cache[cache_key] = self._decide(key, side, names, ip)
        return self._cache[cache_key]

    def _decide(self, key: Key, side: str, names: list[str], ip) -> tuple[str, str]:
        g = self.graph
        node = g.nodes[key]
        # VRF: die des Interfaces der Regel (Zonen und unbekannte Namen = Standard-VRF)
        table: Table = next((t for t in (g.table(key, n) for n in names) if t != 0), 0)
        route = g.route(key, ip, table)
        if route is None:
            return UNKNOWN, f"keine Route zu {ip} auf {node.label}"
        ports: dict[str, str | None] = {}
        for h in route["next_hops"] or []:
            iface = h.get("interface")
            if not iface and h.get("ip"):
                iface = g.interface_to(key, h["ip"], table)
            if iface:
                ports[iface] = g.zone(key[0], iface)
        if not ports:
            return UNKNOWN, f"Interface der Route zu {ip} auf {node.label} unbekannt"
        have = set(ports) | {z for z in ports.values() if z}
        if have & set(names):
            return POSSIBLE, ""
        if side == "dst":
            policies = [
                p for p in g.policies(key, None, ip, None) if p["output_device"] or p["gateway"]
            ]
            if any(set(p["output_device"]) & set(names) for p in policies):
                return POSSIBLE, ""
            if policies:
                return UNKNOWN, f"Policy-Route bzw. SD-WAN auf {node.label} kann {ip} anders leiten"
        if not all(n in self.known(key[0]) for n in names):
            return UNKNOWN, f"Interface bzw. Zone {', '.join(names)} auf {node.label} unbekannt"
        via = ", ".join(f"{i} (Zone {z})" if z else i for i, z in ports.items())
        rule_side = ", ".join(names)
        if side == "dst":
            text = f"{node.label} leitet {ip} über {via} – die Regel gilt nur für den Ausgang"
        else:
            text = f"Verkehr von {ip} kommt bei {node.label} über {via} an – die Regel gilt nur für"
            text += " den Eingang"
        return IMPOSSIBLE, f"{text} {rule_side}"


def summarize(states: list[str]) -> str:
    """Mehrere Treffer einer Regel: moeglich vor unbekannt vor unmoeglich."""
    for state in (POSSIBLE, UNKNOWN):
        if state in states:
            return state
    return IMPOSSIBLE if states else UNKNOWN
