"""Abgleich-Endpunkte (ADR 0010).

- `GET /reconcile/summary`  Befunde je Typ, Referenzquellen des Mandanten und Kennzahlen des
                            Vergleichs fuehrendes IPAM ↔ Anreicherung (DDI ↔ NetBox).
- `GET /reconcile/ipam`     Vergleich DDI ↔ NetBox zeilenweise (fehlt / abweichend / nur in
                            der Anreicherung), filter- und blaetterbar.
- `POST /reconcile`         Abgleich jetzt neu berechnen (operator), auditiert.
Lesen = `viewer`, mandantsgescoped. Gegenueber den Quellsystemen rein lesend.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.api.customer_filter import customer_view
from app.auth.deps import require_operator, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import Finding, FindingStatus, SourceSystem
from app.store import customer_infra, customers, reconcile, references, rule_index

router = APIRouter(prefix="/reconcile", tags=["reconcile"])


def _tenant(principal: Principal, tenant_id: int | None) -> int:
    tid = tenant_scope(principal, tenant_id)
    if tid is None:
        raise HTTPException(status_code=422, detail="tenant_id required")
    return tid


def _keep(session: Session, principal: Principal, tid: int, customer_id: int | None):
    """Kunde oben gewaehlt (0.76.0): Werte in seinen Netzen (eigen bzw. geteilt)."""
    infra = customer_infra.of(session, customer_view(session, principal, tid, customer_id))
    if infra is None:
        return None, None
    return infra, (lambda value: infra.net_mark(value) is not None)


@router.get("/summary")
def summary(
    tenant_id: int | None = None,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    tid = _tenant(principal, tenant_id)
    infra, keep = _keep(session, principal, tid, customer_id)
    by_type: dict[str, dict[str, int]] = {}
    stmt = (
        select(Finding.type, Finding.status, func.count())
        .where(Finding.tenant_id == tid)
        .group_by(Finding.type, Finding.status)
    )
    if infra is not None:  # Findings des Kunden (ueber ihr Subjekt, ADR 0018)
        stmt = stmt.where(Finding.id.in_(infra.view.ids("finding")))
    for ftype, status, n in session.execute(stmt):
        entry = by_type.setdefault(ftype.value, {"open": 0, "total": 0})
        entry["total"] += n
        if status is FindingStatus.OPEN:
            entry["open"] += n
    sources = [
        {
            "id": s.id,
            "name": s.name,
            "platform": s.platform.value,
            "ref_role": s.ref_role,
            "has_reference": bool(s.reference_run_id),
        }
        for s in session.scalars(
            select(SourceSystem).where(
                SourceSystem.tenant_id == tid, SourceSystem.ref_role.is_not(None)
            )
        )
    ]
    ipam = references.compare_sources(session, tid, limit=0, keep=keep)
    return {
        "tenant_id": tid,
        "findings": by_type,
        "reference_sources": sources,
        "ipam": {
            k: ipam[k] for k in ("available", "primary", "enrichment", "summary") if k in ipam
        },
    }


@router.get("/ipam")
def ipam_compare(
    tenant_id: int | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    tid = _tenant(principal, tenant_id)
    if status is not None and status not in references.COMPARE_STATUSES:
        raise HTTPException(status_code=422, detail="invalid status")
    _, keep = _keep(session, principal, tid, customer_id)
    return references.compare_sources(
        session, tid, status, q, min(limit, 2000), max(offset, 0), keep=keep
    )


@router.post("")
def run_reconcile(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    tid = _tenant(principal, tenant_id)
    counts = reconcile.reconcile_tenant(session, tid)
    rule_index.rebuild(session, tid)  # Werte/DNS-Namen aus dem IPAM im Suchtext
    reconcile.refresh_app_findings(session, tid)  # Regel <-> Weg (0.59.0), mit dem Index
    customers.derive(session, tid)  # Findings gehoeren Kunden ueber ihr Subjekt (ADR 0018)
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="reconcile.run",
        subject=str(tid),
        detail={"tenant_id": tid, "findings": sum(counts.values())},
    )
    return {"tenant_id": tid, "findings": counts}
