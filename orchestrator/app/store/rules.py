"""Rule & Service Store - Lesezugriff auf Regeln (ADR 0004).

Abfrage-Helfer fuer die API: Regelliste, Regel-Detail (inkl. Endpunkte/Services)
sowie Batch-Lookups fuer Netzwerkobjekte und Zonen, damit die Anreicherung der
Liste ohne N+1-Queries auskommt.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import MatchStatus, NetworkObject, Rule, RuleDomain, RuleEndpoint, Zone


def _filtered(stmt, tenant_id, source_system_id, domain):
    if tenant_id is not None:
        stmt = stmt.where(Rule.tenant_id == tenant_id)
    if source_system_id is not None:
        stmt = stmt.where(Rule.source_system_id == source_system_id)
    if domain is not None:
        stmt = stmt.where(Rule.domain == domain)
    return stmt


def list_rules(
    session: Session,
    tenant_id: int | None = None,
    limit: int | None = 500,
    offset: int = 0,
    source_system_id: int | None = None,
    domain: RuleDomain | None = None,
) -> list[Rule]:
    """Regeln (Firewall-Policies und ACI-Contracts) gefiltert, seitenweise; `limit=None` = alle."""
    stmt = (
        select(Rule)
        .options(selectinload(Rule.endpoints), selectinload(Rule.services), selectinload(Rule.refs))
        .order_by(Rule.source_system_id, Rule.sequence, Rule.id)
    )
    stmt = _filtered(stmt, tenant_id, source_system_id, domain).offset(offset)
    return list(session.scalars(stmt if limit is None else stmt.limit(limit)))


def count_rules(
    session: Session,
    tenant_id: int | None = None,
    source_system_id: int | None = None,
    domain: RuleDomain | None = None,
) -> int:
    stmt = _filtered(select(func.count(Rule.id)), tenant_id, source_system_id, domain)
    return session.scalar(stmt) or 0


def summary(session: Session, tenant_id: int | None = None) -> dict:
    """Kennzahlen fuer die Uebersicht per SQL - unabhaengig von der Seitengroesse der Liste."""
    scoped = _filtered(select(Rule.id), tenant_id, None, None).subquery()
    by_domain = {
        d.value: n
        for d, n in session.execute(
            _filtered(select(Rule.domain, func.count(Rule.id)), tenant_id, None, None).group_by(
                Rule.domain
            )
        )
    }
    systems = sorted(
        s
        for (s,) in session.execute(
            _filtered(select(Rule.source_system_id).distinct(), tenant_id, None, None)
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
            _filtered(select(Rule.dst_zone_id, func.count(Rule.id)), tenant_id, None, None)
            .group_by(Rule.dst_zone_id)
            .order_by(func.count(Rule.id).desc())
        )
    )
    names = zone_names(session, {z for z, _ in zone_counts})
    return {
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
