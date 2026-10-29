"""Prüfungen der Findings je Mandant (0.110.0, ADR 0039): lesen = `viewer` (mit Dauer und Anzahl
des letzten Durchgangs), schalten = `admin` mit Audit. Eine ausgeschaltete Prüfung läuft nicht;
ihre Befunde fallen beim nächsten Abgleich weg. Ist ihr Modul aus, läuft sie auch nicht
(`effective`)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import audit, checks, modules
from app.auth.deps import require_admin, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.i18n import L
from app.models import Tenant

router = APIRouter(prefix="/checks", tags=["checks"])


class CheckStats(BaseModel):
    ran_at: str
    ms: int
    count: int


class CheckOut(BaseModel):
    key: str
    module: str
    module_title: str
    title: str
    purpose: str
    types: list[str]
    step: str | None  # eigener Teil-Durchgang (zonen, quellen, applikationen)
    in_reconcile: bool
    enabled: bool  # eigene Schaltung
    effective: bool  # mit dem Modul
    stats: CheckStats | None  # letzter Durchgang in diesem Prozess


class CheckSwitch(BaseModel):
    enabled: bool


def _out(session: Session, tenant_id: int | None) -> list[CheckOut]:
    out = []
    for c in checks.all_checks():
        stats = checks.stats(tenant_id, c.key) if tenant_id is not None else None
        out.append(CheckOut(
            **c.out(), module_title=L(*modules.BY_KEY[c.module].title),
            enabled=checks.own_state(session, tenant_id, c.key),
            effective=checks.enabled(session, tenant_id, c),
            stats=CheckStats(**stats) if stats else None,
        ))  # fmt: skip
    return out


@router.get("", response_model=list[CheckOut])
def list_checks(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    """Alle Prüfungen mit ihrem Stand für den Mandanten (ohne Mandant: alle an)."""
    return _out(session, tenant_scope(principal, tenant_id))


@router.put("/{key}", response_model=list[CheckOut])
def switch_check(
    key: str,
    body: CheckSwitch,
    tenant_id: int,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
):
    tid = tenant_scope(principal, tenant_id)
    if tid is None or session.get(Tenant, tid) is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    try:
        checks.switch(session, tid, key, body.enabled, f"user:{principal.subject}")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="check not found") from exc
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="check.switched",
        subject=key,
        detail={"tenant_id": tid, "check": key, "enabled": body.enabled},
    )
    return _out(session, tid)
