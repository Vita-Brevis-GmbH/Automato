"""Quellsystem-Endpunkt: Liste der Systeme (fuer UI-Filter "Systeme")."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import require_viewer
from app.auth.principal import Principal
from app.db import get_session
from app.models import SourceSystem

router = APIRouter(prefix="/systems", tags=["systems"])


class SystemOut(BaseModel):
    id: int
    name: str
    platform: str
    enabled: bool


@router.get("", response_model=list[SystemOut])
def list_systems(
    _: Principal = Depends(require_viewer),
    session: Session = Depends(get_session),
):
    rows = session.scalars(select(SourceSystem).order_by(SourceSystem.id))
    return [
        SystemOut(id=s.id, name=s.name, platform=s.platform.value, enabled=s.enabled) for s in rows
    ]
