"""Findings-Store - Lesezugriff auf Datenqualitaets-/Reconciliation-Befunde."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Finding


def list_findings(
    session: Session,
    tenant_id: int | None = None,
    status: str | None = None,
    limit: int = 500,
) -> list[Finding]:
    stmt = select(Finding).order_by(Finding.id)
    if tenant_id is not None:
        stmt = stmt.where(Finding.tenant_id == tenant_id)
    if status is not None:
        stmt = stmt.where(Finding.status == status)
    return list(session.scalars(stmt.limit(limit)))
