"""Quellsystem-Endpunkte: Liste, Anlegen, Ändern, Löschen.

Ein Quellsystem ist ein Zielsystem, aus dem gelesen wird (z. B. eine FortiGate), und gehört
zu genau einem Mandanten (Name je Mandant eindeutig). Lesen = `viewer` (mandantsgescoped),
Schreiben = `admin`. Die Zugangsdaten liegen nie hier, sondern beim Collector/Agenten
(ADR 0007/0008); `base_url` dient der Doku/Zuordnung.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import audit
from app.auth.deps import require_admin, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import NatRule, Rule, SourceSystem, Tenant
from app.models.source_system import Platform
from app.store import deletion

router = APIRouter(prefix="/systems", tags=["systems"])


class SystemOut(BaseModel):
    id: int
    tenant_id: int | None  # None = Altbestand, noch keinem Mandanten zugeordnet
    name: str
    platform: str
    enabled: bool
    base_url: str = ""
    rule_count: int = 0
    nat_count: int = 0


class SystemIn(BaseModel):
    tenant_id: int
    name: str = Field(min_length=1, max_length=200)
    platform: Platform
    base_url: str = Field(default="", max_length=500)


class SystemPatch(BaseModel):
    tenant_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None


class SystemDeleted(BaseModel):
    id: int
    rules: int
    nat_rules: int
    findings: int


def _counts(session: Session, model) -> dict[int, int]:
    rows = session.execute(
        select(model.source_system_id, func.count()).group_by(model.source_system_id)
    )
    return {sid: n for sid, n in rows}


def _out(s: SourceSystem, rules: dict[int, int], nat: dict[int, int]) -> SystemOut:
    return SystemOut(
        id=s.id,
        tenant_id=s.tenant_id,
        name=s.name,
        platform=s.platform.value,
        enabled=s.enabled,
        base_url=s.base_url,
        rule_count=rules.get(s.id, 0),
        nat_count=nat.get(s.id, 0),
    )


def _single_out(session: Session, s: SourceSystem) -> SystemOut:
    usage = deletion.source_usage(session, s.id)
    return _out(s, {s.id: usage["rules"]}, {s.id: usage["nat_rules"]})


def _get_scoped(session: Session, principal: Principal, system_id: int) -> SourceSystem:
    src = session.get(SourceSystem, system_id)
    if src is None:
        raise HTTPException(status_code=404, detail="source system not found")
    if src.tenant_id is not None:
        tenant_scope(principal, src.tenant_id)  # fremder Mandant -> 403
    elif not principal.is_admin:
        raise HTTPException(status_code=404, detail="source system not found")
    return src


def _name_taken(session: Session, tenant_id: int | None, name: str, exclude: int | None) -> bool:
    stmt = select(SourceSystem.id).where(
        SourceSystem.tenant_id == tenant_id, SourceSystem.name == name
    )
    if exclude is not None:
        stmt = stmt.where(SourceSystem.id != exclude)
    return session.scalar(stmt) is not None


@router.get("", response_model=list[SystemOut])
def list_systems(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session),
):
    """Quellsysteme (alphabetisch). Admin ohne Filter sieht alle inkl. nicht zugeordneter."""
    query = select(SourceSystem).order_by(SourceSystem.name, SourceSystem.id)
    if tenant_id is not None:
        tenant_scope(principal, tenant_id)
        query = query.where(SourceSystem.tenant_id == tenant_id)
    elif not principal.is_admin and principal.tenant_ids is not None:
        query = query.where(SourceSystem.tenant_id.in_(principal.tenant_ids))
    rules, nat = _counts(session, Rule), _counts(session, NatRule)
    return [_out(s, rules, nat) for s in session.scalars(query)]


@router.post("", response_model=SystemOut, status_code=201)
def create_system(
    body: SystemIn,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """Quellsystem für einen Mandanten anlegen - Admin. Name je Mandant eindeutig (409)."""
    tenant_scope(principal, body.tenant_id)
    if session.get(Tenant, body.tenant_id) is None:
        raise HTTPException(status_code=422, detail="tenant not found")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be empty")
    if _name_taken(session, body.tenant_id, name, None):
        raise HTTPException(status_code=409, detail="source system already exists for tenant")
    src = SourceSystem(
        tenant_id=body.tenant_id, name=name, platform=body.platform, base_url=body.base_url.strip()
    )
    session.add(src)
    session.flush()
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="source.created",
        subject=str(src.id),
        detail={
            "source_id": src.id,
            "tenant_id": src.tenant_id,
            "name": src.name,
            "platform": src.platform.value,
        },
    )
    return _out(src, {}, {})


@router.patch("/{system_id}", response_model=SystemOut)
def update_system(
    system_id: int,
    body: SystemPatch,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """Quellsystem ändern - Admin. Mandantenwechsel nur, wenn keine Daten eines anderen
    Mandanten daran hängen (sonst stimmten Regeln und Quellsystem nicht mehr überein)."""
    src = _get_scoped(session, principal, system_id)
    data = body.model_dump(exclude_unset=True)
    tenant_id = data.get("tenant_id", src.tenant_id)
    if "tenant_id" in data and tenant_id != src.tenant_id:
        if tenant_id is None:
            raise HTTPException(status_code=422, detail="tenant_id must not be null")
        tenant_scope(principal, tenant_id)
        if session.get(Tenant, tenant_id) is None:
            raise HTTPException(status_code=422, detail="tenant not found")
        foreign = or_(
            select(Rule.id)
            .where(Rule.source_system_id == src.id, Rule.tenant_id != tenant_id)
            .exists(),
            select(NatRule.id)
            .where(NatRule.source_system_id == src.id, NatRule.tenant_id != tenant_id)
            .exists(),
        )
        if session.scalar(select(foreign)):
            raise HTTPException(status_code=409, detail="source system has data of another tenant")
    name = (data.get("name") or src.name).strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be empty")
    if (name != src.name or tenant_id != src.tenant_id) and _name_taken(
        session, tenant_id, name, src.id
    ):
        raise HTTPException(status_code=409, detail="source system already exists for tenant")
    src.tenant_id = tenant_id
    src.name = name
    if data.get("base_url") is not None:
        src.base_url = data["base_url"].strip()
    if data.get("enabled") is not None:
        src.enabled = data["enabled"]
    session.flush()
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="source.updated",
        subject=str(src.id),
        detail={
            "source_id": src.id,
            "tenant_id": src.tenant_id,
            "name": src.name,
            "enabled": src.enabled,
        },
    )
    return _single_out(session, src)


@router.delete("/{system_id}", response_model=SystemDeleted)
def delete_system(
    system_id: int,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """Quellsystem löschen - Admin. Seine Regeln und NAT-Einträge werden mit gelöscht."""
    src = _get_scoped(session, principal, system_id)
    name, tenant_id = src.name, src.tenant_id
    counts = deletion.delete_source_system(session, src)
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="source.deleted",
        subject=str(system_id),
        detail={"source_id": system_id, "tenant_id": tenant_id, "name": name, **counts},
    )
    return SystemDeleted(id=system_id, **counts)
