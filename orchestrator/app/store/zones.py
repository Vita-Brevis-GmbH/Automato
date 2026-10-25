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
- **Zonenplan** (`plan`, seit 0.56.0 nach dem Zonenmodell, `zone_struct`): Zonen mit ihren
  Knoten (Firewall/VDOM, ACI-VRF) und Netzen, die Uebergaenge zwischen den Zonen (L3Outs,
  Interfaces, Router ohne gelesenes Geraet), je Kunde bzw. als Uebersicht mit den Kunden als je
  eine Zone. Dazu Knoten und Netze ohne Zone.
- **Farben** (`colors`, 0.56.0): aus dem Grundschema, sonst die der Zone, sonst die der Art.

Nur lesend; die Zonen pflegt der Operator in Automato.
"""

import bisect
import ipaddress
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Customer,
    LogicalZone,
    Rule,
    RuleAction,
    RuleIpRange,
    SourceSystem,
    ZonePolicy,
)
from app.store import customers, topo_model, zone_services
from app.store import epgs as epg_store
from app.store.customers import Net, Selector
from app.store.rules import not_twin

ANY = "any"  # Quelle bzw. Ziel any
NONE = "none"  # Adressen ohne Zone
SAMPLES = 10  # Beispiele in Vorschau und Plan
UNZONED = 200  # Netze ohne Zone in der Anzeige
BITS = {4: 32, 6: 128}
Cell = int | str  # Zone (id), ANY oder NONE
OTHER = "other"  # Zonen anderer Kunden in der Sicht eines Kunden-Benutzers (0.53.0)


@dataclass
class CustomerView:
    """Sicht eines Kunden-Benutzers (0.53.0, ADR 0018 Schritt 4): seine Zonen mit Namen, die
    geteilten (ohne Kunde) mit Namen, Zonen anderer Kunden nur als „anderer Kunde“; nur Zellen
    mit einer eigenen Zone."""

    own: set[int]
    shared: set[int]

    def mask(self, c: Cell) -> Cell:
        if not isinstance(c, int) or c in self.own or c in self.shared:
            return c
        return OTHER

    def pairs(self, pairs) -> set[tuple[Cell, Cell]]:
        return {(self.mask(s), self.mask(d)) for s, d in pairs if s in self.own or d in self.own}


def customer_view(items: list[LogicalZone], customer_ids) -> CustomerView:
    ids = set(customer_ids or ())
    return CustomerView(own={z.id for z in items if z.customer_id in ids},
                        shared={z.id for z in items if z.customer_id is None})  # fmt: skip


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
           data=None, struct=None) -> list[ZoneNets]:  # fmt: skip
    """Netze (und EPGs) je Zone aus ihren Selektoren. Mit Knoten-Selektoren (0.56.0: VDOM,
    ACI-Tenant/VRF) aus der Zonenstruktur: die ausdruecklich zugeordneten Netze und die Netze
    ihrer Knoten in der Zone, ohne Netze von Uebergaengen (`zone_struct`)."""
    from app.store import zone_struct

    items = load(session, tenant_id) if items is None else items
    if not items:
        return []
    data = data or customers._Data(session, tenant_id)
    if struct is None and zone_struct.has_nodes(items):
        struct = zone_struct.build(session, tenant_id, items, data)
    out = []
    for z in items:
        if struct is not None:
            epgs, nets = struct.nets(z.id)
        else:
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
    # Absicht je Dienst (0.57.0): erlaubte Dienste der eingeschraenkten Paare, Dienste der Regeln
    services: dict[tuple[int, int], "zone_services.Allowed"] = field(default_factory=dict)
    rule_services: "zone_services.RuleServices | None" = None

    def pairs(self, rid: int) -> list[tuple[Cell, Cell]]:
        return [(s, d) for s in self.src.get(rid, ()) for d in self.dst.get(rid, ())]

    def outside(self, rid: int, pair: tuple) -> list[str] | None:
        """Dienste der Regel ausserhalb der Absicht „eingeschraenkt“ des Paars (None = keine
        Dienste vorgegeben bzw. nicht beurteilbar)."""
        allow = self.services.get(pair)
        if allow is None or self.rule_services is None:
            return None
        return self.rule_services.outside(rid, allow)

    def denied(self, rid: int) -> list[tuple[int, int]]:
        """Paare, gegen deren Absicht eine erlaubende Regel verstoesst: „gesperrt“ bzw.
        „eingeschraenkt“ mit Diensten, die die Regel ueberschreitet (0.57.0)."""
        if self.rules[rid][0] is not RuleAction.ALLOW:
            return []
        out = []
        for s, d in self.pairs(rid):
            p = self.intents.get((s, d))
            if p is None:
                continue
            if p.intent == "deny" or (p.intent == "limited" and self.outside(rid, (s, d))):
                out.append((s, d))
        return out


def assign(session: Session, tenant_id: int, items: list[LogicalZone] | None = None,
           source_system_id: int | None = None,
           nets: list[ZoneNets] | None = None) -> Assignment:  # fmt: skip
    items = load(session, tenant_id) if items is None else items
    nets = derive(session, tenant_id, items) if nets is None else nets
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
    # Absicht je Dienst: nur die Regeln laden, die ein eingeschraenktes Paar mit Diensten oeffnen
    a.services = {
        k: zone_services.allowed(session, tenant_id, p.services)
        for k, p in a.intents.items() if p.intent == "limited" and p.services
    }  # fmt: skip
    if a.services:
        ids = [
            rid for rid, (action, _d, _s) in a.rules.items()
            if action is RuleAction.ALLOW and any(k in a.services for k in a.pairs(rid))
        ]  # fmt: skip
        a.rule_services = zone_services.rule_services(session, ids)
    return a


def zone_out(z: LogicalZone, nets: ZoneNets | None = None, color: dict | None = None) -> dict:
    """Zone mit Zahlen; `color` aus `colors` (Grundschema, Zone bzw. Art)."""
    return {
        "id": z.id,
        "name": z.name,
        "code": z.code,
        "kind": z.kind,
        **(color or {"color": z.color, "color_source": "zone", "schema": None}),
        "own_color": z.color,
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
        "services": list(p.services or []),
        "updated_by": p.updated_by,
        "updated_at": p.updated_at,
    }


def matrix(session: Session, tenant_id: int, source_system_id: int | None = None,
           customer_ids=None) -> dict:  # fmt: skip
    """Zonenmatrix: je Paar Quell-/Zielzone die Regeln (erlaubend, sperrend), Verstoesse gegen
    die Absicht; Zeilen und Spalten sind die Zonen, dazu `any` und `none` wo benutzt. Mit
    `customer_ids` die Sicht eines Kunden-Benutzers (`CustomerView`)."""
    a = assign(session, tenant_id, source_system_id=source_system_id)
    view = customer_view(a.zones, customer_ids) if customer_ids is not None else None
    cells: dict[tuple[Cell, Cell], Counter] = defaultdict(Counter)
    without = assigned = violating = 0
    for rid, (action, _domain, _sid) in a.rules.items():
        pairs = a.pairs(rid)
        if view is not None:
            bad = view.pairs(a.denied(rid))
            pairs = view.pairs(pairs)
            if not pairs:
                continue  # beruehrt keine Zone des Kunden
        else:
            bad = set(a.denied(rid))
            if not pairs:
                without += 1  # Quelle bzw. Ziel ohne IP-Bereich (z. B. FQDN, ungeloeste Gruppe)
                continue
        assigned += 1
        violating += bool(bad)
        for pair in pairs:
            c = cells[pair]
            c[action.value] += 1
            c["violations"] += pair in bad
    intents = a.intents
    zones = a.zones
    if view is not None:
        visible = view.own | view.shared
        intents = {k: p for k, p in a.intents.items()
                   if set(k) <= visible and set(k) & view.own}  # fmt: skip
        used_ids = {x for pair in [*cells, *intents] for x in pair if isinstance(x, int)}
        zones = [z for z in a.zones if z.id in view.own or z.id in used_ids]
    used = {x for pair in cells for x in pair}
    axes: list[Cell] = [z.id for z in zones] + [x for x in (OTHER, ANY, NONE) if x in used]
    keys = set(cells) | set(intents)
    out = []
    for s, d in sorted(keys, key=lambda p: (axes.index(p[0]) if p[0] in axes else 99,
                                            axes.index(p[1]) if p[1] in axes else 99)):  # fmt: skip
        c = cells.get((s, d), Counter())
        p = intents.get((s, d)) if isinstance(s, int) and isinstance(d, int) else None
        out.append({
            "src": s, "dst": d, "allow": c["allow"], "deny": c["deny"],
            "violations": c["violations"], "intent": p.intent if p else None,
            "note": p.note if p else None, "services": list(p.services or []) if p else [],
        })  # fmt: skip
    by_id = {n.id: n for n in a.nets}
    color = colors(session, tenant_id, a.zones)
    if view is not None:  # Netze geteilter Zonen gehoeren nicht zur Sicht des Kunden
        by_id = {k: v for k, v in by_id.items() if k in view.own}
    return {
        "zones": [zone_out(z, by_id.get(z.id), color.get(z.id)) for z in zones],
        "axes": axes,
        "cells": out,
        "intents": [_intent_out(p) for p in intents.values()],
        "summary": {
            "zones": len(zones),
            "rules": len(a.rules) if view is None else assigned,
            "assigned": assigned,
            "without": without,
            "violations": violating,
        },
    }


def parse_cell(value: str) -> Cell:
    v = value.strip().casefold()
    if v in (ANY, NONE, OTHER):
        return v
    return int(v)  # ValueError -> 422


def cell_rules(session: Session, tenant_id: int, src: Cell, dst: Cell, limit: int = 100,
               offset: int = 0, source_system_id: int | None = None,
               customer_ids=None) -> dict:  # fmt: skip
    """Regeln einer Zelle der Matrix (Quellzone -> Zielzone), Verstoesse zuerst. Fuer
    Kunden-Benutzer nur Zellen mit einer eigenen Zone (`anderer Kunde` = OTHER)."""
    a = assign(session, tenant_id, source_system_id=source_system_id)
    if customer_ids is not None:
        view = customer_view(a.zones, customer_ids)
        ids = [rid for rid in a.rules if (src, dst) in view.pairs(a.pairs(rid))]
        bad = {rid for rid in ids if (src, dst) in view.pairs(a.denied(rid))}
    else:
        ids = [rid for rid in a.rules if src in a.src.get(rid, ()) and dst in a.dst.get(rid, ())]
        bad = {rid for rid in ids if (src, dst) in set(a.denied(rid))}
    pair = (src, dst) if isinstance(src, int) and isinstance(dst, int) else None
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
            # Dienste ausserhalb der Absicht „eingeschraenkt“ (0.57.0)
            "outside": (a.outside(rid, pair) or []) if rid in bad and pair else [],
        })  # fmt: skip
    return {"total": len(ids), "violations": len(bad), "items": items}


# -- Befunde ----------------------------------------------------------------------------


def checks(session: Session, tenant_id: int) -> list[dict]:
    """Befunde fuer den Abgleich: gleiches Praefix in mehreren Zonen, erlaubende Regeln
    zwischen Zonen mit Absicht „gesperrt“ bzw. mit Diensten ausserhalb von „eingeschraenkt“."""
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
            pairs = []
            for s, d in bad:
                p = a.intents[(s, d)]
                item = {"src": names[s], "dst": names[d], "intent": p.intent}
                if p.intent == "limited":
                    item |= {"services": list(p.services or []),
                             "outside": a.outside(rid, (s, d)) or []}  # fmt: skip
                pairs.append(item)
            out.append({
                "type": "zone_policy_violation", "rule_id": rid, "pairs": pairs,
                "notes": [n for s, d in bad if (n := a.intents[(s, d)].note)],
            })  # fmt: skip
    return out


# -- Farben (0.56.0) ---------------------------------------------------------------------

# Feste Farbe je Art (Ebene), wenn weder das Grundschema noch die Zone eine nennt
KIND_COLOR = {
    "dc": "#3b82c4",
    "edge": "#d9822b",
    "transfer": "#8a93a3",
    "core": "#2f9e44",
    "distribution": "#1098ad",
    "access": "#74b816",
    "external": "#cc3333",
    "internal": "#3b82c4",
    "dmz": "#d9822b",
    "management": "#7a5cc4",
    "shared": "#2f9e8f",
}


def colors(session: Session, tenant_id: int, items: list[LogicalZone]) -> dict[int, dict]:
    """Farbe je Zone: aus dem Grundschema (gleiche Zone dort; ohne Grundschema aus der
    eingebauten Vorlage), sonst die eigene, sonst die der Art - so ist dieselbe Zone ueberall
    gleich gefaerbt."""
    from app.store import zone_schema

    schema, _profile = zone_schema.for_tenant(session, tenant_id)
    source = "schema" if schema is not None else "template"
    schema = schema or zone_schema.default_schema()
    out = {}
    for z in items:
        sz = schema.match(z.name, z.code)
        if sz is not None and sz.color:
            out[z.id] = {"color": sz.color, "color_source": source, "schema": sz.name}
        elif z.color:
            out[z.id] = {"color": z.color, "color_source": "zone",
                         "schema": sz.name if sz else None}  # fmt: skip
        else:
            out[z.id] = {"color": KIND_COLOR.get(z.kind, "#8a93a3"), "color_source": "kind",
                         "schema": sz.name if sz else None}  # fmt: skip
    return out


# -- Zonenplan (0.56.0) ------------------------------------------------------------------

PSEUDO = {
    "none": ("Knoten ohne Zone", "none"),
    "outside": ("ausserhalb (nicht gelesen)", "outside"),
}


# Ebenen von links nach rechts (Zonenplan, Namen der Uebergaenge): DC, Edge, Transfer, Core,
# Distribution, Access, Public; Kunden als eine Zone wie das DC, ausserhalb ganz rechts
LAYER = {"dc": 0, "internal": 0, "shared": 0, "customer": 0, "edge": 1, "dmz": 1,
         "management": 1, "none": 1, "transfer": 2, "core": 3, "distribution": 4, "access": 5,
         "external": 6, "outside": 6}  # fmt: skip


def _layer_key(z: dict) -> tuple:
    return (LAYER.get(z.get("kind") or "", 0), z.get("position") or 0, z["name"].casefold())


def transition_name(a: dict, b: dict) -> str:
    """„DC-EINET“: Kurz- bzw. Namen der beiden Seiten, nach Ebene geordnet (DC vor Edge vor
    Public) - von beiden Seiten aus gleich."""
    x, y = sorted((a, b), key=_layer_key)
    return f"{x.get('code') or x['name']}-{y.get('code') or y['name']}"


def plan(session: Session, tenant_id: int, customer_id=None,
         everything: bool = False) -> dict:  # fmt: skip
    """Zonenplan nach dem Zonenmodell: Zonen mit ihren Knoten (Firewall/VDOM, ACI-VRF), Netze in
    der Zone und die Uebergaenge zwischen den Zonen (`zone_struct`).

    Ansicht (`view`): ohne Kunde die Infrastruktur - Zonen der Kunden je Kunde als eine Zone
    zusammengefasst; mit Kunde dessen Zonen, die geteilten Zonen, die von ihnen aus erreichbar
    sind, und die Kunden, zu denen seine Zonen einen Uebergang haben, je als eine Zone;
    `everything` = alle Zonen einzeln. `customer_id` ist eine ID oder (Kunden-Benutzer) mehrere."""
    from app.store import zone_struct

    if isinstance(customer_id, set | frozenset | list | tuple):
        mine = set(customer_id)
    else:
        mine = set() if customer_id is None else {customer_id}

    items = load(session, tenant_id)
    data = customers._Data(session, tenant_id)
    st = zone_struct.build(session, tenant_id, items, data)
    by_zone = {n.id: n for n in derive(session, tenant_id, items, data, st)}
    color = colors(session, tenant_id, items)
    cust = dict(session.execute(
        select(Customer.id, Customer.name).where(Customer.tenant_id == tenant_id)
    ).all())  # fmt: skip
    g = st.graph
    zone_nodes = st.zone_nodes()
    zmap = {z.id: z for z in items}
    zones_full = {
        z.id: {
            **zone_out(z, by_zone.get(z.id), color.get(z.id)),
            "customer": cust.get(z.customer_id) if z.customer_id else None,
            "nodes": [topo_model.node_id(k) for k in zone_nodes.get(z.id, [])],
            "internal": len(st.internal.get(z.id, {})),
            "samples": [str(n) for n in sorted(by_zone[z.id].nets, key=_net_key)[:SAMPLES]],
        }
        for z in items
    }  # fmt: skip
    transitions = list(st.transitions.values())

    # Ansicht: welche Zone erscheint wie
    owners = {z.id: z.customer_id for z in items}
    mode = "all" if everything or not any(owners.values()) else (
        "customer" if mine else "overview")  # fmt: skip
    group: dict = {}
    own: set = set()
    need: set = set()
    if mode == "all":
        group = {z.id: z.id for z in items}
    elif mode == "overview":
        group = {zid: (f"customer:{c}" if c else zid) for zid, c in owners.items()}
    else:
        own, need, group = _customer_view(session, tenant_id, items, st, transitions, by_zone,
                                          owners, mine)  # fmt: skip
        for z in zones_full.values():  # geteilte Zonen nur mit den benoetigten Knoten
            if z["id"] not in own:
                z["nodes_total"] = len(z["nodes"])
                z["nodes"] = [n for n in z["nodes"] if n in need]
    for pseudo in PSEUDO:
        group[pseudo] = pseudo

    merged: dict[tuple, dict] = {}
    node_zone = {topo_model.node_id(k): zid for k, zid in st.node_zone.items()}
    for t in transitions:
        ga, gb = group.get(t.a), group.get(t.b)
        if ga is None or gb is None or ga == gb:
            continue
        nets = t.networks
        if mode == "customer":  # nur Netze an benoetigten Knoten
            nets = [n for n in nets if _needed_net(n, (t.a, t.b), node_zone, need)]
            if not nets:
                continue
        k = tuple(sorted((ga, gb), key=lambda x: (isinstance(x, str), str(x))))
        m = merged.setdefault(k, {"a": k[0], "b": k[1], "networks": []})
        m["networks"] += [n for n in nets if n not in m["networks"]]
    if mode != "customer":  # Knoten ohne Zone an Netzen einer Zone (gestrichelt)
        for zid, infos in st.contacts.items():
            gz = group.get(zid)
            if gz is None:
                continue
            k = tuple(sorted((gz, "none"), key=lambda x: (isinstance(x, str), str(x))))
            m = merged.setdefault(k, {"a": k[0], "b": k[1], "networks": [], "contact": True})
            m["networks"] += [n for n in infos if n not in m["networks"]]
    used = {x for k in merged for x in k}

    zones_out: list[dict] = []
    for z in items:
        gid = group.get(z.id)
        if gid != z.id:
            continue
        hidden = z.id not in own and not zones_full[z.id]["nodes"] and z.id not in used
        if mode == "customer" and hidden:  # geteilte Zone ohne benoetigte Knoten und Uebergang
            continue
        zones_out.append(zones_full[z.id])
    collapsed = {int(str(x).split(":")[1]) for x in group.values()
                 if str(x).startswith("customer:")}  # fmt: skip
    for cid in sorted(collapsed, key=lambda c: cust.get(c, "")):
        members = [zmap[zid].name for zid, gid in group.items() if gid == f"customer:{cid}"]
        zones_out.append({"id": f"customer:{cid}", "name": cust.get(cid, f"Kunde #{cid}"),
                          "code": None, "kind": "customer", "color": None, "customer_id": cid,
                          "customer": cust.get(cid), "collapsed": True, "members": members,
                          "nodes": [], "nets": 0, "epgs": 0, "internal": 0, "samples": [],
                          "position": 0})  # fmt: skip
    unassigned = [topo_model.node_id(k) for k in st.unassigned()]
    if mode == "customer":  # Knoten ohne Zone nur, wenn der Kunde sie braucht
        unassigned = [n for n in unassigned if n in need]
    if unassigned:
        used.add("none")
    for pseudo, (name, kind) in PSEUDO.items():
        if pseudo in used:
            zones_out.append({"id": pseudo, "name": name, "code": None, "kind": kind,
                              "color": None, "customer_id": None, "customer": None,
                              "nodes": unassigned if pseudo == "none" else [], "nets": 0,
                              "epgs": 0, "internal": 0, "samples": [], "position": 0})  # fmt: skip
    by_id = {z["id"]: z for z in zones_out}
    trans_out = []
    for (a, b), m in merged.items():
        if a not in by_id or b not in by_id:
            continue
        nets = m["networks"]
        trans_out.append({
            "id": f"{a}~{b}", "a": a, "b": b, "name": transition_name(by_id[a], by_id[b]),
            "networks": nets[:zone_struct.SAMPLES], "count": len(nets),
            "l3out": any(leg["l3out"] for n in nets for leg in n["legs"]),
            "contact": bool(m.get("contact")),
        })  # fmt: skip
    trans_out.sort(key=lambda t: (t["contact"], t["name"].casefold()))
    node_ids = {n for z in zones_out for n in z["nodes"]}
    nodes_out = [
        {"id": topo_model.node_id(k), "label": n.label, "kind": n.kind, "source": n.source,
         "group": n.group, "zone": st.node_zone.get(k)}
        for k, n in sorted(g.nodes.items(), key=lambda x: x[1].label)
        if topo_model.node_id(k) in node_ids
    ]  # fmt: skip
    unzoned = st.unzoned if mode != "customer" else []
    unzoned = sorted(unzoned, key=lambda u: _net_key(ipaddress.ip_network(u["network"])))
    real = [z for z in zones_out if isinstance(z["id"], int)]
    one = next(iter(mine)) if len(mine) == 1 else None
    return {
        "format": 2,
        "view": {
            "mode": mode,
            "customer_id": one if mode == "customer" else None,
            "customer": cust.get(one) if mode == "customer" else None,
        },
        "customers": [
            {"id": c, "name": n}
            for c, n in sorted(cust.items(), key=lambda x: x[1])
            if c in set(owners.values())
        ],  # fmt: skip
        "zones": zones_out,
        "nodes": nodes_out,
        "transitions": trans_out,
        "conflicts": st.conflicts,
        "unzoned": {"total": len(unzoned), "items": unzoned[:UNZONED]},
        "summary": {
            "zones": len(real),
            "nodes": len(nodes_out),
            "transitions": sum(1 for t in trans_out if not t["contact"]),
            "collapsed": sum(1 for z in zones_out if z.get("collapsed")),
            "unassigned": len(unassigned),
            "unzoned": len(unzoned),
            "without_node": sum(1 for z in real if not z["nodes"] and not z["nets"]),
        },
    }


def _needed_net(net: dict, sides: tuple, node_zone: dict, need: set) -> bool:
    """Netz eines Uebergangs im Plan eines Kunden: an einem benoetigten Knoten und je Seite mit
    Knoten daran (Zone bzw. ohne Zone) einer davon benoetigt - sonst fuehrt es zu Geraeten, die
    der Kunde nicht braucht."""
    nodes = [leg["node"] for leg in net["legs"]]
    if not any(n in need for n in nodes):
        return False
    for side in sides:
        at = [n for n in nodes if node_zone.get(n, "none") == side]
        if at and not any(n in need for n in at):
            return False
    return True


def _customer_view(session: Session, tenant_id: int, items: list[LogicalZone], st, transitions,
                   by_zone: dict, owners: dict, mine: set) -> tuple[set, set, dict]:  # fmt: skip
    """Plan eines Kunden (0.57.1): seine Zonen; die Knoten, die er braucht (`zone_struct.needed`:
    an seinen Netzen und auf den kuerzesten Wegen zu den Zonen seiner Regeln); geteilte Zonen mit
    solchen Knoten, an seinen Zonen bzw. als Ziel seiner Regeln; andere Kunden mit genutztem
    Zugang (Uebergang bzw. Regeln) je als eine Zone. -> (eigene Zonen, Knoten, Gruppen)."""
    from app.store import zone_struct

    own = {zid for zid, c in owners.items() if c in mine}

    def foreign(x) -> bool:
        return isinstance(x, int) and owners.get(x) is not None and owners[x] not in mine

    adjacent = {y for t in transitions for x, y in ((t.a, t.b), (t.b, t.a)) if x in own}
    other = {z for z in adjacent if foreign(z)}
    targets: set = set()
    external = {z.id for z in items if z.kind == "external"}
    a = assign(session, tenant_id, items, nets=list(by_zone.values()))
    for rid in a.rules:
        for s, d in a.pairs(rid):
            for x, y in ((s, d), (d, s)):
                if x not in own:
                    continue
                if foreign(y):
                    other.add(y)
                elif isinstance(y, int) and y not in own:
                    targets.add(y)
                elif y == ANY:  # any: ueber die Zonen nach aussen (Public)
                    targets |= external
    need, reached = zone_struct.needed(st, own, targets | other)
    zone_nodes = st.zone_nodes()
    shared = {
        zid for zid, owner in owners.items()
        if owner is None and (zid in reached or zid in adjacent or any(
            topo_model.node_id(k) in need for k in zone_nodes.get(zid, [])))
    }  # fmt: skip
    group: dict = {zid: zid for zid in own | shared}
    for zid in other:
        group[zid] = f"customer:{owners[zid]}"
    return own, need, group


def plan_with_layout(session: Session, tenant_id: int, customer_ids=None,
                     customer_id: int | None = None, everything: bool = False,
                     view_filter=None) -> dict:  # fmt: skip
    """Plan samt Zeichnung (`plan_export.layout`) fuer die Oberflaeche; mit `customer_ids` die
    Sicht eines Kunden-Benutzers (`restrict_plan`). Seit 0.61.0 mit der gespeicherten Anordnung
    der Ansicht und dem Filter (`plan_view.Filter`: Suche, Fokus, Ebenen)."""
    from app.store import plan_export, plan_view

    if customer_ids is not None:
        out = plan(session, tenant_id, set(customer_ids) or {-1})
        out = restrict_plan(out, customer_view(load(session, tenant_id), customer_ids))
    else:
        out = plan(session, tenant_id, customer_id, everything)
    key = plan_view.view_key(out)
    row = plan_view.get(session, tenant_id, key)
    out["positions"] = dict(row.positions or {}) if row is not None else {}
    if customer_ids is None:
        out["arrangement"] = plan_view.arrangement_out(row, key)
    out = plan_view.apply(out, view_filter or plan_view.Filter(), devices=customer_ids is None)
    visible = {str(z["id"]) for z in out["zones"]}
    out["positions"] = {k: v for k, v in out["positions"].items() if k in visible}
    out["layout"] = plan_export.layout(out)
    return out


def restrict_plan(full: dict, view: CustomerView) -> dict:
    """Plan eines Kunden-Benutzers (ADR 0018 Schritt 4): seine Zonen mit ihren Knoten, die
    geteilten Zonen ohne Netze und Knoten (nur mit Uebergang im Ausschnitt), andere Kunden als
    eine Zone „anderer Kunde“; Netze der Uebergaenge nur an den eigenen Zonen, ohne fremde
    Geraete und Router-Namen."""
    own = {z["id"] for z in full["zones"] if isinstance(z["id"], int) and z["id"] in view.own}
    rename = {z["id"]: "other" for z in full["zones"] if z.get("collapsed")}
    own_nodes = {n for z in full["zones"] if z["id"] in own for n in z["nodes"]}
    zones = []
    for z in full["zones"]:
        if z["id"] in own:
            zones.append(z)
        elif z["id"] in rename:
            continue
        elif isinstance(z["id"], int):  # ohne Knoten, auch nicht deren Anzahl
            rest = {k: v for k, v in z.items() if k != "nodes_total"}
            zones.append({**rest, "nets": 0, "epgs": 0, "internal": 0, "samples": [], "nodes": []})
        elif z["id"] == "outside":
            zones.append(z)
    if rename:
        zones.append({"id": "other", "name": "anderer Kunde", "code": None, "kind": "customer",
                      "color": None, "customer_id": None, "customer": None, "collapsed": True,
                      "members": [], "nodes": [], "nets": 0, "epgs": 0, "internal": 0,
                      "samples": [], "position": 0})  # fmt: skip
    ids = {z["id"] for z in zones}
    by_id = {z["id"]: z for z in zones}
    trans: dict[tuple, dict] = {}
    for t in full["transitions"]:
        a, b = rename.get(t["a"], t["a"]), rename.get(t["b"], t["b"])
        if a not in ids or b not in ids or a == b:
            continue
        mine = bool({a, b} & own)
        nets = [{"network": n["network"], "routers": [],
                 "legs": [leg for leg in n["legs"] if leg["node"] in own_nodes]}
                for n in t["networks"]] if mine else []  # fmt: skip
        k = (a, b)
        x = trans.setdefault(k, {**t, "id": f"{a}~{b}", "a": a, "b": b, "networks": [],
                                 "name": transition_name(by_id[a], by_id[b]),
                                 "count": 0})  # fmt: skip
        x["networks"] += nets
        x["count"] += t["count"] if mine else 0
    # geteilte Zonen nur mit einem Uebergang im Ausschnitt (sonst erreicht ueber Knoten ohne
    # Zone, die er nicht sieht); andere Kunden auch ueber Regeln
    linked = {x for t in trans.values() for x in (t["a"], t["b"])} | own | {"other"}
    zones = [z for z in zones if z["id"] in linked]
    nodes = [n for n in full["nodes"] if n["id"] in own_nodes]
    real = [z for z in zones if isinstance(z["id"], int)]
    return {
        **full,
        "customers": [],
        "zones": zones,
        "nodes": nodes,
        "transitions": list(trans.values()),
        "conflicts": [],
        "unzoned": {"total": 0, "items": []},
        "summary": {
            "zones": len(real),
            "nodes": len(nodes),
            "transitions": len(trans),
            "collapsed": 1 if rename else 0,
            "unassigned": 0,
            "unzoned": 0,
            "without_node": sum(
                1 for z in real if z["id"] in own and not z["nodes"] and not z["nets"]
            ),  # fmt: skip
        },
    }
