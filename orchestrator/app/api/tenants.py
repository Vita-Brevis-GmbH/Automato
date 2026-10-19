"""Mandanten-Endpunkte: Liste (UI-Switcher), Anlegen/Loeschen, Kuerzel pflegen, Standorte.

Ein Mandant (= Gruppengesellschaft) traegt ein optionales Kuerzel (`code`) und hat
einen oder mehrere **Standorte** mit dreistelligem Code (ADR 0004-Ergaenzung). Lesen
= `viewer`, Schreiben (Kuerzel/Standorte) = `admin`; alles mandantsgescoped. Der
Global-Admin sieht/verwaltet alle Mandanten.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.auth.deps import require_admin, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import NamingProfile, Site, Tenant
from app.models.tenant import DnsType, IpamType
from app.store import deletion

router = APIRouter(prefix="/tenants", tags=["tenants"])

_SITE_CODE = r"^[0-9A-Za-z]{3}$"  # dreistelliger Standort-Code (numerisch, z. B. "000")


class TenantOut(BaseModel):
    id: int
    name: str
    code: str | None
    audit_reads: bool
    ipam_type: str
    dns_type: str
    naming_profile_id: int | None = None  # None = Standardprofil „default"


class TenantIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=20)
    ipam_type: IpamType = IpamType.NETBOX
    dns_type: DnsType = DnsType.ACTIVE_DIRECTORY


class TenantPatch(BaseModel):
    code: str | None = Field(default=None, max_length=20)
    audit_reads: bool | None = None
    # Namensprofil zuordnen (null = zurueck zum Standardprofil „default")
    naming_profile_id: int | None = None


class SiteIn(BaseModel):
    code: str = Field(pattern=_SITE_CODE)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class SiteOut(BaseModel):
    id: int
    tenant_id: int
    code: str
    name: str
    description: str | None


def _tenant_out(t: Tenant) -> TenantOut:
    return TenantOut(
        id=t.id,
        name=t.name,
        code=t.code,
        audit_reads=t.audit_reads,
        ipam_type=t.ipam_type.value,
        dns_type=t.dns_type.value,
        naming_profile_id=t.naming_profile_id,
    )


def _site_out(s: Site) -> SiteOut:
    return SiteOut(
        id=s.id, tenant_id=s.tenant_id, code=s.code, name=s.name, description=s.description
    )


def _get_scoped_tenant(session: Session, principal: Principal, tenant_id: int) -> Tenant:
    """Mandanten-Zugriff erzwingen (403 bei fremd) und Existenz pruefen (404)."""
    tenant_scope(principal, tenant_id)
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    return tenant


@router.get("", response_model=list[TenantOut])
def list_tenants(
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session),
):
    rows = session.scalars(select(Tenant).order_by(Tenant.name))
    out = [_tenant_out(t) for t in rows]
    # Nicht-Admin sieht nur die ihm zugeordneten Mandanten (Global-Admin: alle).
    if not principal.is_admin and principal.tenant_ids is not None:
        allowed = set(principal.tenant_ids)
        out = [t for t in out if t.id in allowed]
    return out


@router.post("", response_model=TenantOut, status_code=201)
def create_tenant(
    body: TenantIn,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """Mandant (GG) anlegen - Global-Admin. Name eindeutig (409), Kuerzel optional."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be empty")
    if session.scalar(select(Tenant).where(Tenant.name == name)) is not None:
        raise HTTPException(status_code=409, detail="tenant already exists")
    tenant = Tenant(
        name=name,
        code=(body.code or "").strip() or None,
        ipam_type=body.ipam_type,
        dns_type=body.dns_type,
    )
    session.add(tenant)
    session.flush()
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="tenant.created",
        subject=str(tenant.id),
        detail={"tenant_id": tenant.id, "name": tenant.name, "code": tenant.code},
    )
    return _tenant_out(tenant)


@router.patch("/{tenant_id}", response_model=TenantOut)
def update_tenant(
    tenant_id: int,
    body: TenantPatch,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session),
):
    tenant = _get_scoped_tenant(session, principal, tenant_id)
    data = body.model_dump(exclude_unset=True)  # nur gesetzte Felder aendern
    if "code" in data:
        tenant.code = data["code"]
    if "audit_reads" in data and data["audit_reads"] is not None:
        tenant.audit_reads = data["audit_reads"]
    if "naming_profile_id" in data:
        pid = data["naming_profile_id"]
        if pid is not None and session.get(NamingProfile, pid) is None:
            raise HTTPException(status_code=422, detail="naming profile not found")
        tenant.naming_profile_id = pid
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="tenant.updated",
        subject=str(tenant.id),
        detail={
            "tenant_id": tenant.id,
            "code": tenant.code,
            "audit_reads": tenant.audit_reads,
            "naming_profile_id": tenant.naming_profile_id,
        },
    )
    return _tenant_out(tenant)


@router.delete("/{tenant_id}")
def delete_tenant(
    tenant_id: int,
    confirm: str = Query(description="Name des Mandanten zur Bestätigung (Schutz vor Versehen)"),
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, int]:
    """Mandant mit allen Daten löschen - Admin, nur mit `confirm=<Name>` (unumkehrbar)."""
    tenant = _get_scoped_tenant(session, principal, tenant_id)
    if confirm.strip() != tenant.name:
        raise HTTPException(status_code=422, detail="confirm must equal the tenant name")
    name = tenant.name
    counts = deletion.delete_tenant(session, tenant)
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="tenant.deleted",
        subject=str(tenant_id),
        detail={"tenant_id": tenant_id, "name": name, **counts},
    )
    return {"id": tenant_id, **counts}


@router.get("/{tenant_id}/sites", response_model=list[SiteOut])
def list_sites(
    tenant_id: int,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session),
):
    _get_scoped_tenant(session, principal, tenant_id)
    rows = session.scalars(select(Site).where(Site.tenant_id == tenant_id).order_by(Site.code))
    return [_site_out(s) for s in rows]


@router.post("/{tenant_id}/sites", response_model=SiteOut, status_code=201)
def create_site(
    tenant_id: int,
    body: SiteIn,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session),
):
    _get_scoped_tenant(session, principal, tenant_id)
    exists = session.scalars(
        select(Site).where(Site.tenant_id == tenant_id, Site.code == body.code)
    ).first()
    if exists is not None:
        raise HTTPException(status_code=409, detail="site code already exists for tenant")
    site = Site(tenant_id=tenant_id, code=body.code, name=body.name, description=body.description)
    session.add(site)
    session.flush()
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="site.created",
        subject=str(site.id),
        detail={"tenant_id": tenant_id, "code": body.code},
    )
    return _site_out(site)


@router.delete("/{tenant_id}/sites/{site_id}", status_code=204)
def delete_site(
    tenant_id: int,
    site_id: int,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session),
):
    _get_scoped_tenant(session, principal, tenant_id)
    site = session.get(Site, site_id)
    if site is None or site.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="site not found")
    session.delete(site)
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="site.deleted",
        subject=str(site_id),
        detail={"tenant_id": tenant_id, "site_id": site_id},
    )
