"""Module je Mandant (0.107.0, ADR 0036): lesen = `viewer` (die Oberflaeche blendet danach aus),
schalten = `admin` mit Audit. Kern und Einstellungen sind immer an; ein Modul mit einer
ausgeschalteten Abhaengigkeit ist auch aus (`effective`)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import audit, modules
from app.auth.deps import require_admin, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import Tenant

router = APIRouter(prefix="/modules", tags=["modules"])


class ModuleOut(BaseModel):
    key: str
    title: str
    purpose: str
    role: str  # basis | lesen | schreiben
    switchable: bool
    depends: list[str]
    views: list[str]
    enabled: bool  # eigene Schaltung
    effective: bool  # mit den Abhaengigkeiten


class ModuleSwitch(BaseModel):
    enabled: bool


def _out(session: Session, tenant_id: int | None) -> list[ModuleOut]:
    eff = modules.states(session, tenant_id)
    return [
        ModuleOut(**m.out(), effective=eff[m.key],
                  enabled=modules.own_state(session, tenant_id, m.key) if tenant_id else True)
        for m in modules.MODULES
    ]  # fmt: skip


@router.get("", response_model=list[ModuleOut])
def list_modules(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    """Module mit ihrem Stand fuer den Mandanten (ohne Mandant: alle an)."""
    return _out(session, tenant_scope(principal, tenant_id))


@router.put("/{key}", response_model=list[ModuleOut])
def switch_module(
    key: str,
    body: ModuleSwitch,
    tenant_id: int,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
):
    tid = tenant_scope(principal, tenant_id)
    if tid is None or session.get(Tenant, tid) is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    before = modules.states(session, tid)
    try:
        modules.switch(session, tid, key, body.enabled, f"user:{principal.subject}")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="module not found") from exc
    except modules.Invalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    after = modules.states(session, tid)
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="module.switched",
        subject=key,
        detail={"tenant_id": tid, "module": key, "enabled": body.enabled,
                "changed": sorted(k for k in after if after[k] != before[k])},
    )  # fmt: skip
    return _out(session, tid)
