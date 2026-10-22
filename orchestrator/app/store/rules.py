"""Rule & Service Store - Lesezugriff auf Regeln (ADR 0004).

Abfrage-Helfer fuer die API: Regelliste, Regel-Detail (inkl. Endpunkte/Services)
sowie Batch-Lookups fuer Netzwerkobjekte und Zonen, damit die Anreicherung der
Liste ohne N+1-Queries auskommt.
"""

from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session, selectinload

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


def summary(session: Session, tenant_id: int | None = None) -> dict:
    """Kennzahlen fuer die Uebersicht per SQL - unabhaengig von der Seitengroesse der Liste.
    Zwillinge paralleler Firewalls zaehlen nicht doppelt (0.18.0)."""
    scoped = filtered(select(Rule.id), tenant_id, None, None).where(not_twin()).subquery()
    by_domain = {
        d.value: n
        for d, n in session.execute(
            filtered(select(Rule.domain, func.count(Rule.id)), tenant_id, None, None)
            .where(not_twin())
            .group_by(Rule.domain)
        )
    }
    systems = sorted(
        s
        for (s,) in session.execute(
            filtered(select(Rule.source_system_id).distinct(), tenant_id, None, None)
        )
    )
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
            filtered(select(Rule.dst_zone_id, func.count(Rule.id)), tenant_id, None, None)
            .where(not_twin())
            .group_by(Rule.dst_zone_id)
            .order_by(func.count(Rule.id).desc())
        )
    )
    src_counts = list(
        session.execute(
            filtered(select(Rule.src_zone_id, func.count(Rule.id)), tenant_id, None, None)
            .where(not_twin())
            .group_by(Rule.src_zone_id)
            .order_by(func.count(Rule.id).desc())
        )
    )
    names = zone_names(session, {z for z, _ in zone_counts} | {z for z, _ in src_counts})
    actions = {
        a.value: n
        for a, n in session.execute(
            filtered(select(Rule.action, func.count(Rule.id)), tenant_id, None, None)
            .where(not_twin())
            .group_by(Rule.action)
        )
    }
    vdoms = session.execute(
        filtered(select(Rule.vdom, func.count(Rule.id)), tenant_id, None, None)
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
