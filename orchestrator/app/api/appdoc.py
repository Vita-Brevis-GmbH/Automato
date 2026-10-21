"""Applikations-Doku (UC1, systemzentriert): die 10 Kategorien je System.

Liefert pro System (Host) die nach Richtung/Scope/Enforcement gruppierten Tabellen
(`docs/usecases/uc1-applikations-doku-spec.md`). Lesen = `viewer`, mandantsgescoped.
Der Markdown-Export wird clientseitig aus dieser Struktur erzeugt.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import audit
from app.audit import reads
from app.auth.deps import require_operator, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.store import appdoc as store

router = APIRouter(prefix="/appdoc", tags=["appdoc"])


class SystemRefOut(BaseModel):
    name: str
    ip: str | None
    rule_count: int
    kind: str


class DocTableOut(BaseModel):
    index: int
    title: str
    enforcement: str
    columns: list[str]
    # Spalten als Text; dazu `_rule` (Regel-ID), `_dir` (in | out) und `_scope` (Art des Bezugs)
    rows: list[dict[str, str | int | None]]


class SiteRef(BaseModel):
    id: int
    code: str
    name: str
    kind: str = "virtual"  # virtual | physical (ADR 0015)
    physical: list[str] = []  # virtuell: Codes der physischen Standorte
    virtual: list[str] = []  # physisch: Codes der virtuellen Standorte


class DocVmOut(BaseModel):
    """VM hinter dem System (vCenter, ADR 0017): beim Server die VM der IP, bei der EPG die VMs
    an ihren Portgruppen."""

    name: str
    power_state: str | None = None
    portgroup: str | None = None
    ips: list[str] = []
    epg: str | None = None


class SystemDocOut(BaseModel):
    system: str
    system_ip: str | None
    tenant_code: str | None
    site: SiteRef | None
    vms: list[DocVmOut] = []
    tables: list[DocTableOut]


class SiteAssign(BaseModel):
    site_id: int | None = None


@router.get("/systems", response_model=list[SystemRefOut])
def list_systems(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    tenant_id = tenant_scope(principal, tenant_id)
    reads.maybe_audit_read(session, principal, tenant_id, "appdoc")
    return [
        SystemRefOut(name=s.name, ip=s.ip, rule_count=s.rule_count, kind=s.kind)
        for s in store.list_systems(session, tenant_id)
    ]


@router.get("/systems/{system}", response_model=SystemDocOut)
def system_doc(
    system: str,
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    tenant_id = tenant_scope(principal, tenant_id)
    reads.maybe_audit_read(session, principal, tenant_id, "appdoc")
    doc = store.build_doc(session, system, tenant_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="system not found")
    return doc


@router.patch("/systems/{system}/site", response_model=SystemDocOut)
def assign_system_site(
    system: str,
    body: SiteAssign,
    tenant_id: int | None = None,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
):
    tenant_id = tenant_scope(principal, tenant_id)
    try:
        obj = store.assign_site(session, system, tenant_id, body.site_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if obj is None:
        raise HTTPException(status_code=404, detail="system not found")
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="system.site_assigned",
        subject=system,
        detail={"tenant_id": obj.tenant_id, "site_id": body.site_id},
    )
    return store.build_doc(session, system, tenant_id)
