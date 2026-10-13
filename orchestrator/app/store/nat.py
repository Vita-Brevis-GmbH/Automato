"""NAT-/VIP-Store: Lesezugriff auf nat_rule."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NatRule


def list_nat(session: Session, tenant_id: int | None = None, limit: int = 500) -> list[NatRule]:
    stmt = select(NatRule).order_by(NatRule.source_system_id, NatRule.name)
    if tenant_id is not None:
        stmt = stmt.where(NatRule.tenant_id == tenant_id)
    return list(session.scalars(stmt.limit(limit)))


def nat_by_name(session: Session, tenant_id: int) -> dict[str, NatRule]:
    """VIP-/NAT-Name -> NatRule (fuer die Anreicherung der Regel-Detailansicht)."""
    rows = session.scalars(select(NatRule).where(NatRule.tenant_id == tenant_id))
    return {n.name: n for n in rows}


def nat_target(nat: NatRule) -> str:
    """Kompakte Ziel-Darstellung: mapped_ip[:port][/proto]."""
    target = nat.mapped_ip or "?"
    if nat.mapped_port:
        target += f":{nat.mapped_port}"
    if nat.protocol:
        target += f"/{nat.protocol}"
    return target
