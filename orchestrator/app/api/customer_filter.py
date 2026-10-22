"""Kunden-Filter fuer die Lese-APIs (ADR 0018 Schritt 2, 0.32.0).

`customer_id` waehlt den Ausschnitt eines Kunden samt geteiltem Teil; ohne `customer_id`
bleibt die ganze Umgebung sichtbar (Betreuer). Ein Kunde eines anderen Mandanten ist nicht
auffindbar (404).
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.auth.principal import Principal
from app.store import customer_scope


def customer_view(
    session: Session, principal: Principal, tenant_id: int | None, customer_id: int | None
) -> customer_scope.View | None:
    """Sicht des gewaehlten Kunden bzw. None (keine Einschraenkung)."""
    if customer_id is None:
        return None
    if tenant_id is None:
        raise HTTPException(status_code=422, detail="tenant_id required with customer_id")
    try:
        return customer_scope.view(session, tenant_id, [customer_id])
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="customer not found") from exc
