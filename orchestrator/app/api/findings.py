"""Findings-Endpunkt (ADR 0004).

Macht Datenqualitaets-/Reconciliation-Befunde read-only sichtbar (z. B. Objektname
in einer Regel, der nicht im SoT gefunden wurde). Quittieren/Bereinigen folgt mit
dem Schreibpfad.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.schemas.findings import FindingOut
from app.store import findings as findings_store

router = APIRouter(prefix="/findings", tags=["findings"])


@router.get("", response_model=list[FindingOut])
def list_findings(
    tenant_id: int | None = None,
    status: str | None = None,
    session: Session = Depends(get_session),
):
    return [
        FindingOut(
            id=f.id,
            tenant_id=f.tenant_id,
            type=f.type.value,
            severity=f.severity.value,
            status=f.status.value,
            subject_kind=f.subject_kind.value,
            subject_id=f.subject_id,
            detail=f.detail,
            note=f.note,
            first_seen=f.first_seen,
            last_seen=f.last_seen,
        )
        for f in findings_store.list_findings(session, tenant_id=tenant_id, status=status)
    ]
