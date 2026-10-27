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

**ACI-Contracts (0.28.0):** fuer externe EPGs entscheidet die Zuordnung im ACI
(`store/aci_relevance.py`); `for_ip` liefert sie fuer Contracts mit.

**Gegenstelle any (0.25.0):** `behind` sagt, was hinter dem Interface bzw. der Zone der Regel auf
der Seite der Gegenstelle liegt - eine Anbindung (Internet, MPLS …, `topo_exit`), sonst die
Default-Route ueber dieses Interface (extern) bzw. die Netze dahinter (angeschlossen und
geroutet). Aus `any` wird so z. B. „any → Internet Provider (wan)“.
"""

import ipaddress
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Epg,
    InvInterface,
    Rule,
    RuleDomain,
    RuleEndpoint,
    RuleIpRange,
    TopoRoute,
    Zone,
)
from app.store import aci_relevance
from app.store import nat as nat_store
from app.store.paths import Graph, Key, _exit_label
from app.store.topology import Table, ip_hex, table_of

SHOWN_NETS = 3  # Netze hinter einem Interface in der Anzeige

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


def _net_key(prefix: str) -> tuple:
    try:
        n = ipaddress.ip_network(prefix, strict=False)
    except ValueError:
        return (9, 0, 0)
    return (n.version, int(n.network_address), n.prefixlen)


class Relevance:
    """Prueft Regeln gegen das Routing ihrer Firewall; Graph und Routen je Mandant einmal."""

    def __init__(self, session: Session, tenant_id: int):
        self.session = session
        self.tenant_id = tenant_id
        self._graph: Graph | None = None
        self._known: dict[int, set[str]] = {}
        self._cache: dict[tuple, tuple[str, str]] = {}
        self._zones: dict[int, dict[str, list[str]]] = {}
        self._rib: dict[Key, list[tuple[str, int, set[str]]]] = {}
        self._behind: dict[tuple, dict | None] = {}
        self._balancers: nat_store.Balancers | None = None
        self._balanced: set[int] | None = None

    def through_balancer(self, rule, values: list[str]) -> list[str]:
        """0.79.0: erreicht die Regel ein Ziel ueber einen Load Balancer (NSX ALB; VIP der
        FortiGate bzw. Ziel = VIP des Load Balancers), zaehlt die Route zu dessen VIP - nicht
        die zum Server dahinter."""
        if self._balanced is None:
            stmt = select(RuleIpRange.rule_id).where(
                RuleIpRange.tenant_id == self.tenant_id,
                RuleIpRange.role == "dst",
                RuleIpRange.via.contains(nat_store.CHAIN.strip()),
            )
            self._balanced = set(self.session.scalars(stmt.distinct()))
        if rule.id not in self._balanced:
            return []
        if self._balancers is None:
            self._balancers = nat_store.Balancers.load(self.session, self.tenant_id)
        return self._balancers.vips_for(values)

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

    def members(self, sid: int, name: str) -> list[str]:
        """Interfaces eines Namens der Regel: das Interface selbst bzw. die einer Zone."""
        if sid not in self._zones:
            zones: dict[str, list[str]] = {}
            for iface, zone in self.session.execute(
                select(InvInterface.name, InvInterface.zone).where(
                    InvInterface.source_system_id == sid
                )
            ):
                zones.setdefault(iface, [iface])
                if zone:
                    zones.setdefault(zone, []).append(iface)
            self._zones[sid] = zones
        return self._zones[sid].get(name, [])

    def _routes(self, key: Key) -> list[tuple[str, int, set[str]]]:
        """Wirksame Routen der Standard-VRF: (Praefix, Laenge, Interfaces der Next-Hops)."""
        if key not in self._rib:
            rows = []
            for r in self.session.scalars(
                select(TopoRoute).where(
                    TopoRoute.source_system_id == key[0],
                    TopoRoute.context == key[1],
                    TopoRoute.table == "rib",
                    TopoRoute.best.is_(True),
                )
            ):
                if table_of(r) != 0:
                    continue
                ifaces = {h.get("interface") for h in r.next_hops or [] if h.get("interface")}
                rows.append((r.prefix, r.prefix_len, ifaces))
            self._rib[key] = rows
        return self._rib[key]

    def behind(self, rule: Rule, side: str, zone: str | None = None) -> dict | None:
        """Was hinter Interface bzw. Zone der Regel auf der Seite `side` liegt: {text, kind}
        mit kind = Art der Anbindung (internet, mpls, …), `default` bzw. `nets`; None ohne
        Interface (bzw. any) oder ohne gelesenes Routing."""
        raw = rule.raw if isinstance(rule.raw, dict) else {}
        names = _names(raw, "srcintf" if side == "src" else "dstintf") or ([zone] if zone else [])
        if not names or any(n.lower() == "any" for n in names):
            return None
        key = self._node(rule)
        if key is None:
            return None
        cache_key = (key, tuple(names))
        if cache_key not in self._behind:
            parts = [p for p in (self._behind_one(key, n) for n in names) if p]
            self._behind[cache_key] = (
                {"text": "; ".join(p["text"] for p in parts), "kind": parts[0]["kind"]}
                if parts
                else None
            )
        return self._behind[cache_key]

    def _behind_one(self, key: Key, name: str) -> dict | None:
        g = self.graph
        ifaces = self.members(key[0], name) or [name]
        for iface in ifaces:
            ex = g.exit_for(key, iface, g.zone(key[0], iface) or name, None)
            if ex is not None:
                return {"text": f"{_exit_label(ex)} ({name})", "kind": ex.kind}
        routes = self._routes(key)
        if any(length == 0 and set(ifaces) & via for _p, length, via in routes):
            return {"text": f"extern (Default-Route über {name})", "kind": "default"}
        nets = sorted(
            {p for p, length, via in routes if length > 0 and set(ifaces) & via}, key=_net_key
        )
        if not nets:
            return None
        more = f" +{len(nets) - SHOWN_NETS}" if len(nets) > SHOWN_NETS else ""
        return {
            "text": f"Netze hinter {name}: {', '.join(nets[:SHOWN_NETS])}{more}",
            "kind": "nets",
        }

    def check(self, rule: Rule, side: str, values: list[str], zone: str | None = None) -> dict:
        """Relevanz der Regel fuer die Werte des Systems auf ihrer Seite `src` bzw. `dst`:
        moeglich, wenn ein Wert passt; unmoeglich nur, wenn keiner passt und alles bekannt ist.
        `zone` = Zone der Regel auf dieser Seite, falls die Rohdaten keine Interfaces nennen."""
        raw = rule.raw if isinstance(rule.raw, dict) else {}
        names = _names(raw, "srcintf" if side == "src" else "dstintf") or ([zone] if zone else [])
        if side == "dst":
            values = [*values, *self.through_balancer(rule, values)]
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


def for_ip(session: Session, tenant_id: int, rule_ids: list[int], ip_text: str) -> dict[int, dict]:
    """Regel-Liste (0.26.0): je Firewall-Regel, deren Quelle bzw. Ziel die IP abdeckt, die
    Relevanz fuer diese IP - {state, text, sides}; je ACI-Contract die Zuordnung zu seinen
    externen EPGs (0.28.0). Regeln ohne Abdeckung fehlen."""
    try:
        ip = ipaddress.ip_address(ip_text.strip())
    except ValueError:
        return {}
    if not rule_ids:
        return {}
    hx = ip_hex(ip)
    sides: dict[int, set[str]] = {}
    for rid, role in session.execute(
        select(RuleIpRange.rule_id, RuleIpRange.role).where(
            RuleIpRange.tenant_id == tenant_id,
            RuleIpRange.rule_id.in_(rule_ids),
            RuleIpRange.family == ip.version,
            RuleIpRange.role.in_(("src", "dst")),
            RuleIpRange.lo <= hx,
            RuleIpRange.hi >= hx,
        )
    ):
        sides.setdefault(rid, set()).add(role)
    if not sides:
        return {}
    rows = session.execute(
        select(
            Rule.id, Rule.source_system_id, Rule.vdom, Rule.raw, Rule.src_zone_id, Rule.dst_zone_id
        ).where(Rule.id.in_(list(sides)), Rule.domain == RuleDomain.FIREWALL)
    ).all()
    zone_ids = {z for r in rows for z in (r.src_zone_id, r.dst_zone_id) if z}
    zones = (
        dict(session.execute(select(Zone.id, Zone.name).where(Zone.id.in_(zone_ids))).all())
        if zone_ids
        else {}
    )
    rel = Relevance(session, tenant_id)
    out: dict[int, dict] = {}
    for r in rows:
        results = {}
        for side in sorted(sides[r.id]):
            zone = zones.get(r.src_zone_id if side == "src" else r.dst_zone_id)
            results[side] = rel.check(r, side, [str(ip)], zone)
        state = summarize([x["state"] for x in results.values()])
        text = next((x["text"] for x in results.values() if x["state"] == state), "")
        out[r.id] = {"state": state, "text": text,
                     "sides": {k: x["state"] for k, x in results.items()}}  # fmt: skip
    return out | _aci_for_ip(session, tenant_id, sides, ip, rel)


def _aci_for_ip(
    session: Session, tenant_id: int, sides: dict[int, set[str]], ip, rel: Relevance
) -> dict[int, dict]:
    """ACI-Contracts, die die IP erfassen: je Seite die Zuordnung ueber Subnetze externer EPGs
    (laengstes Subnetz der VRF, Scope, Fabric) und - fuer EPGs im Fabric - der Scope des
    Contracts (0.45.1): verbindet er die EPG der IP mit keiner Gegenstelle (anderes AP, andere
    VRF bzw. anderer Tenant), ist die Seite nicht moeglich. Sonst bleiben Seiten mit internen
    EPGs ungeprueft (`unknown`) - der Contract ist dann nie ganz ausgeschlossen."""
    rows = session.execute(
        select(RuleEndpoint.rule_id, RuleEndpoint.role, RuleEndpoint.epg_id, Rule.aci_scope)
        .join(Rule, Rule.id == RuleEndpoint.rule_id)
        .where(
            RuleEndpoint.rule_id.in_(list(sides)),
            Rule.domain == RuleDomain.ACI_CONTRACT,
            RuleEndpoint.epg_id.is_not(None),
        )
    ).all()
    if not rows:
        return {}
    epgs = {e.id: e for e in session.scalars(select(Epg).where(Epg.id.in_({r[2] for r in rows})))}
    cls = aci_relevance.Classifier(session, tenant_id, lambda: rel.graph)
    found: dict[int, dict[str, list[tuple[str, str]]]] = defaultdict(lambda: defaultdict(list))
    internal: dict[tuple[int, str], list[Epg]] = defaultdict(list)
    members: dict[tuple[int, str], list[Epg]] = defaultdict(list)
    scopes: dict[int, str | None] = {}
    for rid, role, eid, scope in rows:
        side, epg = getattr(role, "value", role), epgs.get(eid)
        if epg is None:
            continue
        scopes[rid] = scope
        members[(rid, side)].append(epg)
        if side not in sides[rid]:
            continue
        if epg.kind != "external":
            internal[(rid, side)].append(epg)
            continue
        result = cls.check(epg, ip)
        if result is not None:
            found[rid][side].append(result)
    covering, placed = _covering(session, [e for es in internal.values() for e in es], ip, cls)
    for (rid, side), own in internal.items():
        mine = [e for e in own if e.id in covering]
        others = members.get((rid, "dst" if side == "src" else "src"), [])
        if not mine:
            # Konfiguration bzw. gelernter Endpunkt ordnet die IP einer anderen EPG der VRF zu
            # (0.46.0) - der Contract erfasst sie nur ueber das (gemeinsame) Subnetz
            vrfs = {(e.source_system_id, cls.vrf_of(e)) for e in own}
            why = next((t for w, t in placed.items() if w in vrfs), None)
            if why:
                found[rid][side].append((IMPOSSIBLE, why))
            continue
        if not others:
            continue
        reasons = [cls.scope_reason(scopes.get(rid), e, o) for e in mine for o in others]
        if all(reasons):  # keine EPG der IP hat eine Gegenstelle im Scope
            found[rid][side].append((IMPOSSIBLE, reasons[0]))
    out: dict[int, dict] = {}
    for rid, by_side in found.items():
        results = {}
        for side, items in by_side.items():
            if (rid, side) in internal and not any(s == IMPOSSIBLE for s, _t in items):
                continue
            state = summarize([s for s, _t in items])
            results[side] = {"state": state, "text": next(t for s, t in items if s == state)}
        if not results:
            continue
        for side in sorted(sides[rid] - set(results)):
            if (rid, side) in internal:  # z. B. das BD-Subnetz der EPG: nicht geprueft
                results[side] = {"state": UNKNOWN, "text": ""}
        state = summarize([x["state"] for x in results.values()])
        text = next((x["text"] for x in results.values() if x["state"] == state), "")
        out[rid] = {"state": state, "text": text,
                    "sides": {k: x["state"] for k, x in results.items()}}  # fmt: skip
    return out


def _covering(session: Session, epgs: list[Epg], ip, cls=None) -> tuple[set[int], dict[tuple, str]]:
    """EPGs (im Fabric), zu denen die IP gehoert - je VRF Konfiguration vor Gelerntem
    (0.46.0, `epg_locate`): statischer Endpunkt, Portgruppe der VM, gelernter Endpunkt,
    EPG- bzw. BD-Subnetz. Dazu je VRF, wo eine sichere Stufe (nicht nur ein Subnetz) die IP
    hinlegt - als Begruendung fuer Contracts anderer EPGs."""
    from app.store import epg_locate  # spaet: epg_locate -> aci_relevance -> paths
    from app.store import epgs as epg_store

    if not epgs:
        return set(), {}
    loc = epg_locate.Locator(session, epgs[0].tenant_id, cls)
    out: set[int] = set()
    placed: dict[tuple, str] = {}
    for sid, vrf in {(e.source_system_id, loc.cls.vrf_of(e)) for e in epgs}:
        found, how = loc.locate(str(ip), sid, vrf)
        out |= {e.id for e in found}
        if found and how != epg_locate.LABEL[epg_locate.SUBNET]:
            where = " bzw. ".join(epg_store.inventory_label(e) for e in found)
            placed[(sid, vrf)] = f"{ip} gehört nach {how} zur EPG {where}"
    return out, placed
