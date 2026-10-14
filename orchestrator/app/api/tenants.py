"""Mandanten-Endpunkte: Liste (UI-Switcher), Kuerzel pflegen, Standorte verwalten.

Ein Mandant (= Gruppengesellschaft) traegt ein optionales Kuerzel (`code`) und hat
einen oder mehrere **Standorte** mit dreistelligem Code (ADR 0004-Ergaenzung). Lesen
= `viewer`, Schreiben (Kuerzel/Standorte) = `admin`; alles mandantsgescoped. Der
Global-Admin sieht/verwaltet alle Mandanten.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.auth.deps import require_admin, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import Site, Tenant

router = APIRouter(prefix="/tenants", tags=["tenants"])

_SITE_CODE = r"^[0-9A-Za-z]{3}$"  # dreistelliger Standort-Code (SOP: numerisch, z. B. "000")


class TenantOut(BaseModel):
    id: int
    name: str
    code: str | None
    ipam_type: str
    dns_type: str


class TenantPatch(BaseModel):
    code: str | None = Field(default=None, max_length=20)


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
        ipam_type=t.ipam_type.value,
        dns_type=t.dns_type.value,
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


@router.patch("/{tenant_id}", response_model=TenantOut)
def update_tenant(
    tenant_id: int,
    body: TenantPatch,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session),
):
    tenant = _get_scoped_tenant(session, principal, tenant_id)
    tenant.code = body.code
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="tenant.updated",
        subject=str(tenant.id),
        detail={"tenant_id": tenant.id, "code": tenant.code},
    )
    return _tenant_out(tenant)


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
