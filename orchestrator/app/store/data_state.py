"""Datenstand nach Inhalt je Quellsystem (0.141.0, ADR 0049).

Anwender 2026-11-06: „Die Berechnungen nur anstossen, wenn was geändert hat, also nur die Wege
berechnen, welche von der geänderten Firewall Regel betroffen sind.“ Bis 0.140.0 galt jeder Lauf
als neuer Datenstand (Zeitpunkt des letzten Laufs im Abdruck der Doku) - auch ohne Aenderung.

Nach dem Einspielen (`record`, Referenzquellen `record_reference`) vergleicht der Orchestrator den
Inhalt mit dem letzten Lauf desselben Quellsystems:

- **je Teil** ein Abdruck (Objekte, NAT, EPGs, VMs, Routen, angeschlossene Netze, Interfaces …),
  je Eintrag (Objekt, VIP, EPG, VM) mit seinen Adressen, **je Regel** Inhalt, Adressbereiche,
  Dienste und Vorgaenger (Reihenfolge);
- nur wenn sich etwas unterscheidet, entsteht eine `DataChange` mit der naechsten Nummer des
  Mandanten (`seq`): geaenderte Regeln, betroffene Adressen, Geraete und EPGs bzw. `all`, wenn die
  Aenderung jede Applikation betreffen kann. Der Datenstand des Mandanten ist die hoechste Nummer
  (`generation`) - ein Lauf ohne Aenderung aendert ihn nicht.

Wirkung je Teil (`SCOPES`) - im Zweifel `all`:

- Regeln: einzeln (`store/footprint.py` prueft je Applikation);
- Objekte, VMs, EPGs: ueber ihre Adressen (alte und neue), EPGs auch ueber die DN;
- NAT, Routen, Policy-Routen: das Geraet (Applikationen, deren Wege bzw. Regeln es nutzen), NAT
  zusaetzlich ueber die Adressen;
- angeschlossene Netze, Interfaces, Subnetze, VRFs, Kontexte, HA, L3Outs, BGP-Nachbarn und
  -Filter, Portgruppen, Referenzquellen (IPAM/DNS): `all`;
- MAC-/ARP-Tabellen und Nachbarn (CDP/LLDP) der Switches: keine (Doku und Wege nutzen sie nicht);
  ebenso die ESXi-Hosts eines vCenters mit vmnics und Nachbarn (1.8.0, `virtual.hosts`) - sie
  dienen nur Netzinventar, Verkabelung und NetBox-Sync, die jeweils beim Abruf rechnen.
"""

import hashlib
import ipaddress
import json
import logging
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import DataChange, DataState, ReferenceRecordRow, RuleIpRange

log = logging.getLogger(__name__)

ALL, DEVICE, IPS = "all", "device", "ips"
NONE: frozenset[str] = frozenset()
SCOPES: dict[str, frozenset[str]] = {
    "objects": frozenset({IPS}),
    "nat": frozenset({DEVICE, IPS}),
    "nat.avi": frozenset({IPS}),  # NSX ALB: kein Knoten der Wege, VIP und Pool ueber die Adresse
    "epgs": frozenset({IPS}),
    "virtual.vms": frozenset({IPS}),
    "routing.routes": frozenset({DEVICE}),
    "routing.policy_routes": frozenset({DEVICE}),
    "inventory.neighbors": NONE,
    "inventory.macs": NONE,
    "inventory.arps": NONE,
    "virtual.hosts": NONE,  # ESXi-Hosts (1.8.0): nur Inventar/Verkabelung, nicht Doku und Wege
}
# Teile, die Routing bzw. Inventar sind (Routing-Filter, `bgp_cache`)
_NET_PREFIXES = ("routing.", "inventory.")
KEEP = 5000  # Aenderungen je Mandant, die bleiben (aeltere Staende rechnen dann neu)
_CHUNK = 500


def scope(part: str) -> frozenset[str]:
    return SCOPES.get(part, frozenset({ALL}))


# -- Abdruecke ---------------------------------------------------------------------------


def _h(value) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.blake2b(text.encode(), digest_size=12).hexdigest()


def _dump(value):
    """Eintrag eines Laufs als JSON-Wert (Modelle des Collectors, Listen von Texten bzw. dicts)."""
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def ranges(values) -> list[str]:
    """Adressen bzw. Netze als Bereiche `4:lo-hi` (hex); Bereiche `a-b`, Werte mit Art (`range:`),
    unlesbare (FQDN, Geo …) fallen weg."""
    out: list[str] = []
    for raw in values or []:
        text = str(raw or "").strip()
        if not text:
            continue
        if ":" in text and text.split(":", 1)[0] in ("range", "host", "net", "ip", "subnet"):
            text = text.split(":", 1)[1].strip()
        try:
            if "-" in text and "/" not in text:
                a, b = (ipaddress.ip_address(x.strip()) for x in text.split("-", 1))
                lo, hi, fam = int(a), int(b), a.version
            else:
                net = ipaddress.ip_network(text.split()[0], strict=False)
                lo, hi = int(net.network_address), int(net.broadcast_address)
                fam = net.version
        except ValueError:
            continue
        out.append(f"{fam}:{lo:x}-{hi:x}")
    return out


def parse(r: str) -> tuple[int, int, int]:
    fam, _, span = r.partition(":")
    lo, _, hi = span.partition("-")
    return int(fam), int(lo, 16), int(hi, 16)


def _items(entries) -> dict[str, list]:
    """{Schluessel: [Abdruck, Bereiche]} -> Abdruck des Teils."""
    return {k: v for k, v in entries}


def _part_digest(items: dict[str, list]) -> str:
    return _h(sorted((k, v[0]) for k, v in items.items()))


def _list_digest(entries: list) -> str:
    return _h(sorted(_h(_dump(e)) for e in entries))


def _nat_ips(n) -> list[str]:
    vals = [n.ext_ip, n.mapped_ip]
    for t in n.targets or []:
        if isinstance(t, dict):
            vals += [t.get("ip"), t.get("address")]
    return ranges(v for v in vals if v)


def _epg_ips(e) -> list[str]:
    return ranges([*e.subnets, *e.bd_subnets, *(p.ip for p in e.endpoints)])


def _payload(result, platform: str) -> tuple[dict[str, str], dict[str, dict[str, list]]]:
    """Abdruecke der Teile, die der Lauf geliefert hat, und die Eintraege mit Adressen."""
    parts: dict[str, str] = {}
    items: dict[str, dict[str, list]] = {}

    def keep(part: str, entries) -> None:
        found = _items(entries)
        items[part] = found
        parts[part] = _part_digest(found)

    keep("objects", ((f"{o.vdom}|{o.category}|{o.family}|{o.name}",
                      [_h(_dump(o)), ranges(o.values or ([o.value] if o.value else []))])
                     for o in result.objects))  # fmt: skip
    nat = "nat.avi" if platform == "avi" else "nat"
    keep(nat, ((f"{n.nat_type}|{n.vdom}|{n.external_id}|{n.name}|{n.family}",
                [_h(_dump(n)), _nat_ips(n)]) for n in result.nat_rules))  # fmt: skip
    keep("epgs", ((e.dn or e.name, [_h(_dump(e)), _epg_ips(e)]) for e in result.epgs))
    if result.virtual is not None:
        vms = result.virtual.vms
        keep("virtual.vms", ((v.uuid or v.name, [_h(_dump(v)),
                                                 ranges(ip for n in v.nics for ip in n.ips)])
                             for v in vms))  # fmt: skip
        parts["virtual.portgroups"] = _list_digest(result.virtual.portgroups)
        if result.virtual.hosts is not None:  # 1.8.0: aeltere Agenten ohne Hosts - Teil bleibt
            parts["virtual.hosts"] = _list_digest(result.virtual.hosts)
    if result.inventory is not None:
        for field in type(result.inventory).model_fields:
            parts[f"inventory.{field}"] = _list_digest(getattr(result.inventory, field) or [])
    if result.routing is not None:
        for field in type(result.routing).model_fields:
            entries = getattr(result.routing, field) or []
            if field == "routes":  # angeschlossene Netze wirken ueberall (Startpunkte, Adressen)
                near = [r for r in entries if (r.protocol or "") in ("connected", "local")]
                far = [r for r in entries if (r.protocol or "") not in ("connected", "local")]
                parts["routing.connected"] = _list_digest(near)
                parts["routing.routes"] = _list_digest(far)
            else:
                parts[f"routing.{field}"] = _list_digest(entries)
    return parts, items


def _rule_digests(session: Session, rules: list, normalized: list) -> dict[str, list]:
    """Je Regel (Kennung): [Inhalt, Vorgaenger in ihrer VDOM] - Inhalt = gelieferte Regel,
    Adressbereiche (aufgeloeste Objekte, VIP-Ziele) und Dienste."""
    ids = [r.id for r in rules]
    found: dict[int, list] = defaultdict(list)
    for i in range(0, len(ids), _CHUNK):
        chunk = ids[i : i + _CHUNK]
        for rid, role, fam, lo, hi, any_, via in session.execute(
            select(RuleIpRange.rule_id, RuleIpRange.role, RuleIpRange.family, RuleIpRange.lo,
                   RuleIpRange.hi, RuleIpRange.is_any, RuleIpRange.via)
            .where(RuleIpRange.rule_id.in_(chunk))
        ):  # fmt: skip
            found[rid].append((role, fam, lo, hi, bool(any_), via))
    out: dict[str, list] = {}
    last: dict[tuple, int] = {}
    for rule, nr in zip(rules, normalized, strict=True):
        services = sorted(s.canonical_name for s in rule.services)
        content = _h([_dump(nr), sorted(found.get(rule.id, [])), services])
        group = (str(getattr(rule.domain, "value", rule.domain)), rule.vdom)
        out[str(rule.id)] = [content, last.get(group)]
        last[group] = rule.id
    return out


# -- Vergleich ----------------------------------------------------------------------------


def _changed_items(old: dict[str, list], new: dict[str, list]) -> tuple[set[str], list[str]]:
    keys = {k for k in set(old) | set(new)
            if (old.get(k) or [None])[0] != (new.get(k) or [None])[0]}  # fmt: skip
    ips: list[str] = []
    for k in keys:
        for side in (old.get(k), new.get(k)):
            if side:
                ips.extend(side[1] or [])
    return keys, ips


def _changed_rules(old: dict[str, list], new: dict[str, list]) -> list[int]:
    return sorted(int(k) for k in set(old) | set(new) if old.get(k) != new.get(k))


def generation(session: Session, tenant_id: int, net_only: bool = False) -> int:
    """Datenstand des Mandanten: Nummer der letzten Aenderung (0 = keine). `net_only`: nur
    Aenderungen an Routing bzw. Inventar (Routing-Filter)."""
    stmt = select(func.max(DataChange.seq)).where(DataChange.tenant_id == tenant_id)
    if net_only:
        stmt = stmt.where(DataChange.net.is_(True))
    return session.scalar(stmt) or 0


def changes(session: Session, tenant_id: int, after: int, upto: int) -> list[DataChange] | None:
    """Aenderungen nach `after` bis `upto` - None, wenn welche fehlen (aufgeraeumt)."""
    if upto <= after:
        return []
    rows = list(session.scalars(
        select(DataChange)
        .where(DataChange.tenant_id == tenant_id, DataChange.seq > after, DataChange.seq <= upto)
        .order_by(DataChange.seq)
    ))  # fmt: skip
    return rows if len(rows) == upto - after else None


def _store(session: Session, run, state: DataState | None, parts: dict, items: dict,
           rules: dict | None) -> DataChange | None:  # fmt: skip
    """Mit dem letzten Stand vergleichen, Aenderung anlegen, Stand fortschreiben."""
    sid, tid = run.source_system_id, run.tenant_id
    first = state is None
    if state is None:
        state = DataState(source_system_id=sid, tenant_id=tid, parts={}, rules={}, items={})
        session.add(state)
    old_parts, old_items = dict(state.parts or {}), dict(state.items or {})
    old_rules = dict(state.rules or {})
    changed = sorted(p for p, d in parts.items() if old_parts.get(p) != d)
    rule_ids = _changed_rules(old_rules, rules) if rules is not None else []
    if rule_ids and "rules" not in changed:
        changed.append("rules")
    devices: set[int] = set()
    ips: list[str] = []
    epgs: set[str] = set()
    every = first
    for part in changed:
        if part == "rules":
            continue
        effect = scope(part)
        if ALL in effect:
            every = True
        if DEVICE in effect:
            devices.add(sid)
        if IPS in effect and part in items:
            keys, found = _changed_items(old_items.get(part) or {}, items[part])
            ips.extend(found)
            if part == "epgs":
                epgs |= keys
    if changed or first:
        seq = generation(session, tid) + 1
        change = DataChange(tenant_id=tid, seq=seq, source_system_id=sid, run_id=run.id,
                            at=datetime.now(UTC), parts=changed, all=every,
                            net=every or any(p.startswith(_NET_PREFIXES) for p in changed),
                            rules=rule_ids or None, devices=sorted(devices) or None,
                            ips=sorted(set(ips)) or None, epgs=sorted(epgs) or None)  # fmt: skip
        session.add(change)
        session.execute(delete(DataChange).where(DataChange.tenant_id == tid,
                                                 DataChange.seq <= seq - KEEP))  # fmt: skip
    else:
        change = None
    state.parts = {**old_parts, **parts}
    state.items = {**old_items, **items}
    if rules is not None:
        state.rules = rules
    state.updated_at = datetime.now(UTC)
    session.flush()
    if change is not None:
        log.info("Quellsystem %s: Datenstand %s geaendert (%s; %d Regeln)%s", sid, change.seq,
                 ", ".join(changed) or "erster Lauf", len(rule_ids),
                 " - alle Applikationen" if every else "")  # fmt: skip
    else:
        log.info("Quellsystem %s: Lauf ohne Aenderung am Datenstand", sid)
    return change


def record(session: Session, run, result, rules: list) -> DataChange | None:
    """Nach dem Einspielen eines Laufs (`ingest.persist_result`): Inhalt vergleichen."""
    if run.source_system_id is None or run.tenant_id is None:
        return None
    platform = str(getattr(run.platform, "value", run.platform) or "")
    parts, items = _payload(result, platform)
    rule_state = _rule_digests(session, rules, result.rules)
    state = session.get(DataState, run.source_system_id)
    return _store(session, run, state, parts, items, rule_state)


def record_reference(session: Session, run) -> DataChange | None:
    """Nach dem Abschluss eines Laufs einer Referenzquelle (IPAM/DNS): ihr Inhalt als ein Teil -
    Namen und Netze wirken in jeder Applikation."""
    if run.source_system_id is None or run.tenant_id is None:
        return None
    skip = ("id", "tenant_id", "source_system_id", "collector_run_id")
    cols = [c for c in ReferenceRecordRow.__table__.columns if c.name not in skip]
    rows = session.execute(
        select(*cols).where(ReferenceRecordRow.collector_run_id == run.id)
    ).all()  # fmt: skip
    digest = _h(sorted(_h(list(r)) for r in rows))
    state = session.get(DataState, run.source_system_id)
    return _store(session, run, state, {"reference": digest}, {}, None)


def describe(change: DataChange) -> dict:
    """Aenderung fuer Anzeige und Log."""
    return {"seq": change.seq, "source_system_id": change.source_system_id,
            "at": change.at.isoformat() if change.at else None, "parts": change.parts,
            "all": change.all, "rules": len(change.rules or []),
            "devices": change.devices or [], "epgs": len(change.epgs or []),
            "ips": len(change.ips or [])}  # fmt: skip
