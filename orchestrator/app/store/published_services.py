"""Publizierte Dienste (UC1): VIPs der FortiGate bzw. des NSX ALB und die Regeln, die auf sie
zeigen.

Zugeordnet werden die Regeln, deren Endpunkt auf das VIP-Objekt zeigt; beim NSX ALB (ADR 0027,
kein Objekt auf der Firewall) die Regeln mit der VIP-Adresse als Host im Endpunkt.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NatRule, NetworkObject, ObjectClass, Platform, RuleEndpoint, SourceSystem


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


def ip_rule_ids(
    session: Session, ips: set[str], tenant_id: int | None = None
) -> dict[str, set[int]]:
    """VIP-Adresse -> Regeln mit dieser Adresse als Host im Endpunkt (NSX ALB, ADR 0027)."""
    if not ips:
        return {}
    values = {*ips, *(f"{ip}/{128 if ':' in ip else 32}" for ip in ips)}
    stmt = (
        select(NetworkObject.value, RuleEndpoint.rule_id)
        .join(RuleEndpoint, RuleEndpoint.network_object_id == NetworkObject.id)
        .where(NetworkObject.value.in_(values))
    )
    if tenant_id is not None:
        stmt = stmt.where(NetworkObject.tenant_id == tenant_id)
    out: dict[str, set[int]] = {}
    for value, rule_id in session.execute(stmt):
        out.setdefault(value.split("/")[0], set()).add(rule_id)
    return out


def platforms(session: Session, tenant_id: int | None = None) -> dict[int, tuple[str, str]]:
    """Quellsystem -> (Name, Plattform) fuer die Spalte Quelle."""
    stmt = select(SourceSystem.id, SourceSystem.name, SourceSystem.platform)
    if tenant_id is not None:
        stmt = stmt.where(SourceSystem.tenant_id == tenant_id)
    return {sid: (name, str(platform)) for sid, name, platform in session.execute(stmt)}


def is_balancer(platform: str | None) -> bool:
    return platform == Platform.AVI.value


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
