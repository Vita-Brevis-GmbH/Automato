"""Mandanten-Endpunkt: Liste fuer den UI-Mandanten-Switcher (read-only).

Der Global-Admin bekommt spaeter (RBAC, ADR 0007) die Gesamtsicht ueber alle
Mandanten; bis dahin dient die Liste nur dem Umschalten/Filtern im UI.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import require_viewer
from app.auth.principal import Principal
from app.db import get_session
from app.models import Tenant

router = APIRouter(prefix="/tenants", tags=["tenants"])


class TenantOut(BaseModel):
    id: int
    name: str
    ipam_type: str
    dns_type: str


@router.get("", response_model=list[TenantOut])
def list_tenants(
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session),
):
    rows = session.scalars(select(Tenant).order_by(Tenant.name))
    out = [
        TenantOut(id=t.id, name=t.name, ipam_type=t.ipam_type.value, dns_type=t.dns_type.value)
        for t in rows
    ]
    # Nicht-Admin sieht nur die ihm zugeordneten Mandanten (Global-Admin: alle).
    if not principal.is_admin and principal.tenant_ids is not None:
        allowed = set(principal.tenant_ids)
        out = [t for t in out if t.id in allowed]
    return out
