"""Findings-Store - Lesezugriff auf Datenqualitaets-/Reconciliation-Befunde."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Finding


def list_findings(
    session: Session,
    tenant_id: int | None = None,
    status: str | None = None,
    ftype: str | None = None,
    limit: int = 5000,
) -> list[Finding]:
    stmt = select(Finding).order_by(Finding.id)
    if tenant_id is not None:
        stmt = stmt.where(Finding.tenant_id == tenant_id)
    if status is not None:
        stmt = stmt.where(Finding.status == status)
    if ftype is not None:
        stmt = stmt.where(Finding.type == ftype)
    return list(session.scalars(stmt.limit(limit)))


def get_finding(session: Session, finding_id: int) -> Finding | None:
    return session.get(Finding, finding_id)
