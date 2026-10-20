"""Findings-Endpunkt (ADR 0004).

Macht Datenqualitaets-/Reconciliation-Befunde sichtbar und **triagierbar**: Status
(open→acknowledged/resolved/ignored) + Notiz. Lesen = `viewer`, Triage = `operator`;
beides mandantsgescoped, Triage auditiert. Die schreibende Bereinigung am Zielsystem
(DDI/Regel) folgt mit dem Schreibpfad (ADR 0002).
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import audit
from app.audit import reads
from app.auth.deps import require_operator, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import FindingStatus
from app.schemas.findings import FindingOut
from app.store import findings as findings_store

router = APIRouter(prefix="/findings", tags=["findings"])


class FindingPatch(BaseModel):
    status: str | None = None
    note: str | None = None


def _finding_out(f) -> FindingOut:
    return FindingOut(
        id=f.id,
        tenant_id=f.tenant_id,
        type=f.type.value,
        severity=f.severity.value,
        status=f.status.value,
        subject_kind=f.subject_kind.value,
        subject_id=f.subject_id,
        source_system_id=f.source_system_id,
        detail=f.detail,
        note=f.note,
        first_seen=f.first_seen,
        last_seen=f.last_seen,
    )


@router.get("", response_model=list[FindingOut])
def list_findings(
    tenant_id: int | None = None,
    status: str | None = None,
    type: str | None = None,
    limit: int = 5000,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session),
):
    tenant_id = tenant_scope(principal, tenant_id)
    reads.maybe_audit_read(session, principal, tenant_id, "findings")
    rows = findings_store.list_findings(
        session, tenant_id=tenant_id, status=status, ftype=type, limit=min(limit, 20000)
    )
    return [_finding_out(f) for f in rows]


@router.patch("/{finding_id}", response_model=FindingOut)
def triage_finding(
    finding_id: int,
    body: FindingPatch,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session),
):
    f = findings_store.get_finding(session, finding_id)
    if f is None:
        raise HTTPException(status_code=404, detail="finding not found")
    tenant_scope(principal, f.tenant_id)  # Mandanten-Zugriff erzwingen (403 bei fremd)

    data = body.model_dump(exclude_unset=True)
    if "status" in data and data["status"] is not None:
        try:
            f.status = FindingStatus(data["status"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid status") from exc
    if "note" in data:
        f.note = data["note"]
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="finding.triaged",
        subject=str(f.id),
        detail={"finding_id": f.id, "status": f.status.value},
    )
    return _finding_out(f)
