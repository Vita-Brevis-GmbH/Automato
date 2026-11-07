"""Zuordnung Benutzer -> Kunde in Automato anwenden (0.53.0, ADR 0018 Schritt 4).

Keycloak meldet nur, wer jemand ist (sub, Benutzername, E-Mail); welche Kunden er sieht, steht
in `customer_user`. Gibt es eine Zuordnung, wird der Aufrufer zum Kunden-Benutzer: nie Admin,
nur die freigegebenen Endpunkte, Mandanten = die seiner Kunden. Die Zuordnung schraenkt also
ein - sie erweitert nie eine Betreuer-Sicht. Kunden aus dem Claim `customer_ids` (Alternative)
kommen dazu. Ist die Zuordnung nicht lesbar, gibt es keinen Zugriff (fail-closed).

Feinere Rollen (ADR 0018 Ergaenzung): je Kunde gilt die Kunden-Rolle der Zuordnung; steht der
Kunde auch im Claim, die hoehere der beiden. Zuordnungen, die ein **Kunden-Admin** angelegt hat
(`origin = customer`), wirken nur auf Benutzer ohne Betreuer-Rolle bzw. auf Kunden-Benutzer -
sonst koennte ein Kunden-Admin einen Betreuer durch eine Zuordnung zum Kunden-Benutzer machen
und damit aussperren.
"""

import logging
from dataclasses import replace

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.auth import capabilities as caps
from app.auth import roles as R
from app.auth.principal import Principal
from app.models import CustomerUser

log = logging.getLogger(__name__)

STAFF = "staff"  # von einem Betreuer (Admin) angelegt
CUSTOMER = "customer"  # von einem Kunden-Admin angelegt
_STAFF_ROLES = frozenset({R.VIEWER, R.OPERATOR, R.ADMIN})


def lookup(factory, logins: tuple[str, ...]) -> list[tuple[int, int, str, str]]:
    """(Kunde, Mandant, Rolle, Herkunft) je Zuordnung der Kennungen."""
    if not logins:
        return []
    try:
        with factory() as session:
            return [
                (cid, tid, role, origin or STAFF)
                for cid, tid, role, origin in session.execute(
                    select(
                        CustomerUser.customer_id,
                        CustomerUser.tenant_id,
                        CustomerUser.role,
                        CustomerUser.origin,
                    )
                    .where(CustomerUser.login.in_(list(logins)))
                    .order_by(CustomerUser.id)
                )  # fmt: skip
            ]
    except SQLAlchemyError as exc:
        log.warning("Zuordnung Benutzer -> Kunde nicht lesbar: %s", exc)
        raise HTTPException(status_code=503, detail="customer assignment unavailable") from exc


def _eligible(principal: Principal, rows: list[tuple]) -> list[tuple[int, int, str]]:
    """Zuordnungen, die fuer diesen Aufrufer gelten: die der Betreuer immer, die eines
    Kunden-Admins nur fuer Kunden-Benutzer bzw. Benutzer ohne Betreuer-Rolle."""
    staff = [r[:3] for r in rows if (r[3] if len(r) > 3 else STAFF) != CUSTOMER]
    by_customer = [r[:3] for r in rows if (r[3] if len(r) > 3 else STAFF) == CUSTOMER]
    if by_customer and (staff or principal.is_customer or not principal.roles & _STAFF_ROLES):
        return staff + by_customer
    return staff


def apply(principal: Principal, rows: list[tuple]) -> Principal:
    rows = _eligible(principal, rows)
    if not rows:
        return principal
    customers = set(principal.customer_ids or ()) | {c for c, _t, _r in rows}
    tenants = set(principal.tenant_ids or ()) if principal.is_customer else set()
    tenants |= {t for _c, t, _r in rows}
    # je Kunde die hoehere Rolle aus Claim und Zuordnung
    role_of: dict[int, str | None] = {}
    for cid, role in principal.customer_roles if principal.is_customer else ():
        role_of[cid] = caps.higher(role_of.get(cid), role)
    for cid, _t, role in rows:
        role_of[cid] = caps.higher(role_of.get(cid), caps.normalize(role))
    customer_roles = tuple(sorted((c, r) for c, r in role_of.items() if r and c in customers))
    triage = any(caps.rank(r) >= caps.rank(caps.TRIAGE_ROLE) for _c, r in customer_roles)
    roles = {R.VIEWER} | ({R.OPERATOR} if triage else set())
    if principal.is_customer:  # Rolle aus dem Claim (z. B. operator) bleibt, nie Admin
        roles |= set(principal.roles) - {R.ADMIN}
    return replace(
        principal,
        roles=frozenset(roles),
        tenant_ids=tuple(sorted(tenants)),
        customer_ids=tuple(sorted(customers)),
        customer_source="claim+automato" if principal.is_customer else "automato",
        customer_roles=customer_roles,
    )


def resolve(factory, principal: Principal) -> Principal:
    return apply(principal, lookup(factory, principal.logins))
