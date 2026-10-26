"""Publizierte Dienste (UC1): FortiGate-VIPs und die Regeln, die auf sie zeigen.

Zugeordnet werden die Regeln, deren Endpunkt auf das VIP-Objekt zeigt.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NatRule, NetworkObject, ObjectClass, RuleEndpoint


def vip_rule_ids(session: Session, tenant_id: int | None = None) -> dict[str, set[int]]:
    """VIP-/Dienstname -> Menge der Regel-IDs, die diesen VIP als Endpunkt referenzieren."""
    stmt = (
        select(NetworkObject.name, RuleEndpoint.rule_id)
        .join(RuleEndpoint, RuleEndpoint.network_object_id == NetworkObject.id)
        .where(NetworkObject.object_class == ObjectClass.VIP)
    )
    if tenant_id is not None:
        stmt = stmt.where(NetworkObject.tenant_id == tenant_id)
    out: dict[str, set[int]] = {}
    for name, rule_id in session.execute(stmt):
        out.setdefault(name, set()).add(rule_id)
    return out


def list_vips(session: Session, tenant_id: int | None = None) -> list[NatRule]:
    stmt = select(NatRule).order_by(NatRule.name)
    if tenant_id is not None:
        stmt = stmt.where(NatRule.tenant_id == tenant_id)
    return list(session.scalars(stmt))


def get_vip(session: Session, name: str, tenant_id: int | None = None) -> NatRule | None:
    stmt = select(NatRule).where(NatRule.name == name)
    if tenant_id is not None:
        stmt = stmt.where(NatRule.tenant_id == tenant_id)
    return session.scalars(stmt).first()
