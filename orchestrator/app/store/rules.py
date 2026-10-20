"""Rule & Service Store - Lesezugriff auf Regeln (ADR 0004).

Abfrage-Helfer fuer die API: Regelliste, Regel-Detail (inkl. Endpunkte/Services)
sowie Batch-Lookups fuer Netzwerkobjekte und Zonen, damit die Anreicherung der
Liste ohne N+1-Queries auskommt.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import NetworkObject, Rule, Zone


def list_rules(session: Session, tenant_id: int | None = None, limit: int = 500) -> list[Rule]:
    stmt = (
        select(Rule)
        .options(selectinload(Rule.endpoints), selectinload(Rule.services), selectinload(Rule.refs))
        .order_by(Rule.source_system_id, Rule.sequence, Rule.id)
    )
    if tenant_id is not None:
        stmt = stmt.where(Rule.tenant_id == tenant_id)
    return list(session.scalars(stmt.limit(limit)))


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
