"""Regel-Endpunkte (ADR 0004).

Liest Regeln aus dem Rule & Service Store. Bis Collectors Regeln persistieren
(M7) ist die Liste leer; der Router und der API-Vertrag stehen aber bereits,
damit das Web-UI andocken kann. Filter nach System/Netzwerk/Zone folgen mit dem UI-Ausbau.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.schemas.rules import RuleOut
from app.store import rules as rules_store

router = APIRouter(prefix="/rules", tags=["rules"])


@router.get("", response_model=list[RuleOut])
def list_rules(tenant_id: int | None = None, session: Session = Depends(get_session)):
    return [
        RuleOut(
            id=r.id,
            tenant_id=r.tenant_id,
            source_system_id=r.source_system_id,
            domain=r.domain,
            name=r.name,
            action=r.action,
            section_label=r.section_label,
        )
        for r in rules_store.list_rules(session, tenant_id=tenant_id)
    ]
