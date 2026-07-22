"""Rule & Service Store - Lesezugriff auf Regeln (ADR 0004).

Grundaufbau des Store: Abfrage-Helfer fuer die API. Das Persistieren der
Collector-Ergebnisse in dieses Modell (DTO -> Regeln/Endpunkte/Services +
Matching -> Findings) folgt mit dem Durchstich (M7).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Rule


def list_rules(session: Session, tenant_id: int | None = None, limit: int = 500) -> list[Rule]:
    stmt = select(Rule).order_by(Rule.source_system_id, Rule.sequence)
    if tenant_id is not None:
        stmt = stmt.where(Rule.tenant_id == tenant_id)
    return list(session.scalars(stmt.limit(limit)))
