"""Findings-Store - Lesezugriff auf Datenqualitaets-/Reconciliation-Befunde.

Gefiltert (Status, Typ, Freitext ueber Objekt und Detail) und seitenweise; die Zaehler fuer
Filterleiste, Uebersicht und Navigation kommen per SQL (`summary`), nicht aus einer Liste.
"""

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.models import Finding, FindingStatus


def _filtered(stmt, tenant_id, status=None, ftype=None, q=None, only=None):
    if only is not None:  # Findings eines Kunden (ADR 0018)
        stmt = stmt.where(Finding.id.in_(only))
    if tenant_id is not None:
        stmt = stmt.where(Finding.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(Finding.status == status)
    if ftype:
        stmt = stmt.where(Finding.type == ftype)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Finding.subject_id).like(like),
                func.lower(cast(Finding.detail, String)).like(like),
            )
        )
    return stmt


def list_findings(
    session: Session,
    tenant_id: int | None = None,
    status: str | None = None,
    ftype: str | None = None,
    limit: int | None = 5000,
    offset: int = 0,
    q: str | None = None,
    only=None,
) -> list[Finding]:
    stmt = _filtered(select(Finding).order_by(Finding.id), tenant_id, status, ftype, q, only)
    stmt = stmt.offset(offset)
    return list(session.scalars(stmt if limit is None else stmt.limit(limit)))


def count_findings(
    session: Session, tenant_id=None, status=None, ftype=None, q=None, only=None
) -> int:
    stmt = _filtered(select(func.count(Finding.id)), tenant_id, status, ftype, q, only)
    return session.scalar(stmt) or 0


def summary(session: Session, tenant_id: int | None, ftype=None, q=None, only=None) -> dict:
    """Zaehler je Status (unter Typ/Suche) und je Typ (offen/gesamt) des Mandanten."""
    by_status = {
        s.value: n
        for s, n in session.execute(
            _filtered(
                select(Finding.status, func.count(Finding.id)), tenant_id, None, ftype, q, only
            ).group_by(Finding.status)
        )
    }
    by_type: dict[str, dict[str, int]] = {}
    for t, s, n in session.execute(
        _filtered(
            select(Finding.type, Finding.status, func.count(Finding.id)), tenant_id, only=only
        ).group_by(Finding.type, Finding.status)
    ):
        entry = by_type.setdefault(t.value, {"open": 0, "total": 0})
        entry["total"] += n
        if s is FindingStatus.OPEN:
            entry["open"] += n
    return {
        "total": sum(by_status.values()),
        "open": by_status.get(FindingStatus.OPEN.value, 0),
        "by_status": by_status,
        "by_type": by_type,
    }


def get_finding(session: Session, finding_id: int) -> Finding | None:
    return session.get(Finding, finding_id)
