"""Rechte nur aus dem Cockpit (1.14.0, ADR 0058): bei Kunden aus dem Cockpit vergeben Kunden
keine Rechte.

Anwender 2026-11-16: „die Rollen und User … sollen nur noch im Cockpit sein. Die Kunden sollen
dies nicht machen können, da es zu gefährlich ist, dass wir neuen Benutzern bei einem Problem von
aussen [Rechte] geben können.“

Fuer Kunden in Automato, die das Cockpit fuehrt (`store/verwaltung.cockpit_kunden`):

- **Zuordnungen eines Kunden-Admins** (`customer_user.origin = customer`) wirken nicht mehr - nur
  die des Betreuers (globaler Admin, Notzugang) bleiben.
- **Kein Zuordnen durch den Kunden-Admin:** der Aufrufer bekommt die Kunden markiert
  (`Principal.cockpit_customers`); fuer sie fehlt ihm `customer_users.manage` (die Oberflaeche
  blendet die Seite aus, die API antwortet 409 mit Hinweis).
- Die Rolle aus dem Token eines Kunden-Realms zaehlt bei Kunden aus dem Cockpit nicht
  (`auth/oidc.realm_principal`): Rolle aus `benutzer` bzw. die Standard-Rolle.

Ist der Stand nicht lesbar, gibt es keinen Zugriff (503, fail-closed) - wie bei der Zuordnung.
"""

import logging
from dataclasses import replace

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.auth import assignments
from app.auth.principal import Principal
from app.store import verwaltung

log = logging.getLogger(__name__)


def _gefuehrt(factory, tenant_ids) -> frozenset[int]:
    try:
        with factory() as session:
            return verwaltung.cockpit_kunden(session, tenant_ids)
    except SQLAlchemyError as exc:
        log.warning("Kunden aus dem Cockpit nicht lesbar: %s", exc)
        raise HTTPException(status_code=503, detail="customer assignment unavailable") from exc


def markieren(principal: Principal, gefuehrt: frozenset[int]) -> Principal:
    """Kunden aus dem Cockpit am Kunden-Benutzer vermerken (dort kein Zuordnen)."""
    if not principal.is_customer:
        return principal
    ids = tuple(sorted(gefuehrt & set(principal.customer_ids or ())))
    return replace(principal, cockpit_customers=ids) if ids else principal


def resolve(factory, principal: Principal) -> Principal:
    """Zuordnung in Automato anwenden (`assignments`) - ohne Zuordnungen eines Kunden-Admins bei
    Kunden aus dem Cockpit; Kunden aus dem Cockpit am Ergebnis vermerken."""
    rows = assignments.lookup(factory, principal.logins)
    if not rows and not principal.is_customer:
        return principal  # Betreuer ohne Zuordnung: unveraendert, keine weitere Abfrage
    tenants = set(principal.tenant_ids or ()) | {r[1] for r in rows}
    gefuehrt = _gefuehrt(factory, tenants)
    rows = [r for r in rows
            if not ((r[3] if len(r) > 3 else assignments.STAFF) == assignments.CUSTOMER
                    and r[0] in gefuehrt)]  # fmt: skip
    return markieren(assignments.apply(principal, rows), gefuehrt)


def realm(factory, principal: Principal) -> Principal:
    """Benutzer eines Kunden-Realms: Kunden aus dem Cockpit vermerken."""
    if not principal.customer_ids:
        return principal
    return markieren(principal, _gefuehrt(factory, principal.tenant_ids or ()))
