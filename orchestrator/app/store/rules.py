"""Rule & Service Store - Lesezugriff auf Regeln (ADR 0004).

Abfrage-Helfer fuer die API: Regelliste, Regel-Detail (inkl. Endpunkte/Services)
sowie Batch-Lookups fuer Netzwerkobjekte und Zonen, damit die Anreicherung der
Liste ohne N+1-Queries auskommt.
"""

import ipaddress

from sqlalchemy import and_, delete, exists, func, select
from sqlalchemy.orm import Session, aliased, selectinload

from app.models import (
    MatchStatus,
    NetworkObject,
    Rule,
    RuleDomain,
    RuleEndpoint,
    RuleIndex,
    RuleIpRange,
    RuleObjectRef,
    RuleObjectUsage,
    Zone,
    rule_service,
)


def delete_rules(session: Session, condition) -> int:
    """Regeln samt Endpunkten, Verweisen, Services und Index per SQL loeschen (schnell auch bei
    grossen Regelwerken; das ORM laedt sonst jede Regel mit allen Kindern einzeln)."""
    ids = select(Rule.id).where(condition)
    fetch = {"synchronize_session": "fetch"}
    for model in (RuleIndex, RuleObjectUsage, RuleIpRange):
        session.execute(delete(model).where(model.rule_id.in_(ids)).execution_options(**fetch))
    for model in (RuleEndpoint, RuleObjectRef):
        session.execute(delete(model).where(model.rule_id.in_(ids)).execution_options(**fetch))
    session.execute(delete(rule_service).where(rule_service.c.rule_id.in_(ids)))
    result = session.execute(delete(Rule).where(condition).execution_options(**fetch))
    session.flush()
    return result.rowcount or 0


def filtered(
    stmt,
    tenant_id,
    source_system_id,
    domain,
    src_zone: str | None = None,
    dst_zone: str | None = None,
    action=None,
):
    if tenant_id is not None:
        stmt = stmt.where(Rule.tenant_id == tenant_id)
    if source_system_id is not None:
        stmt = stmt.where(Rule.source_system_id == source_system_id)
    if domain is not None:
        stmt = stmt.where(Rule.domain == domain)
    for column, name in ((Rule.src_zone_id, src_zone), (Rule.dst_zone_id, dst_zone)):
        if name:
            stmt = stmt.where(column.in_(select(Zone.id).where(Zone.name == name)))
    if action is not None:
        stmt = stmt.where(Rule.action == action)
    return stmt


ORDER = (Rule.source_system_id, Rule.sequence, Rule.id)  # Reihenfolge wie auf dem Geraet


def paired_order(stmt):
    """Reihenfolge wie auf dem Geraet, IPv4/IPv6-Paare nebeneinander (0.36.0): die IPv6-Regel
    eines Paars (gleich, bestaetigt bzw. Vorschlag) steht direkt nach ihrer IPv4-Regel."""
    idx, partner = aliased(RuleIndex), aliased(Rule)
    return (
        stmt.outerjoin(idx, idx.rule_id == Rule.id)
        .outerjoin(
            partner,
            and_(
                partner.id == idx.pair_rule_id,
                Rule.ip_family == "v6",
                partner.ip_family == "v4",
                partner.source_system_id == Rule.source_system_id,
            ),
        )
        .order_by(
            Rule.source_system_id,
            func.coalesce(partner.sequence, Rule.sequence),
            func.coalesce(partner.id, Rule.id),
            partner.id.is_not(None),
            Rule.id,
        )
    )


def not_twin():
    """Ohne Zwillinge paralleler Firewalls (Verbund, 0.18.0): Listen und Kennzahlen zeigen je
    gleicher Policy nur die Regel der fuehrenden Firewall."""
    return ~exists(
        select(RuleIndex.rule_id).where(
            RuleIndex.rule_id == Rule.id, RuleIndex.twin_of.is_not(None)
        )
    )


def list_rules(
    session: Session,
    tenant_id: int | None = None,
    limit: int | None = 500,
    offset: int = 0,
    source_system_id: int | None = None,
    domain: RuleDomain | None = None,
    src_zone: str | None = None,
    dst_zone: str | None = None,
    action=None,
    rule_ids: list[int] | None = None,
) -> list[Rule]:
    """Regeln (Firewall-Policies und ACI-Contracts) gefiltert, seitenweise; `limit=None` = alle."""
    stmt = (
        select(Rule)
        .options(selectinload(Rule.endpoints), selectinload(Rule.services), selectinload(Rule.refs))
        .order_by(*ORDER)
    )
    stmt = filtered(stmt, tenant_id, source_system_id, domain, src_zone, dst_zone, action).offset(
        offset
    )
    if rule_ids is not None:
        stmt = stmt.where(Rule.id.in_(rule_ids))
    return list(session.scalars(stmt if limit is None else stmt.limit(limit)))


def count_rules(
    session: Session,
    tenant_id: int | None = None,
    source_system_id: int | None = None,
    domain: RuleDomain | None = None,
    src_zone: str | None = None,
    dst_zone: str | None = None,
    action=None,
) -> int:
    stmt = filtered(
        select(func.count(Rule.id)), tenant_id, source_system_id, domain, src_zone, dst_zone, action
    )
    return session.scalar(stmt) or 0


def summary(session: Session, tenant_id: int | None = None, only=None) -> dict:
    """Kennzahlen fuer die Uebersicht per SQL - unabhaengig von der Seitengroesse der Liste.
    Zwillinge paralleler Firewalls zaehlen nicht doppelt (0.18.0). `only` = Regel-IDs eines
    Kunden (Unterabfrage, ADR 0018)."""

    def mine(stmt):
        stmt = filtered(stmt, tenant_id, None, None)
        return stmt if only is None else stmt.where(Rule.id.in_(only))

    scoped = mine(select(Rule.id)).where(not_twin()).subquery()
    by_domain = {
        d.value: n
        for d, n in session.execute(
            mine(select(Rule.domain, func.count(Rule.id))).where(not_twin()).group_by(Rule.domain)
        )
    }
    systems = sorted(s for (s,) in session.execute(mine(select(Rule.source_system_id).distinct())))
    endpoints = session.scalar(
        select(func.count(RuleEndpoint.id)).where(RuleEndpoint.rule_id.in_(select(scoped.c.id)))
    )
    unmatched = session.scalar(
        select(func.count(RuleEndpoint.id))
        .join(NetworkObject, NetworkObject.id == RuleEndpoint.network_object_id)
        .where(
            RuleEndpoint.rule_id.in_(select(scoped.c.id)),
            NetworkObject.match_status == MatchStatus.UNMATCHED,
        )
    )
    zone_counts = list(
        session.execute(
            mine(select(Rule.dst_zone_id, func.count(Rule.id)))
            .where(not_twin())
            .group_by(Rule.dst_zone_id)
            .order_by(func.count(Rule.id).desc())
        )
    )
    src_counts = list(
        session.execute(
            mine(select(Rule.src_zone_id, func.count(Rule.id)))
            .where(not_twin())
            .group_by(Rule.src_zone_id)
            .order_by(func.count(Rule.id).desc())
        )
    )
    names = zone_names(session, {z for z, _ in zone_counts} | {z for z, _ in src_counts})
    actions = {
        a.value: n
        for a, n in session.execute(
            mine(select(Rule.action, func.count(Rule.id))).where(not_twin()).group_by(Rule.action)
        )
    }
    vdoms = session.execute(
        mine(select(Rule.vdom, func.count(Rule.id)))
        .where(not_twin())
        .where(Rule.vdom.is_not(None))
        .group_by(Rule.vdom)
        .order_by(Rule.vdom)
    )
    return {
        "by_src_zone": [(names.get(z, "–") if z else "–", n) for z, n in src_counts],
        "by_action": actions,
        "by_vdom": [(v, n) for v, n in vdoms],
        "total": sum(by_domain.values()),
        "by_domain": by_domain,
        "source_system_ids": systems,
        "endpoints": endpoints or 0,
        "unmatched": unmatched or 0,
        "by_dst_zone": [(names.get(z, "–") if z else "–", n) for z, n in zone_counts],
    }


def get_rule(session: Session, rule_id: int) -> Rule | None:
    stmt = (
        select(Rule)
        .options(selectinload(Rule.endpoints), selectinload(Rule.services), selectinload(Rule.refs))
        .where(Rule.id == rule_id)
    )
    return session.scalars(stmt).first()


def objects_by_ids(session: Session, ids: set[int]) -> dict[int, NetworkObject]:
    if not ids:
        return {}
    rows = session.scalars(select(NetworkObject).where(NetworkObject.id.in_(ids)))
    return {o.id: o for o in rows}


def zone_names(session: Session, ids: set[int]) -> dict[int, str]:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return {z.id: z.name for z in session.scalars(select(Zone).where(Zone.id.in_(ids)))}


def path_search(session: Session, rule: Rule) -> dict:
    """Weg-Suche fuer eine Regel (0.112.0, Anwender: „Knopf Wegsuche, damit ich einfach nach
    den Fällen suchen kann“): je Seite die Hosts bzw. Netze der Regel (ohne any) mit ihrem
    Objekt - der erste ist vorgewaehlt -, die Dienste wie in der Weg-Suche (tcp/443, icmp)."""
    sides: dict[str, list[dict]] = {"src": [], "dst": []}
    rows = session.execute(
        select(RuleIpRange.role, RuleIpRange.family, RuleIpRange.lo, RuleIpRange.hi,
               RuleIpRange.via)
        .where(RuleIpRange.rule_id == rule.id, RuleIpRange.is_any.is_(False))
        .order_by(RuleIpRange.id)
    )  # fmt: skip
    for role, family, lo, hi, via in rows:
        values = sides.get(role)
        value = _range_value(family, lo, hi)
        if values is None or value is None or len(values) >= PATH_SEARCH_MAX:
            continue
        if all(v["value"] != value for v in values):
            values.append({"value": value, "name": via if via and via != value else None})
    services = []
    for s in sorted(rule.services, key=lambda s: s.canonical_name or ""):
        text = _service_value(s)
        if text and text not in services:
            services.append(text)
    out = {"rule_id": rule.id, "name": rule.name, "src": sides["src"], "dst": sides["dst"],
           "services": services[:PATH_SEARCH_MAX], "src_exit": None, "start": None}  # fmt: skip
    if not sides["src"]:  # Quelle any (bzw. nur Laender, ISDB): Eingang der Regel (0.113.1)
        out.update(_ingress(session, rule))
    return out


def _ingress(session: Session, rule: Rule) -> dict:
    """Ohne bestimmte Quelle (Anwender 0.112: „hier jeweils die Quelle mindestens angeben“): die
    Anbindung am Eingang der Regel (Interface bzw. Zone in der Anbindung, fuer diese Firewall),
    sonst der Start an der Firewall der Regel (VDOM) - so geht die Weg-Suche bis zum Ziel, auch
    durch eine VIP (DNAT)."""
    from app.models import TopoContext, TopoExit  # spaet: Topologie gehoert zur Analyse

    if rule.domain is not RuleDomain.FIREWALL or rule.source_system_id is None:
        return {}
    ports = {str(x.get("name")) for x in (rule.raw or {}).get("srcintf") or []
             if isinstance(x, dict) and x.get("name")}  # fmt: skip
    if rule.src_zone_id is not None:
        zone = session.get(Zone, rule.src_zone_id)
        if zone is not None:
            ports.add(zone.name)
    ports.discard("any")
    sid = rule.source_system_id
    for e in session.scalars(select(TopoExit).where(TopoExit.tenant_id == rule.tenant_id)
                             .order_by(TopoExit.name, TopoExit.id)):  # fmt: skip
        if ports & set(e.interfaces or []) and (not e.source_system_ids
                                                or sid in e.source_system_ids):  # fmt: skip
            return {"src_exit": {"ref": f"exit:{e.id}@{sid}", "name": e.name, "kind": e.kind,
                                 "ports": sorted(ports & set(e.interfaces or []))}}  # fmt: skip
    contexts = list(session.scalars(
        select(TopoContext.context).where(TopoContext.source_system_id == sid,
                                          TopoContext.kind == "vdom").distinct()))  # fmt: skip
    context = rule.vdom or ("root" if "root" in contexts else contexts[0] if contexts else None)
    if context is None or context not in contexts:
        return {}
    return {"start": f"{sid}|{context}", "ports": sorted(ports)}


PATH_SEARCH_MAX = 20  # Werte je Seite zur Auswahl


def _range_value(family: int, lo: str, hi: str) -> str | None:
    """Bereich der Regel als IP bzw. Netz fuer die Weg-Suche (ein Bereich: sein erstes Netz)."""
    make = ipaddress.IPv6Address if family == 6 else ipaddress.IPv4Address
    try:
        first, last = make(int(lo, 16)), make(int(hi, 16))
    except (TypeError, ValueError):
        return None
    if first == last:
        return str(first)
    return str(next(ipaddress.summarize_address_range(first, last)))


def _service_value(s) -> str | None:
    """Dienst wie in der Weg-Suche: tcp/443, udp/53, tcp (alle Ports), icmp; alles = None."""
    proto = getattr(s.proto, "value", s.proto)
    if proto in ("tcp", "udp"):
        if s.port_start and (s.port_start > 1 or (s.port_end or 0) < 65535):
            return f"{proto}/{s.port_start}"
        return proto
    if proto in ("icmp", "icmpv6"):
        return "icmp"
    return None
