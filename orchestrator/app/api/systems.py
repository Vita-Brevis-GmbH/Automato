"""Quellsystem-Endpunkte: Liste (UI-Filter "Systeme") und Anlegen (Admin).

Ein Quellsystem ist ein Zielsystem, aus dem gelesen wird (z. B. eine FortiGate). Die
Zugangsdaten liegen nie hier, sondern beim Collector/Agenten (ADR 0007/0008); `base_url`
dient der Doku/Zuordnung.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.auth.deps import require_admin, require_viewer
from app.auth.principal import Principal
from app.db import get_session
from app.models import SourceSystem
from app.models.source_system import Platform

router = APIRouter(prefix="/systems", tags=["systems"])


class SystemOut(BaseModel):
    id: int
    name: str
    platform: str
    enabled: bool
    base_url: str = ""


class SystemIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    platform: Platform
    base_url: str = Field(default="", max_length=500)


def _out(s: SourceSystem) -> SystemOut:
    return SystemOut(
        id=s.id, name=s.name, platform=s.platform.value, enabled=s.enabled, base_url=s.base_url
    )


@router.get("", response_model=list[SystemOut])
def list_systems(
    _: Principal = Depends(require_viewer),
    session: Session = Depends(get_session),
):
    rows = session.scalars(select(SourceSystem).order_by(SourceSystem.id))
    return [_out(s) for s in rows]


@router.post("", response_model=SystemOut, status_code=201)
def create_system(
    body: SystemIn,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """Quellsystem anlegen - Admin. Name eindeutig (409); keine Zugangsdaten."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be empty")
    if session.scalar(select(SourceSystem).where(SourceSystem.name == name)) is not None:
        raise HTTPException(status_code=409, detail="source system already exists")
    src = SourceSystem(name=name, platform=body.platform, base_url=body.base_url.strip())
    session.add(src)
    session.flush()
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="source.created",
        subject=str(src.id),
        detail={"source_id": src.id, "name": src.name, "platform": src.platform.value},
    )
    return _out(src)
