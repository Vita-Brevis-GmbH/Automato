"""Logische Zonen (0.51.0, ADR 0024): Netze je Zone, Zonenmatrix, Zonenplan und Befunde.

Eine Zone ist durch Selektoren beschrieben (`models/logical_zone.py`); ihre Netze leitet
`customers.nets_of` ab - dieselbe Auswertung wie beim Kunden, dazu die Netze hinter einer
Anbindung. Eine IP bzw. ein Netz gehoert zur Zone mit dem **laengsten Praefix**, das es
enthaelt (eine DMZ 10.1.5.0/24 in der Server-Zone 10.1.0.0/16 geht vor); ein Bereich, der
mehrere Zonen beruehrt, zaehlt fuer jede davon, sein Rest ohne Zone als „nicht zugeordnet“.
Dasselbe Praefix in zwei Zonen ist ein Befund (`zone_overlap`).

- **Zonenmatrix** (`matrix`): je Regel (FortiGate-Policy, ACI-Contract; ohne Zwillinge
  paralleler Firewalls) Quelle und Ziel aus dem Regelindex (`rule_ip_range`) auf die Zonen
  abgebildet; je Paar Quell-/Zielzone die erlaubenden und sperrenden Regeln, dazu `any` und
  „nicht zugeordnet“. Mit der Absicht je Paar (`zone_policy`): eine erlaubende Regel zwischen
  Zonen mit Absicht „gesperrt“ ist ein Befund (`zone_policy_violation`). `any` ist kein
  Verstoss - es steht als eigene Zeile bzw. Spalte da.
- **Zonenplan** (`plan`): je Zone die gelesenen Knoten (Firewall/VDOM, ACI-VRF), an denen sie
  haengt - am Interface bzw. L3Out (Netz des Interfaces), im Fabric (EPG-/BD-Subnetz), ueber
  die Interfaces einer Anbindung bzw. - fuer Zonen ohne eigenes Netz an einem Knoten - ueber
  eine Route zu einem Router ohne gelesenes Geraet (Default-Route ins Internet, MPLS). Zonen am
  selben Knoten sind ueber ihn verbunden (Uebergang). Dazu Netze ohne Zone.

Nur lesend; die Zonen pflegt der Operator in Automato.
"""

import bisect
import ipaddress
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    InvSubnet,
    LogicalZone,
    Rule,
    RuleAction,
    RuleIpRange,
    SourceSystem,
    TopoExit,
    TopoRoute,
    ZonePolicy,
)
from app.store import customers, topo_model
from app.store import epgs as epg_store
from app.store.customers import Net, Selector
from app.store.paths import _CONNECTED, Graph, Key
from app.store.rules import not_twin
from app.store.topology import MGMT, table_from

ANY = "any"  # Quelle bzw. Ziel any
NONE = "none"  # Adressen ohne Zone
SAMPLES = 10  # Beispiele in Vorschau und Plan
PLAN_ROUTES = 32  # Netze je Zone, fuer die der Plan Routen nachschlaegt
UNZONED = 200  # Netze ohne Zone in der Anzeige
BITS = {4: 32, 6: 128}
Cell = int | str  # Zone (id), ANY oder NONE


def selectors_of(z: LogicalZone) -> list[Selector]:
    return [Selector(s.kind, s.value, s.source_system_id, s.exclude) for s in z.selectors]


def load(session: Session, tenant_id: int) -> list[LogicalZone]:
    return list(
        session.scalars(
            select(LogicalZone)
            .where(LogicalZone.tenant_id == tenant_id)
            .order_by(LogicalZone.position, LogicalZone.name, LogicalZone.id)
        )
    )


@dataclass
class ZoneNets:
    id: int
    name: str
    nets: dict[Net, str]  # Netz -> Grund (Selektor)
    epgs: dict[int, str] = field(default_factory=dict)


def derive(session: Session, tenant_id: int, items: list[LogicalZone] | None = None,
           data=None) -> list[ZoneNets]:  # fmt: skip
    """Netze (und EPGs) je Zone aus ihren Selektoren."""
    items = load(session, tenant_id) if items is None else items
    if not items:
        return []
    data = data or customers._Data(session, tenant_id)
    out = []
    for z in items:
        epgs, nets = customers.nets_of(data, selectors_of(z))
        out.append(ZoneNets(z.id, z.name, nets, epgs))
    return out


def _net_key(n: Net) -> tuple:
    return (n.version, int(n.network_address), n.prefixlen)


def _blocks(family: int, lo: int, hi: int) -> list[tuple[int, int]]:
    """Bereich lo..hi als CIDR-Bloecke (ein Netz bleibt ein Block)."""
    cls = ipaddress.IPv4Address if family == 4 else ipaddress.IPv6Address
    return [
        (int(n.network_address), int(n.broadcast_address))
        for n in ipaddress.summarize_address_range(cls(lo), cls(hi))
    ]


def _fills(lo: int, hi: int, spans: list[tuple[int, int]]) -> bool:
    """Decken die Netze den Bereich lo..hi ganz ab?"""
    pos = lo
    for s_lo, s_hi in sorted(spans):
        if s_lo > pos:
            return False
        pos = max(pos, s_hi + 1)
        if pos > hi:
            return True
    return pos > hi


class ZoneIndex:
    """Zonen je IP bzw. Bereich: das laengste Zonen-Netz, das ihn enthaelt, und die
    Zonen-Netze darin (ohne Zone: NONE)."""

    def __init__(self, items: list[ZoneNets]):
        owners: dict[Net, list[int]] = defaultdict(list)
        for z in items:
            for net in z.nets:
                if z.id not in owners[net]:
                    owners[net].append(z.id)
        self.by_len: dict[int, dict[int, dict[int, list[int]]]] = {4: {}, 6: {}}
        spans: dict[int, list[tuple[int, int, int]]] = {4: [], 6: []}
        for net, ids in owners.items():
            fam, plen = net.version, net.prefixlen
            first = int(net.network_address)
            self.by_len[fam].setdefault(plen, {})[first >> (BITS[fam] - plen)] = ids
            spans[fam] += [(first, int(net.broadcast_address), zid) for zid in ids]
        self.lengths = {f: sorted(self.by_len[f], reverse=True) for f in (4, 6)}
        self.spans = {f: sorted(spans[f]) for f in (4, 6)}
        self.starts = {f: [s[0] for s in self.spans[f]] for f in (4, 6)}
        self.shared = {net: ids for net, ids in owners.items() if len(ids) > 1}
        self._cache: dict[tuple[int, int, int], frozenset] = {}

    def covering(self, family: int, lo: int, hi: int) -> list[int]:
        """Zone(n) des laengsten Zonen-Netzes, das lo..hi ganz enthaelt."""
        bits = BITS[family]
        for plen in self.lengths[family]:
            shift = bits - plen
            if lo >> shift == hi >> shift:
                ids = self.by_len[family][plen].get(lo >> shift)
                if ids:
                    return ids
        return []

    def zones_of(self, family: int, lo: int, hi: int) -> frozenset:
        key = (family, lo, hi)
        if key not in self._cache:
            out: set = set()
            spans, starts = self.spans[family], self.starts[family]
            for b_lo, b_hi in _blocks(family, lo, hi):
                inner = []
                i = bisect.bisect_left(starts, b_lo)
                while i < len(spans) and spans[i][0] <= b_hi:
                    s_lo, s_hi, zid = spans[i]
                    if s_hi <= b_hi and (s_lo, s_hi) != (b_lo, b_hi):
                        inner.append((s_lo, s_hi))
                        out.add(zid)
                    i += 1
                if not _fills(b_lo, b_hi, inner):  # Rest: Zone darum herum bzw. keine
                    out.update(self.covering(family, b_lo, b_hi) or [NONE])
            self._cache[key] = frozenset(out)
        return self._cache[key]

    def of_net(self, net: Net) -> frozenset:
        return self.zones_of(net.version, int(net.network_address), int(net.broadcast_address))


# -- Vorschau ---------------------------------------------------------------------------


def preview(session: Session, tenant_id: int, selectors: list[Selector],
            zone_id: int | None = None) -> dict:  # fmt: skip
    """Was die Selektoren erfassen - ohne zu speichern: Netze und EPGs mit Grund, dazu
    gleiche Praefixe in anderen Zonen."""
    data = customers._Data(session, tenant_id)
    epgs, nets = customers.nets_of(data, selectors)
    others = derive(session, tenant_id, [z for z in load(session, tenant_id) if z.id != zone_id],
                    data)  # fmt: skip
    clash = [{"network": str(n), "zone": o.name} for o in others for n in o.nets if n in nets]
    by_id = {e.id: e for e in data.epgs}
    ordered = sorted(nets.items(), key=lambda x: _net_key(x[0]))
    return {
        "counts": {"net": len(nets), "epg": len(epgs)},
        "samples": {
            "net": [{"network": str(n), "via": why} for n, why in ordered[:SAMPLES]],
            "epg": [epg_store.inventory_label(by_id[e]) for e in list(epgs)[:SAMPLES]],
        },
        "overlaps": clash[:SAMPLES],
        "overlaps_total": len(clash),
    }


# -- Zonenmatrix ------------------------------------------------------------------------


@dataclass
class Assignment:
    """Regeln des Mandanten mit den Zonen ihrer Quelle und ihres Ziels."""

    zones: list[LogicalZone]
    nets: list[ZoneNets]
    index: ZoneIndex
    rules: dict[int, tuple[RuleAction, str, int]]  # id -> (Aktion, Domaene, Quellsystem)
    src: dict[int, set] = field(default_factory=lambda: defaultdict(set))
    dst: dict[int, set] = field(default_factory=lambda: defaultdict(set))
    intents: dict[tuple[int, int], ZonePolicy] = field(default_factory=dict)

    def pairs(self, rid: int) -> list[tuple[Cell, Cell]]:
        return [(s, d) for s in self.src.get(rid, ()) for d in self.dst.get(rid, ())]

    def denied(self, rid: int) -> list[tuple[int, int]]:
        """Paare mit Absicht „gesperrt“, die eine erlaubende Regel trotzdem oeffnet."""
        if self.rules[rid][0] is not RuleAction.ALLOW:
            return []
        return [
            (s, d) for s, d in self.pairs(rid)
            if (p := self.intents.get((s, d))) is not None and p.intent == "deny"
        ]  # fmt: skip


def assign(session: Session, tenant_id: int, items: list[LogicalZone] | None = None,
           source_system_id: int | None = None) -> Assignment:  # fmt: skip
    items = load(session, tenant_id) if items is None else items
    nets = derive(session, tenant_id, items)
    a = Assignment(items, nets, ZoneIndex(nets), {})
    a.intents = {
        (p.src_zone_id, p.dst_zone_id): p
        for p in session.scalars(select(ZonePolicy).where(ZonePolicy.tenant_id == tenant_id))
    }
    if not items:
        return a
    stmt = select(Rule.id, Rule.action, Rule.domain, Rule.source_system_id).where(
        Rule.tenant_id == tenant_id, not_twin()
    )
    if source_system_id is not None:
        stmt = stmt.where(Rule.source_system_id == source_system_id)
    a.rules = {
        rid: (action, domain.value, sid) for rid, action, domain, sid in session.execute(stmt)
    }
    for rid, role, fam, lo, hi, is_any in session.execute(
        select(
            RuleIpRange.rule_id,
            RuleIpRange.role,
            RuleIpRange.family,
            RuleIpRange.lo,
            RuleIpRange.hi,
            RuleIpRange.is_any,
        ).where(RuleIpRange.tenant_id == tenant_id, RuleIpRange.role.in_(("src", "dst")))
    ):
        if rid not in a.rules:
            continue
        zones = {ANY} if is_any else a.index.zones_of(fam, int(lo, 16), int(hi, 16))
        (a.src if role == "src" else a.dst)[rid].update(zones)
    return a


def zone_out(z: LogicalZone, nets: ZoneNets | None = None) -> dict:
    return {
        "id": z.id,
        "name": z.name,
        "code": z.code,
        "kind": z.kind,
        "color": z.color,
        "customer_id": z.customer_id,
        "position": z.position,
        "nets": len(nets.nets) if nets else 0,
        "epgs": len(nets.epgs) if nets else 0,
    }


def _intent_out(p: ZonePolicy) -> dict:
    return {
        "src": p.src_zone_id,
        "dst": p.dst_zone_id,
        "intent": p.intent,
        "note": p.note,
        "updated_by": p.updated_by,
        "updated_at": p.updated_at,
    }


def matrix(session: Session, tenant_id: int, source_system_id: int | None = None) -> dict:
    """Zonenmatrix: je Paar Quell-/Zielzone die Regeln (erlaubend, sperrend), Verstoesse gegen
    die Absicht; Zeilen und Spalten sind die Zonen, dazu `any` und `none` wo benutzt."""
    a = assign(session, tenant_id, source_system_id=source_system_id)
    cells: dict[tuple[Cell, Cell], Counter] = defaultdict(Counter)
    without = assigned = violating = 0
    for rid, (action, _domain, _sid) in a.rules.items():
        pairs = a.pairs(rid)
        if not pairs:
            without += 1  # Quelle bzw. Ziel ohne IP-Bereich (z. B. FQDN, ungeloeste Gruppe)
            continue
        assigned += 1
        bad = set(a.denied(rid))
        violating += bool(bad)
        for pair in pairs:
            c = cells[pair]
            c[action.value] += 1
            c["violations"] += pair in bad
    used = {x for pair in cells for x in pair}
    axes: list[Cell] = [z.id for z in a.zones] + [x for x in (ANY, NONE) if x in used]
    keys = set(cells) | set(a.intents)
    out = []
    for s, d in sorted(keys, key=lambda p: (axes.index(p[0]) if p[0] in axes else 99,
                                            axes.index(p[1]) if p[1] in axes else 99)):  # fmt: skip
        c = cells.get((s, d), Counter())
        p = a.intents.get((s, d)) if isinstance(s, int) and isinstance(d, int) else None
        out.append({
            "src": s, "dst": d, "allow": c["allow"], "deny": c["deny"],
            "violations": c["violations"], "intent": p.intent if p else None,
            "note": p.note if p else None,
        })  # fmt: skip
    by_id = {n.id: n for n in a.nets}
    return {
        "zones": [zone_out(z, by_id.get(z.id)) for z in a.zones],
        "axes": axes,
        "cells": out,
        "intents": [_intent_out(p) for p in a.intents.values()],
        "summary": {
            "zones": len(a.zones),
            "rules": len(a.rules),
            "assigned": assigned,
            "without": without,
            "violations": violating,
        },
    }


def parse_cell(value: str) -> Cell:
    v = value.strip().casefold()
    if v in (ANY, NONE):
        return v
    return int(v)  # ValueError -> 422


def cell_rules(session: Session, tenant_id: int, src: Cell, dst: Cell, limit: int = 100,
               offset: int = 0, source_system_id: int | None = None) -> dict:  # fmt: skip
    """Regeln einer Zelle der Matrix (Quellzone -> Zielzone), Verstoesse zuerst."""
    a = assign(session, tenant_id, source_system_id=source_system_id)
    ids = [rid for rid in a.rules if src in a.src.get(rid, ()) and dst in a.dst.get(rid, ())]
    bad = {rid for rid in ids if (src, dst) in set(a.denied(rid))}
    names = dict(
        session.execute(
            select(SourceSystem.id, SourceSystem.name).where(SourceSystem.tenant_id == tenant_id)
        ).all()
    )
    rows = {
        r.id: r
        for r in session.scalars(select(Rule).where(Rule.id.in_(ids)))
    } if ids else {}  # fmt: skip
    ordered = sorted(
        ids,
        key=lambda rid: (rid not in bad, names.get(rows[rid].source_system_id) or "",
                         rows[rid].vdom or "", rows[rid].sequence or 0, rid),
    )  # fmt: skip
    items = []
    for rid in ordered[offset : offset + limit]:
        r = rows[rid]
        raw = r.raw if isinstance(r.raw, dict) else {}
        items.append({
            "id": r.id, "name": r.name, "external_id": r.external_id, "action": r.action.value,
            "domain": r.domain.value, "source": names.get(r.source_system_id),
            "source_system_id": r.source_system_id, "vdom": r.vdom,
            "tenant": raw.get("tenant") if r.domain.value == "aci_contract" else None,
            "violation": rid in bad,
        })  # fmt: skip
    return {"total": len(ids), "violations": len(bad), "items": items}


# -- Befunde ----------------------------------------------------------------------------


def checks(session: Session, tenant_id: int) -> list[dict]:
    """Befunde fuer den Abgleich: gleiches Praefix in mehreren Zonen, erlaubende Regeln
    zwischen Zonen mit Absicht „gesperrt“."""
    items = load(session, tenant_id)
    if not items:
        return []
    a = assign(session, tenant_id, items)
    names = {z.id: z.name for z in items}
    out: list[dict] = []
    for net, ids in sorted(a.index.shared.items(), key=lambda x: _net_key(x[0])):
        out.append({"type": "zone_overlap", "subject": str(net), "network": str(net),
                    "zones": [names[i] for i in ids]})  # fmt: skip
    for rid in a.rules:
        bad = a.denied(rid)
        if bad:
            out.append({
                "type": "zone_policy_violation", "rule_id": rid,
                "pairs": [{"src": names[s], "dst": names[d]} for s, d in bad],
                "notes": [n for s, d in bad if (n := a.intents[(s, d)].note)],
            })  # fmt: skip
    return out


# -- Zonenplan --------------------------------------------------------------------------


class _Rib:
    """Beste Routen der RIB je Knoten im Speicher (ohne Management): laengstes Praefix ohne
    eine Abfrage je Netz."""

    def __init__(self, session: Session, tenant_id: int):
        self.by: dict[tuple, dict[int, dict[int, dict]]] = defaultdict(dict)
        for sid, ctx, fam, prefix, plen, lo, proto, hops, attrs in session.execute(
            select(
                TopoRoute.source_system_id,
                TopoRoute.context,
                TopoRoute.family,
                TopoRoute.prefix,
                TopoRoute.prefix_len,
                TopoRoute.lo,
                TopoRoute.protocol,
                TopoRoute.next_hops,
                TopoRoute.attrs,
            ).where(
                TopoRoute.tenant_id == tenant_id,
                TopoRoute.table == "rib",
                TopoRoute.best.is_(True),
            )
        ):
            table = table_from(attrs)
            if table == MGMT:
                continue
            slot = self.by[((sid, ctx), fam, table)].setdefault(plen, {})
            key = int(lo, 16) >> (BITS[fam] - plen)
            entry = slot.setdefault(key, {"prefix": prefix, "protocol": proto, "next_hops": []})
            for h in hops or []:
                if h not in entry["next_hops"]:
                    entry["next_hops"].append(h)
        self.lengths = {k: sorted(v, reverse=True) for k, v in self.by.items()}
        self.tables: dict[tuple[Key, int], list] = defaultdict(list)
        for key, fam, table in self.by:
            self.tables[(key, fam)].append(table)

    def lookup(self, key: Key, family: int, lo: int, hi: int) -> list[dict]:
        """Wirksame Route je VRF des Knotens, die lo..hi ganz enthaelt."""
        out = []
        for table in self.tables.get((key, family), []):
            slot = (key, family, table)
            for plen in self.lengths[slot]:
                shift = BITS[family] - plen
                if lo >> shift == hi >> shift and (e := self.by[slot][plen].get(lo >> shift)):
                    out.append(e)
                    break
        return out

    def interface_to(self, key: Key, ip: str) -> str | None:
        """Interface des angeschlossenen Netzes, in dem die IP (Next-Hop) liegt."""
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return None
        for e in self.lookup(key, addr.version, int(addr), int(addr)):
            if e["protocol"] in _CONNECTED:
                for h in e["next_hops"]:
                    if h.get("interface"):
                        return h["interface"]
        return None


@dataclass
class _Attach:
    via: set[str] = field(default_factory=set)  # interface | fabric | exit | route
    interfaces: set[str] = field(default_factory=set)
    networks: list[str] = field(default_factory=list)
    routes: list[dict] = field(default_factory=list)
    count: int = 0  # Netze bzw. Subnetze

    def add(self, via: str, interface: str | None, network: str | None) -> None:
        self.via.add(via)
        if interface:
            self.interfaces.add(interface)
        if network and network not in self.networks:
            self.count += 1
            if len(self.networks) < SAMPLES:
                self.networks.append(network)


def plan(session: Session, tenant_id: int) -> dict:
    """Zonenplan: Zonen, die Knoten, an denen sie haengen, und die Uebergaenge zwischen Zonen
    ueber einen Knoten; dazu Netze ohne Zone."""
    items = load(session, tenant_id)
    nets = derive(session, tenant_id, items)
    idx = ZoneIndex(nets)
    g = Graph(session, tenant_id)
    att: dict[tuple[int, Key], _Attach] = defaultdict(_Attach)
    unzoned: dict[str, dict] = {}

    # am Interface bzw. L3Out: das Netz des Interfaces
    addrs, ports = topo_model.addresses(session, tenant_id, g)
    for a in addrs:
        for zid in idx.of_net(a.net):
            if zid == NONE:
                unzoned.setdefault(str(a.net), {"network": str(a.net), "node": g.nodes[a.key].label,
                                                "where": a.interface})  # fmt: skip
            else:
                att[(zid, a.key)].add("interface", a.interface, str(a.net))
    # im Fabric: EPG- bzw. BD-Subnetze der VRF
    for s in session.scalars(
        select(InvSubnet).where(
            InvSubnet.tenant_id == tenant_id, InvSubnet.context.in_(("bd", "epg"))
        )
    ):
        key = (s.source_system_id, s.vrf or "")
        net = customers._net(s.prefix)
        if key not in g.nodes or net is None:
            continue
        where = (
            f"EPG {s.aci_tenant}|{s.ap}|{s.epg}" if s.epg and s.ap else f"BD {s.aci_tenant}|{s.bd}"
        )
        for zid in idx.of_net(net):
            if zid == NONE:
                unzoned.setdefault(str(net), {"network": str(net), "node": g.nodes[key].label,
                                              "where": where})  # fmt: skip
            else:
                att[(zid, key)].add("fabric", None, str(net))
    # ueber die Interfaces einer Anbindung (Selektor `exit`)
    exits = list(session.scalars(select(TopoExit).where(TopoExit.tenant_id == tenant_id)))
    for z in items:
        names = {
            s.value.strip().casefold() for s in z.selectors if s.kind == "exit" and not s.exclude
        }
        for e in exits:
            if (e.name or "").casefold() not in names and f"#{e.id}" not in names:
                continue
            for key in g.nodes:
                if e.source_system_ids and key[0] not in e.source_system_ids:
                    continue
                for port in sorted(set(e.interfaces or []) & ports.get(key, set())):
                    att[(z.id, key)].add("exit", port, None)
    # Zonen ohne Netz an einem Knoten: ueber Routen zu Routern ohne gelesenes Geraet
    direct = {zid for zid, _key in att}
    rib = _Rib(session, tenant_id)
    owners = g.owners()
    for zn in nets:
        if zn.id in direct:
            continue
        sample = sorted(zn.nets, key=_net_key)[:PLAN_ROUTES]
        for key in g.nodes:
            for net in sample:
                lo, hi = int(net.network_address), int(net.broadcast_address)
                for r in rib.lookup(key, net.version, lo, hi):
                    if r["protocol"] in _CONNECTED:
                        continue
                    for h in r["next_hops"]:
                        ip = str(h.get("ip") or "").split("%")[0]
                        if ip and any(o[0] != key for o in owners.get(ip, [])):
                            continue  # ueber einen anderen gelesenen Knoten: die Zone haengt dort
                        iface = h.get("interface") or (rib.interface_to(key, ip) if ip else None)
                        entry = att[(zn.id, key)]
                        entry.add("route", iface, str(net))
                        route = {"prefix": r["prefix"], "next_hop": ip or None, "interface": iface,
                                 "names": g.names(ip) if ip else []}  # fmt: skip
                        if route not in entry.routes and len(entry.routes) < SAMPLES:
                            entry.routes.append(route)

    # Uebergaenge: Zonen am selben Knoten
    by_node: dict[Key, list[int]] = defaultdict(list)
    for zid, key in att:
        by_node[key].append(zid)
    order = {z.id: i for i, z in enumerate(items)}
    links: dict[tuple[int, int], list[str]] = defaultdict(list)
    for key, zids in by_node.items():
        zids.sort(key=lambda z: order.get(z, 0))
        for i, a in enumerate(zids):
            for b in zids[i + 1 :]:
                links[(a, b)].append(topo_model.node_id(key))
    by_zone = {n.id: n for n in nets}
    attachments = [
        {
            "zone": zid,
            "node": topo_model.node_id(key),
            "via": sorted(x.via),
            "interfaces": sorted(x.interfaces),
            "networks": x.networks,
            "count": x.count,
            "routes": x.routes,
            "default": any(r["prefix"].endswith("/0") for r in x.routes),
        }
        for (zid, key), x in sorted(att.items(), key=lambda i: (order.get(i[0][0], 0), i[0][1]))
    ]
    nodes = [
        {"id": topo_model.node_id(key), **{k: v for k, v in g.nodes[key].out().items()
                                          if k in ("kind", "label", "source", "group")},
         "zones": len(by_node[key])}
        for key in sorted(by_node, key=lambda k: g.nodes[k].label)
    ]  # fmt: skip
    rest = sorted(unzoned.values(), key=lambda u: _net_key(ipaddress.ip_network(u["network"])))
    return {
        "zones": [
            {
                **zone_out(z, by_zone.get(z.id)),
                "samples": [str(n) for n in sorted(by_zone[z.id].nets, key=_net_key)[:SAMPLES]],
            }
            for z in items
        ],  # fmt: skip
        "nodes": nodes,
        "attachments": attachments,
        "links": [{"a": a, "b": b, "nodes": n} for (a, b), n in links.items()],
        "unzoned": {"total": len(rest), "items": rest[:UNZONED]},
        "summary": {
            "zones": len(items),
            "nodes": len(nodes),
            "attachments": len(attachments),
            "links": len(links),
            "unzoned": len(rest),
            "without_node": sum(1 for z in items if z.id not in {a["zone"] for a in attachments}),
        },
    }
