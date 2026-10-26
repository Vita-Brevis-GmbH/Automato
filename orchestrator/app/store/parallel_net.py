"""Parallele Firewalls (0.72.0, ADR 0020 Ergaenzung): Policy-Routen, Routing und NAT im
Verbund vergleichen - wie die Regeln (`store/parallel.py`) fuehrt die Firewall mit dem
kleinsten Namen.

- **Policy-Routen und SD-WAN-Regeln** je VDOM und Art ueber die Nummer (seq-num bzw. ID),
  sonst den Namen: Status, Bedingung (Eingang, Quelle, Ziel, Protokoll, Ports, Internet-
  Service), Aktion bzw. Modus, Gateway, Ausgang (SD-WAN: Reihenfolge der Mitglieder) und
  SD-WAN-Zonen. Abweichend bzw. fehlend -> Befund `parallel_policy_route_mismatch` je Eintrag.
- **Routing** je VDOM: die wirksame Tabelle (Praefix in seiner VRF: Protokoll und
  Ausgangs-Interfaces - die Next-Hop-IPs nicht, das sind je Firewall eigene Router bzw.
  Adressen) und die konfigurierten statischen Routen (Praefix: Distanz, Interface, Gateway,
  Status, Blackhole). Ein Befund `parallel_route_mismatch` je Firewall und VDOM mit den
  abweichenden Praefixen. Nicht lesbare Tabellen (`topo_context.missing`) bleiben aussen vor.
  BGP-Nachbarn und -Pfade nicht: jede Firewall hat eigene Nachbarn, die Wirkung zeigt die
  Routing-Tabelle.
- **NAT:** VIPs und IP-Pools je VDOM, Familie und Name: externe/interne IP und Ports,
  Protokoll, Port-Weiterleitung, Typ, externes Interface, alle Ziele, Real Server und
  Einstellungen (Methode, Persistenz, Health-Check, Pool-Typ, Quellbereich). Befund
  `parallel_nat_mismatch` je VIP bzw. Pool.

Rein lesend; Teil des Abgleichs (`parallel.compare`).
"""

import json
from collections import defaultdict
from collections.abc import Callable, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    FindingType,
    NatRule,
    Severity,
    SubjectKind,
    TopoContext,
    TopoPolicyRoute,
    TopoRoute,
)

MAX_ENTRIES = 200  # Praefixe je Routing-Befund (Rest als Zahl)

POLICY_FIELDS = {
    "enabled": "Status",
    "input_device": "Eingang",
    "src": "Quelle",
    "srcaddr": "Quelle",
    "dst": "Ziel",
    "dstaddr": "Ziel",
    "protocol": "Protokoll",
    "ports": "Ports",
    "service": "Internet-Service",
    "action": "Aktion",
    "gateway": "Gateway",
    "output_device": "Ausgang",
    "zones": "SD-WAN-Zonen",
}

NAT_FIELDS = {
    "ext_ip": "Externe IP",
    "ext_port": "Externer Port",
    "mapped_ip": "Interne IP",
    "mapped": "Interne Ziele",
    "mapped_port": "Interner Port",
    "protocol": "Protokoll",
    "portforward": "Port-Weiterleitung",
    "vip_type": "Typ",
    "extintf": "Externes Interface",
    "targets": "Real Server",
    "attrs": "Einstellungen",
}

RIB_FIELDS = {"protocols": "Protokoll", "interfaces": "Interface"}
STATIC_FIELDS = {"routes": "Distanz/Interface/Gateway/Status"}


def _text(value) -> str:
    return "" if value is None else str(value).strip().lower()


def _names(values) -> frozenset:
    out = set()
    for v in values or []:
        name = v.get("name") if isinstance(v, dict) else v
        if name not in (None, ""):
            out.add(_text(name))
    return frozenset(out)


def shown(value) -> str:
    if isinstance(value, frozenset):
        return ", ".join(sorted(value)) or "–"
    if isinstance(value, tuple):
        return " → ".join(value) or "–"
    return value or "–"


def _differences(a: dict, b: dict, labels: dict[str, str]) -> tuple[list[str], dict]:
    """(Bezeichnungen der abweichenden Felder, {Bezeichnung: [Wert hier, Wert dort]})."""
    fields: list[str] = []
    values: dict[str, list[str]] = {}
    for key, label in labels.items():
        if a.get(key) == b.get(key):
            continue
        if label not in fields:
            fields.append(label)
        pair = [shown(a.get(key)), shown(b.get(key))]
        if label in values:  # z. B. Quelle als Netz und als Objekt
            values[label] = [f"{values[label][0]}; {pair[0]}", f"{values[label][1]}; {pair[1]}"]
        else:
            values[label] = pair
    return fields, values


# -- Inhalte ------------------------------------------------------------------------------


def policy_signature(p: TopoPolicyRoute) -> dict:
    match = p.match or {}
    out: dict = {"enabled": "ein" if p.enabled else "aus"}
    for key in ("input_device", "src", "srcaddr", "dst", "dstaddr", "service"):
        out[key] = _names(match.get(key))
    out["protocol"] = _text(match.get("protocol"))
    ports = match.get("ports") or []
    out["ports"] = "-".join(str(x) for x in ports) if ports else ""
    out["action"] = _text(p.action)
    out["gateway"] = _text(p.gateway)
    # SD-WAN: Reihenfolge der Mitglieder zaehlt; Policy-Route: ein Interface
    out["output_device"] = tuple(_text(d) for d in p.output_device or [])
    out["zones"] = _names((p.attrs or {}).get("zones")) if p.kind == "sdwan" else frozenset()
    return out


def nat_signature(n: NatRule) -> dict:
    raw = n.raw or {}
    mapped = raw.get("mappedip")
    ranges = (
        frozenset(_text(m.get("range") if isinstance(m, dict) else m) for m in mapped)
        if isinstance(mapped, list)
        else frozenset([_text(mapped)]) - {""}
    )
    targets = frozenset(
        ":".join(
            str(x) for x in (t.get("ip") or t.get("address"), t.get("port")) if x not in (None, "")
        ).lower()
        + f" ({_text(t.get('status')) or 'active'})"
        + (f" w{t['weight']}" if t.get("weight") not in (None, "", 1) else "")
        for t in n.targets or []
        if isinstance(t, dict)
    )
    attrs = n.attrs or {}
    extintf = raw.get("extintf")
    return {
        "ext_ip": _text(n.ext_ip),
        "ext_port": _text(n.ext_port),
        "mapped_ip": _text(n.mapped_ip),
        "mapped": ranges,
        "mapped_port": _text(n.mapped_port),
        "protocol": _text(n.protocol),
        "portforward": "ein" if n.portforward else "aus",
        "vip_type": _text(n.vip_type),
        "extintf": _names(extintf if isinstance(extintf, list) else [extintf]),
        "targets": targets,
        "attrs": ", ".join(f"{k}={_attr(v)}" for k, v in sorted(attrs.items())).lower(),
    }


def _attr(value) -> str:
    if isinstance(value, dict | list):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value)


def _rib(routes: Iterable[TopoRoute]) -> dict[tuple, dict]:
    """Wirksame Tabelle: (Familie, Praefix, VRF, Management) -> Protokolle, Interfaces,
    Next-Hops (nur zur Anzeige)."""
    out: dict[tuple, dict] = {}
    for r in routes:
        attrs = r.attrs or {}
        key = (r.family, r.prefix, int(attrs.get("vrf") or 0), bool(attrs.get("mgmt")))
        e = out.setdefault(key, {"protocols": set(), "interfaces": set(), "hops": set()})
        e["protocols"].add(_text(r.protocol) or "?")
        for h in r.next_hops or []:
            if h.get("interface"):
                e["interfaces"].add(_text(h["interface"]))
            if h.get("ip"):
                e["hops"].add(str(h["ip"]))
    return {
        k: {
            "protocols": frozenset(v["protocols"]),
            "interfaces": frozenset(v["interfaces"]),
            "hops": frozenset(v["hops"]),
        }
        for k, v in out.items()
    }


def _static(routes: Iterable[TopoRoute]) -> dict[tuple, dict]:
    """Statische Routen: (Familie, Praefix) -> Menge der Eintraege (Distanz, Interface,
    Gateway, Status, Blackhole) - mehrere gleiche Praefixe (ECMP, Backup) als Menge."""
    out: dict[tuple, set] = defaultdict(set)
    for r in routes:
        attrs = r.attrs or {}
        hop = (r.next_hops or [{}])[0] or {}
        parts = [
            f"Distanz {r.distance}" if r.distance is not None else "",
            _text(hop.get("interface")),
            str(hop.get("ip") or ""),
            "blackhole" if attrs.get("blackhole") else "",
            "" if r.best else "aus",
        ]
        out[(r.family, r.prefix)].add(" ".join(p for p in parts if p) or "–")
    return {k: {"routes": frozenset(v)} for k, v in out.items()}


def _unreadable(missing: list | None) -> set[str]:
    """Nicht lesbare Tabellen (`topo_context.missing`, z. B. „Routing-Tabelle IPv4 (HTTP
    403)“) -> rib4/rib6/static4/static6/policy/sdwan."""
    out = set()
    for m in missing or []:
        text = str(m)
        for label, key in (
            ("Routing-Tabelle IPv4", "rib4"),
            ("Routing-Tabelle IPv6", "rib6"),
            ("statische Routen IPv4", "static4"),
            ("statische Routen IPv6", "static6"),
            ("Policy-Routen", "policy"),
            ("SD-WAN", "sdwan"),
        ):
            if text.startswith(label):
                out.add(key)
    return out


# -- Vergleich ----------------------------------------------------------------------------


def _match_items(
    ids: list[int],
    items: dict[int, list],
    keys: Callable[[object], list[tuple]],
    sign: Callable[[object], dict],
    labels: dict[str, str],
) -> tuple[list, list, int]:
    """Eintraege der Firewalls des Verbunds zuordnen (erster Schluessel, sonst der naechste,
    z. B. Nummer und Name): (abweichend [(fuehrend, anderer, Felder, Werte)], fehlend
    [(Eintrag, fehlt auf Firewall-IDs)], gleich). Werte: [anderer, fuehrend]."""
    maps = {}
    for sid in ids:
        per: dict[tuple, object] = {}
        dup: set[tuple] = set()
        for item in items.get(sid, []):
            for k in keys(item):
                if k in per:
                    dup.add(k)
                per.setdefault(k, item)
        maps[sid] = {k: v for k, v in per.items() if k not in dup or k == keys(v)[0]}
    claimed: set[int] = set()
    differs, missing, same = [], [], 0
    for pos, sid in enumerate(ids):
        for lead in items.get(sid, []):
            if id(lead) in claimed:
                continue
            sig = sign(lead)
            gone = list(ids[:pos])  # Firewalls davor haetten den Eintrag schon gefunden
            for other in ids[pos + 1 :]:
                twin = next((maps[other][k] for k in keys(lead) if k in maps[other]), None)
                if twin is None or id(twin) in claimed:
                    gone.append(other)
                    continue
                claimed.add(id(twin))
                # Werte wie im Befund: zuerst der andere (Gegenstand), dann der fuehrende
                fields, values = _differences(sign(twin), sig, labels)
                if fields:
                    differs.append((lead, twin, fields, values))
                else:
                    same += 1
            if gone:
                missing.append((lead, gone))
    return differs, missing, same


def _policy_keys(p: TopoPolicyRoute) -> list[tuple]:
    out = []
    if p.seq is not None:
        out.append((p.context, p.kind, "seq", p.seq))
    if p.name:
        out.append((p.context, p.kind, "name", p.name.strip().lower()))
    return out or [(p.context, p.kind, "id", p.id)]


def _policy_label(p: TopoPolicyRoute) -> str:
    what = "SD-WAN-Regel" if p.kind == "sdwan" else "Policy-Route"
    num = f" {p.seq}" if p.seq is not None else ""
    return f"{what}{num}" + (f" „{p.name}“" if p.name else "")


def _nat_keys(n: NatRule) -> list[tuple]:
    return [(n.vdom, n.nat_type, n.family, (n.name or "").strip().lower())]


def compare(session: Session, Issue, group: str, ids: list[int], names: dict[int, str]):
    """Policy-Routen, Routing und NAT der Firewalls eines Verbunds: (Befunde, Kennzahlen)."""
    issues: list = []
    stats: dict[str, dict[str, int]] = {}

    contexts = {
        (c.source_system_id, c.context): c
        for c in session.scalars(select(TopoContext).where(TopoContext.source_system_id.in_(ids)))
    }
    unreadable = {k: _unreadable(c.missing) for k, c in contexts.items()}

    # Policy-Routen und SD-WAN-Regeln
    pbr: dict[int, list] = defaultdict(list)
    for p in session.scalars(
        select(TopoPolicyRoute)
        .where(TopoPolicyRoute.source_system_id.in_(ids))
        .order_by(
            TopoPolicyRoute.source_system_id,
            TopoPolicyRoute.context,
            TopoPolicyRoute.kind,
            TopoPolicyRoute.seq,
            TopoPolicyRoute.id,
        )
    ):
        table = "sdwan" if p.kind == "sdwan" else "policy"
        if any(table in unreadable.get((sid, p.context), set()) for sid in ids):
            continue  # auf einer Firewall nicht lesbar - kein Vergleich
        pbr[p.source_system_id].append(p)
    differs, missing, same = _match_items(ids, pbr, _policy_keys, policy_signature, POLICY_FIELDS)
    stats["policy_routes"] = {"same": same, "differs": len(differs), "missing": len(missing)}
    for lead, twin, fields, values in differs:
        extra = {"other": names[lead.source_system_id], "fields": fields, "values": values}
        issues.append(_policy_issue(Issue, group, twin, names, extra))
    for lead, gone in missing:
        extra = {"missing_on": [names[s] for s in gone]}
        issues.append(_policy_issue(Issue, group, lead, names, extra))

    # NAT: VIPs und IP-Pools
    nat: dict[int, list] = defaultdict(list)
    for n in session.scalars(
        select(NatRule)
        .where(NatRule.source_system_id.in_(ids))
        .order_by(NatRule.source_system_id, NatRule.vdom, NatRule.nat_type, NatRule.name)
    ):
        nat[n.source_system_id].append(n)
    differs, missing, same = _match_items(ids, nat, _nat_keys, nat_signature, NAT_FIELDS)
    stats["nat"] = {"same": same, "differs": len(differs), "missing": len(missing)}
    for lead, twin, fields, values in differs:
        extra = {
            "other": names[lead.source_system_id],
            "other_nat_id": lead.id,
            "fields": fields,
            "values": values,
        }
        issues.append(_nat_issue(Issue, group, twin, names, extra))
    for lead, gone in missing:
        extra = {"missing_on": [names[s] for s in gone]}
        issues.append(_nat_issue(Issue, group, lead, names, extra))

    # Routing je VDOM gegen die fuehrende Firewall
    routes: dict[tuple, list] = defaultdict(list)
    for r in session.scalars(
        select(TopoRoute).where(
            TopoRoute.source_system_id.in_(ids), TopoRoute.table.in_(("rib", "static"))
        )
    ):
        routes[(r.source_system_id, r.context, r.table)].append(r)
    stats["routes"] = {"same": 0, "differs": 0, "missing": 0}
    lead = ids[0]
    lead_ctx = {ctx for sid, ctx in contexts if sid == lead}
    for other in ids[1:]:
        other_ctx = {ctx for sid, ctx in contexts if sid == other}
        if not lead_ctx or not other_ctx:
            continue  # Routing einer Firewall nicht gelesen (z. B. Lauf vor 0.16.0)
        for ctx in sorted(lead_ctx | other_ctx):
            entries = _route_entries(lead, other, ctx, routes, contexts, unreadable, stats)
            if entries:
                issues.append(_route_issue(Issue, group, other, lead, ctx, names, entries))
    return issues, stats


def _route_entries(lead, other, ctx, routes, contexts, unreadable, stats) -> list[dict]:
    if (lead, ctx) not in contexts or (other, ctx) not in contexts:
        here = (other, ctx) in contexts
        stats["routes"]["missing"] += 1
        return [{"table": "vdom", "prefix": ctx, "kind": "only_here" if here else "only_lead"}]
    skip = unreadable[(lead, ctx)] | unreadable[(other, ctx)]
    out: list[dict] = []
    for table, build, labels in (("rib", _rib, RIB_FIELDS), ("static", _static, STATIC_FIELDS)):
        a = build(routes.get((lead, ctx, table), []))
        b = build(routes.get((other, ctx, table), []))
        for key in sorted(set(a) | set(b), key=_route_sort):
            family = key[0]
            if f"{table}{family}" in skip:
                continue
            entry = {"table": table, "family": family, "prefix": key[1]}
            if table == "rib" and (key[2] or key[3]):
                entry["vrf"] = "Management" if key[3] else key[2]
            if key not in b:
                out.append({**entry, "kind": "only_lead", "lead": _route_shown(a[key])})
                stats["routes"]["missing"] += 1
            elif key not in a:
                out.append({**entry, "kind": "only_here", "here": _route_shown(b[key])})
                stats["routes"]["missing"] += 1
            else:
                fields, _ = _differences(a[key], b[key], labels)
                if fields:
                    shown_ab = {"lead": _route_shown(a[key]), "here": _route_shown(b[key])}
                    out.append({**entry, "kind": "differs", "fields": fields, **shown_ab})
                    stats["routes"]["differs"] += 1
                else:
                    stats["routes"]["same"] += 1
    return out


def _route_sort(key: tuple):
    import ipaddress

    try:
        net = ipaddress.ip_network(key[1], strict=False)
        return (key[0], int(net.network_address), net.prefixlen, key[2:])
    except ValueError:
        return (key[0], 0, 0, key[2:])


def _route_shown(e: dict) -> str:
    if "routes" in e:
        return "; ".join(sorted(e["routes"]))
    hops = f" über {', '.join(sorted(e['hops']))}" if e.get("hops") else ""
    return f"{shown(e['protocols'])} · {shown(e['interfaces'])}{hops}"


# -- Befunde ------------------------------------------------------------------------------


def _policy_issue(Issue, group: str, p: TopoPolicyRoute, names, extra: dict):
    key = p.seq if p.seq is not None else (p.name or p.id)
    return Issue(
        type=FindingType.PARALLEL_POLICY_ROUTE_MISMATCH,
        subject_kind=SubjectKind.NODE,
        subject_id=f"{p.source_system_id}|{p.context}|{p.kind}|{key}",
        source_system_id=p.source_system_id,
        severity=Severity.WARNING,
        detail={
            "group": group,
            "source": names.get(p.source_system_id),
            "vdom": p.context,
            "kind": p.kind,
            "seq": p.seq,
            "name": p.name,
            "label": _policy_label(p),
            **extra,
        },
    )


def _nat_issue(Issue, group: str, n: NatRule, names, extra: dict):
    return Issue(
        type=FindingType.PARALLEL_NAT_MISMATCH,
        subject_kind=SubjectKind.NAT,
        subject_id=f"{n.source_system_id}|{n.vdom or ''}|{n.nat_type}|{n.family or ''}|{n.name}",
        source_system_id=n.source_system_id,
        severity=Severity.WARNING,
        detail={
            "group": group,
            "source": names.get(n.source_system_id),
            "vdom": n.vdom,
            "nat_id": n.id,
            "name": n.name,
            "nat_type": n.nat_type,
            "family": n.family,
            **extra,
        },
    )


def _route_issue(Issue, group: str, sid: int, lead: int, ctx: str, names, entries: list[dict]):
    counts = defaultdict(int)
    for e in entries:
        counts[e["kind"]] += 1
    return Issue(
        type=FindingType.PARALLEL_ROUTE_MISMATCH,
        subject_kind=SubjectKind.NODE,
        subject_id=f"{sid}|{ctx}",
        source_system_id=sid,
        severity=Severity.WARNING,
        detail={
            "group": group,
            "source": names.get(sid),
            "other": names.get(lead),
            "vdom": ctx,
            "count": len(entries),
            "counts": dict(counts),
            "entries": entries[:MAX_ENTRIES],
        },
    )
