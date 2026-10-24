"""Zuordnung Benutzer -> Kunde in Automato anwenden (0.53.0, ADR 0018 Schritt 4).

Keycloak meldet nur, wer jemand ist (sub, Benutzername, E-Mail); welche Kunden er sieht, steht
in `customer_user`. Gibt es eine Zuordnung, wird der Aufrufer zum Kunden-Benutzer: nie Admin,
nur die freigegebenen Endpunkte, Mandanten = die seiner Kunden. Die Zuordnung schraenkt also
ein - sie erweitert nie eine Betreuer-Sicht. Kunden aus dem Claim `customer_ids` (Alternative)
kommen dazu. Ist die Zuordnung nicht lesbar, gibt es keinen Zugriff (fail-closed).
"""

import logging
from dataclasses import replace

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.auth import roles as R
from app.auth.principal import Principal
from app.models import CustomerUser

log = logging.getLogger(__name__)


def lookup(factory, logins: tuple[str, ...]) -> list[tuple[int, int, str]]:
    """(Kunde, Mandant, Rolle) je Zuordnung der Kennungen."""
    if not logins:
        return []
    try:
        with factory() as session:
            return [
                (cid, tid, role)
                for cid, tid, role in session.execute(
                    select(CustomerUser.customer_id, CustomerUser.tenant_id, CustomerUser.role)
                    .where(CustomerUser.login.in_(list(logins)))
                    .order_by(CustomerUser.id)
                )
            ]
    except SQLAlchemyError as exc:
        log.warning("Zuordnung Benutzer -> Kunde nicht lesbar: %s", exc)
        raise HTTPException(status_code=503, detail="customer assignment unavailable") from exc


def apply(principal: Principal, rows: list[tuple[int, int, str]]) -> Principal:
    if not rows:
        return principal
    customers = set(principal.customer_ids or ()) | {c for c, _t, _r in rows}
    tenants = set(principal.tenant_ids or ()) if principal.is_customer else set()
    tenants |= {t for _c, t, _r in rows}
    roles = {R.VIEWER} | ({R.OPERATOR} if any(r == R.OPERATOR for *_x, r in rows) else set())
    if principal.is_customer:  # Rolle aus dem Claim (z. B. operator) bleibt, nie Admin
        roles |= set(principal.roles) - {R.ADMIN}
    return replace(
        principal,
        roles=frozenset(roles),
        tenant_ids=tuple(sorted(tenants)),
        customer_ids=tuple(sorted(customers)),
        customer_source="claim+automato" if principal.is_customer else "automato",
    )


def resolve(factory, principal: Principal) -> Principal:
    return apply(principal, lookup(factory, principal.logins))
