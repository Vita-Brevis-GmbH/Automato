"""Findings-Endpunkt (ADR 0004).

Macht Datenqualitaets-/Reconciliation-Befunde sichtbar und **triagierbar**: Status
(open→acknowledged/resolved/ignored) + Notiz. Lesen = `viewer`, Triage = `operator`;
beides mandantsgescoped, Triage auditiert. Kunden-Benutzer triagieren mit der Kunden-Rolle
`triage` (bzw. hoeher) nur die Findings dieser Kunden (feinere Rollen, ADR 0018). Die
schreibende Bereinigung am Zielsystem (DDI/Regel) folgt mit dem Schreibpfad (ADR 0002).

Seit 0.109.0 auch **von Hand erfasst** (`POST /findings`, Operator, Typ `manual`): z. B. aus
„Warum dieser Weg?“, wenn etwas anzupassen ist - mit Titel, Text, Schweregrad und dem Weg; kein
Abgleich rechnet bzw. löscht ihn, er bleibt bis zur Triage (erledigt bzw. ignoriert).
"""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import audit
from app.api.customer_filter import (
    actor,
    capability_view,
    customer_view,
    finding_detail,
    own_view,
    visible,
)
from app.audit import reads
from app.auth import capabilities as caps
from app.auth.deps import require_operator, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import (
    Finding,
    FindingStatus,
    FindingType,
    Severity,
    SourceSystem,
    SubjectKind,
)
from app.schemas.findings import FindingOut
from app.store import finding_text
from app.store import findings as findings_store

router = APIRouter(prefix="/findings", tags=["findings"])


class FindingPatch(BaseModel):
    status: str | None = None
    note: str | None = None


class ManualFindingIn(BaseModel):
    """Von Hand erfasster Befund (0.109.0): Titel und Text frei, dazu Weg und Gerät."""

    tenant_id: int
    title: str = Field(min_length=3, max_length=300)
    text: str | None = Field(default=None, max_length=4000)
    severity: Literal["info", "warning", "error"] = "warning"
    source_system_id: int | None = None  # Gerät, an dem etwas anzupassen ist
    hop: str | None = Field(default=None, max_length=300)  # z. B. „fw-01 · VDOM root“
    src: str | None = Field(default=None, max_length=100)
    dst: str | None = Field(default=None, max_length=100)
    service: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=4000)


def _finding_out(f, principal: Principal | None = None) -> FindingOut:
    # Kunden-Benutzer: ohne Angaben zu Regeln ausserhalb ihres Ausschnitts (ADR 0018)
    detail = finding_detail(principal, f.detail) if principal is not None else f.detail
    message, hint = finding_text.explain(f.type.value, f.subject_id, detail)
    return FindingOut(
        id=f.id,
        tenant_id=f.tenant_id,
        type=f.type.value,
        severity=f.severity.value,
        status=f.status.value,
        subject_kind=f.subject_kind.value,
        subject_id=f.subject_id,
        source_system_id=f.source_system_id,
        detail=detail,
        message=message,
        hint=hint or None,
        note=f.note,
        first_seen=f.first_seen,
        last_seen=f.last_seen,
    )


class FindingSummaryOut(BaseModel):
    total: int
    open: int
    by_status: dict[str, int]  # unter Typ-/Suchfilter
    by_type: dict[str, dict[str, int]]  # je Typ {open, total} (ohne Filter)


@router.get("/summary", response_model=FindingSummaryOut)
def findings_summary(
    tenant_id: int | None = None,
    type: str | None = None,
    q: str | None = None,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    """Zaehler fuer Filterleiste, Uebersicht und Navigation (alle Findings, nicht eine Seite)."""
    tenant_id = tenant_scope(principal, tenant_id)
    view = customer_view(session, principal, tenant_id, customer_id)
    only = view.ids("finding") if view else None
    return findings_store.summary(session, tenant_id, ftype=type, q=q, only=only)


@router.get("", response_model=list[FindingOut])
def list_findings(
    response: Response,
    tenant_id: int | None = None,
    status: str | None = None,
    type: str | None = None,
    q: str | None = None,
    customer_id: int | None = None,
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    """Findings seitenweise; Gesamtzahl (nach Filtern) im Header `X-Total-Count`."""
    tenant_id = tenant_scope(principal, tenant_id)
    reads.maybe_audit_read(session, principal, tenant_id, "findings")
    view = customer_view(session, principal, tenant_id, customer_id)
    flt = {"tenant_id": tenant_id, "status": status, "ftype": type, "q": q,
           "only": view.ids("finding") if view else None}  # fmt: skip
    response.headers["X-Total-Count"] = str(findings_store.count_findings(session, **flt))
    rows = findings_store.list_findings(session, limit=limit, offset=offset, **flt)
    return [_finding_out(f, principal) for f in rows]


@router.post("", response_model=FindingOut, status_code=201)
def create_finding(
    body: ManualFindingIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
):
    """Befund von Hand erfassen (0.109.0, Operator; Kunden-Benutzer nicht): Typ `manual`, Subjekt
    der Weg (`Quelle → Ziel`) bzw. das Gerät; im Audit ohne Titel und Text."""
    tid = tenant_scope(principal, body.tenant_id)
    if principal.is_customer:
        raise HTTPException(status_code=403, detail="forbidden")
    if body.source_system_id is not None:
        src = session.get(SourceSystem, body.source_system_id)
        if src is None or src.tenant_id != tid:
            raise HTTPException(status_code=404, detail="source system not found")
    text = lambda v: (v or "").strip() or None  # noqa: E731
    title = text(body.title)
    if title is None or len(title) < 3:
        raise HTTPException(status_code=422, detail="title too short")
    path = f"{text(body.src) or '?'} → {text(body.dst) or '?'}" if body.src or body.dst else None
    subject = path or text(body.hop) or title
    detail = {k: v for k, v in {
        "title": title, "text": text(body.text), "src": text(body.src), "dst": text(body.dst),
        "service": text(body.service), "hop": text(body.hop), "by": principal.subject,
    }.items() if v}  # fmt: skip
    now = datetime.now(UTC)
    f = Finding(tenant_id=tid, type=FindingType.MANUAL, severity=Severity(body.severity),
                status=FindingStatus.OPEN,
                subject_kind=SubjectKind.PATH if path else SubjectKind.NODE,
                subject_id=subject[:300], source_system_id=body.source_system_id, detail=detail,
                note=text(body.note), first_seen=now, last_seen=now)  # fmt: skip
    session.add(f)
    session.flush()
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="finding.created",
        subject=str(f.id),
        detail={
            "finding_id": f.id,
            "tenant_id": tid,
            "type": f.type.value,
            "severity": f.severity.value,
            "source_system_id": f.source_system_id,
        },  # fmt: skip
    )
    return _finding_out(f, principal)


class FindingBulkTriage(BaseModel):
    """Mehrere Findings auf einmal triagieren (0.117.0, Anwender: „Multi-Checkbox für das
    Ignorieren“): Status (Vorgabe ignoriert) und optional eine Notiz fuer alle."""

    ids: list[int] = Field(min_length=1, max_length=5000)
    status: str = "ignored"
    note: str | None = Field(default=None, max_length=4000)


def _triageable(session: Session, principal: Principal, finding_id: int):
    """Finding, das dieser Benutzer triagieren darf - sonst 404/403 (wie bisher je Finding)."""
    f = findings_store.get_finding(session, finding_id)
    if f is None:
        raise HTTPException(status_code=404, detail="finding not found")
    tenant_scope(principal, f.tenant_id)  # Mandanten-Zugriff erzwingen (403 bei fremd)
    visible(session, own_view(session, principal, f.tenant_id), "finding", f.id)  # ADR 0018
    if principal.is_customer:  # feinere Rollen: Triage nur fuer Kunden mit dieser Rolle
        view = capability_view(session, principal, f.tenant_id, caps.TRIAGE)
        if view is None or not view.contains(session, "finding", f.id):
            raise HTTPException(status_code=403, detail="insufficient role")
    return f


def _status(value: str) -> FindingStatus:
    try:
        return FindingStatus(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid status") from exc


def _triaged(session: Session, principal: Principal, f, bulk: bool = False) -> None:
    extra = {"bulk": True} if bulk else {}
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="finding.triaged",
        subject=str(f.id),
        detail={"finding_id": f.id, "status": f.status.value, **extra, **actor(principal)},
    )


@router.post("/triage")
def triage_findings(
    body: FindingBulkTriage,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Status (und Notiz) fuer mehrere Findings - ganz oder gar nicht: jedes wird wie einzeln
    geprueft (Mandant, Kunde, Kunden-Rolle triage), sonst aendert sich keins; je Finding ein
    Audit-Eintrag."""
    status = _status(body.status)
    found = [_triageable(session, principal, i) for i in dict.fromkeys(body.ids)]
    for f in found:
        f.status = status
        if body.note is not None:
            f.note = body.note.strip() or None
        _triaged(session, principal, f, bulk=True)
    return {"updated": len(found), "status": status.value}


@router.patch("/{finding_id}", response_model=FindingOut)
def triage_finding(
    finding_id: int,
    body: FindingPatch,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
):
    f = _triageable(session, principal, finding_id)
    data = body.model_dump(exclude_unset=True)
    if "status" in data and data["status"] is not None:
        f.status = _status(data["status"])
    if "note" in data:
        f.note = data["note"]
    _triaged(session, principal, f)
    return _finding_out(f, principal)
