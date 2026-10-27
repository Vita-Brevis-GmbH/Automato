"""Kunden-Filter und -Einschraenkung fuer die Lese-APIs (ADR 0018 Schritte 2 und 3).

- **Betreuer:** `customer_id` waehlt den Ausschnitt eines Kunden samt geteiltem Teil; ohne
  bleibt die ganze Umgebung sichtbar. Ein Kunde eines anderen Mandanten ist nicht
  auffindbar (404).
- **Kunden-Benutzer** (0.33.0): immer der Ausschnitt ihrer Kunden; ein anderer `customer_id`
  ergibt 403, ohne zugeordnete Kunden sehen sie nichts (403, fail-closed). Details ausserhalb
  ihres Ausschnitts sind nicht auffindbar (404), Zaehler und Regel-Listen nennen nur ihre
  eigenen Regeln.
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.auth.principal import Principal
from app.store import customer_scope

_FINDING_DETAIL_HIDDEN = customer_scope.FINDING_DETAIL_HIDDEN


def customer_view(
    session: Session, principal: Principal, tenant_id: int | None, customer_id: int | None
) -> customer_scope.View | None:
    """Sicht des gewaehlten Kunden (Betreuer) bzw. der eigenen Kunden (Kunden-Benutzer);
    None = keine Einschraenkung."""
    if principal.is_customer:
        if tenant_id is None:
            raise HTTPException(status_code=403, detail="tenant required")
        allowed = principal.customer_ids or ()
        if customer_id is not None and customer_id not in allowed:
            raise HTTPException(status_code=403, detail="customer not allowed")
        try:
            return customer_scope.view(
                session, tenant_id, [customer_id] if customer_id is not None else list(allowed)
            )
        except LookupError as exc:
            raise HTTPException(status_code=403, detail="no customer assigned") from exc
    if customer_id is None:
        return None
    if tenant_id is None:
        raise HTTPException(status_code=422, detail="tenant_id required with customer_id")
    try:
        return customer_scope.view(session, tenant_id, [customer_id])
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="customer not found") from exc


def own_view(session: Session, principal: Principal, tenant_id: int) -> customer_scope.View | None:
    """Einschraenkung fuer Details: nur bei Kunden-Benutzern."""
    return customer_view(session, principal, tenant_id, None) if principal.is_customer else None


def capability_view(
    session: Session, principal: Principal, tenant_id: int, capability: str
) -> customer_scope.View | None:
    """Kunden-Benutzer (feinere Rollen): Sicht nur auf die Kunden, fuer die er diese Capability
    hat; None = keiner davon im Mandanten. Betreuer: nicht verwenden."""
    customers = principal.customers_with(capability)
    if not customers:
        return None
    try:
        return customer_scope.view(session, tenant_id, list(customers))
    except LookupError:
        return None


def ownership(
    session: Session, principal: Principal, tenant_id: int, capability: str
) -> customer_scope.Ownership | None:
    """Was ganz den Kunden gehoert, fuer die der Kunden-Benutzer die Capability hat."""
    view = capability_view(session, principal, tenant_id, capability)
    return None if view is None else customer_scope.Ownership(session, tenant_id, view.own)


def actor(principal: Principal) -> dict:
    """Audit (feinere Rollen): Kunden-Rollen des handelnden Kunden-Benutzers je Kunde."""
    if not principal.is_customer:
        return {}
    roles = {str(c): principal.customer_role(c) for c in principal.customer_ids or ()}
    return {"customer_roles": {c: r for c, r in roles.items() if r}}


def visible(session: Session, view: customer_scope.View | None, kind: str, ref_id) -> None:
    """Element im Ausschnitt? Sonst 404 (Kunden-Benutzer erfahren nicht, dass es existiert)."""
    if view is None:
        return
    if not view.contains(session, kind, ref_id):
        raise HTTPException(status_code=404, detail="not found")


def ids(session: Session, view: customer_scope.View | None, kind: str) -> set[int] | None:
    """IDs einer Art im Ausschnitt bzw. None ohne Einschraenkung."""
    return None if view is None else set(session.scalars(view.ids(kind)))


def finding_detail(principal: Principal, detail: dict | None) -> dict | None:
    """Detail eines Findings fuer Kunden-Benutzer ohne Angaben zu fremden Regeln."""
    if not principal.is_customer or not isinstance(detail, dict):
        return detail
    return {k: v for k, v in detail.items() if k not in _FINDING_DETAIL_HIDDEN}
