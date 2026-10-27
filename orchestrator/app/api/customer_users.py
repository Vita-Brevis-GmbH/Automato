"""Zuordnung Benutzer -> Kunde in Automato (0.53.0, ADR 0018 Schritt 4).

Keycloak meldet nur, wer jemand ist; welche Kunden ein Benutzer sieht, pflegt ein Admin hier
(`customer_user`). Die Kennung (Benutzername, E-Mail bzw. `sub`) wird klein gespeichert und
gegen das Token verglichen (`auth/assignments.py`). Eine Zuordnung schraenkt den Benutzer immer
auf den Kunden ein - das eigene Konto laesst sich nicht zuordnen (Schutz vor Aussperren).
Automato schreibt nicht nach Keycloak. Jede Aenderung im Audit.

Feinere Rollen (ADR 0018 Ergaenzung, `auth/capabilities.py`): Kunden-Rolle `viewer` liest die
Doku des Kunden, `triage` quittiert zusaetzlich seine Findings (`operator` aus 0.53.0 gilt als
triage), `editor` pflegt seine Applikationen, `customer_admin` ordnet die Kunden-Benutzer seines
Kunden hier selbst zu (`admin` bleibt dem globalen Admin vorbehalten: 422). Ein **Kunden-Admin**
sieht und aendert nur die Zuordnungen seiner Kunden, vergibt hoechstens die eigene Rolle, aendert
bzw. entfernt nie die eigene Zuordnung und nie andere Kunden; seine Zuordnungen
(`origin = customer`) wirken nie auf Betreuer. Betreuer: Admin.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.api.customer_filter import actor
from app.auth import assignments
from app.auth import capabilities as caps
from app.auth.deps import require_admin, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.i18n import L
from app.models import CUSTOMER_USER_ROLES, Customer, CustomerUser

router = APIRouter(tags=["customer-users"])

_LOGIN = r"^\S{1,200}$"


class AssignmentIn(BaseModel):
    customer_id: int
    login: str = Field(pattern=_LOGIN)
    role: str = "viewer"
    note: str | None = Field(default=None, max_length=2000)


class AssignmentPatch(BaseModel):
    role: str | None = None
    note: str | None = Field(default=None, max_length=2000)


def _out(a: CustomerUser, names: dict[int, str], principal: Principal | None = None) -> dict:
    # Kunden-Admins sehen nicht, welcher Betreuer eine Zuordnung angelegt hat
    hidden = principal is not None and principal.is_customer and a.origin != assignments.CUSTOMER
    return {
        "id": a.id,
        "tenant_id": a.tenant_id,
        "customer_id": a.customer_id,
        "customer": names.get(a.customer_id),
        "login": a.login,
        "role": a.role,
        "origin": a.origin or assignments.STAFF,
        "note": a.note,
        "created_by": None if hidden else a.created_by,
        "created_at": a.created_at,
    }


def _names(session: Session, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    return dict(
        session.execute(select(Customer.id, Customer.name).where(Customer.id.in_(ids))).all()
    )


def _audit(session: Session, principal: Principal, action: str, a: CustomerUser,
           previous: str | None = None) -> None:  # fmt: skip
    detail = {
        "tenant_id": a.tenant_id,
        "customer_id": a.customer_id,
        "login": a.login,
        "role": a.role,
        "origin": a.origin or assignments.STAFF,
        **actor(principal),
    }
    if previous is not None:
        detail["previous_role"] = previous
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action=f"customer_user.{action}",
        subject=str(a.id),
        detail=detail,
    )


def _role(role: str) -> str:
    if role not in CUSTOMER_USER_ROLES:
        raise HTTPException(status_code=422, detail=f"unknown role {role!r}")
    return role


def _own(principal: Principal, login: str) -> bool:
    return login in {principal.subject.lower(), *principal.logins}


def _managed(principal: Principal, customer_id: int) -> None:
    """Kunden-Admin: nur Kunden, fuer die er Kunden-Benutzer verwalten darf - fremde Kunden
    sind nicht auffindbar (404), eigene ohne diese Rolle 403."""
    if not principal.is_customer:
        return
    if customer_id not in (principal.customer_ids or ()):
        raise HTTPException(status_code=404, detail="customer not found")
    if not principal.can(caps.USERS_MANAGE, customer_id):
        raise HTTPException(status_code=403, detail="insufficient role")


def _within(principal: Principal, customer_id: int, role: str) -> None:
    """Kunden-Admin: hoechstens die eigene Rolle vergeben bzw. aendern (nie hochstufen)."""
    if principal.is_customer and caps.rank(role) > caps.rank(principal.customer_role(customer_id)):
        raise HTTPException(
            status_code=403,
            detail=L("Rolle über der eigenen Rolle", "role above own role"),
        )


@router.get("/customer-users")
def list_assignments(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """Zuordnungen (je Mandant bzw. alle), nach Kunde und Kennung; Kunden-Admin: die seiner
    Kunden."""
    tid = tenant_scope(principal, tenant_id)
    stmt = select(CustomerUser)
    if tid is not None:
        stmt = stmt.where(CustomerUser.tenant_id == tid)
    if principal.is_customer:
        stmt = stmt.where(CustomerUser.customer_id.in_(principal.customers_with(caps.USERS_MANAGE)))
    rows = list(session.scalars(stmt.order_by(CustomerUser.customer_id, CustomerUser.login)))
    names = _names(session, {a.customer_id for a in rows})
    return [_out(a, names, principal) for a in rows]


@router.post("/customer-users", status_code=201)
def assign(
    body: AssignmentIn,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Benutzer einem Kunden zuordnen (Admin bzw. Kunden-Admin fuer seinen Kunden)."""
    c = session.get(Customer, body.customer_id)
    if c is None:
        raise HTTPException(status_code=404, detail="customer not found")
    tenant_scope(principal, c.tenant_id)
    _managed(principal, c.id)
    role = _role(body.role)
    _within(principal, c.id, role)
    login = body.login.strip().lower()
    if _own(principal, login):
        raise HTTPException(status_code=409, detail="own account cannot be assigned to a customer")
    clash = session.scalar(
        select(CustomerUser.id).where(CustomerUser.customer_id == c.id, CustomerUser.login == login)
    )
    if clash is not None:
        raise HTTPException(status_code=409, detail="assignment already exists")
    origin = assignments.CUSTOMER if principal.is_customer else assignments.STAFF
    a = CustomerUser(tenant_id=c.tenant_id, customer_id=c.id, login=login, role=role,
                     origin=origin, note=(body.note or "").strip() or None,
                     created_by=principal.subject, created_at=datetime.now(UTC))  # fmt: skip
    session.add(a)
    session.flush()
    _audit(session, principal, "assigned", a)
    return _out(a, {c.id: c.name}, principal)


def _get(session: Session, principal: Principal, assignment_id: int) -> CustomerUser:
    a = session.get(CustomerUser, assignment_id)
    if a is None:
        raise HTTPException(status_code=404, detail="assignment not found")
    tenant_scope(principal, a.tenant_id)
    if principal.is_customer:  # Kunden-Admin: nur Zuordnungen seiner Kunden, nie die eigene
        if a.customer_id not in principal.customers_with(caps.USERS_MANAGE):
            raise HTTPException(status_code=404, detail="assignment not found")
        if _own(principal, a.login):
            raise HTTPException(
                status_code=409,
                detail=L(
                    "Die eigene Zuordnung lässt sich nicht ändern",
                    "own assignment cannot be changed",
                ),
            )
        _within(principal, a.customer_id, a.role)
    return a


@router.patch("/customer-users/{assignment_id}")
def change(
    assignment_id: int,
    body: AssignmentPatch,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Rolle bzw. Notiz einer Zuordnung aendern (Admin bzw. Kunden-Admin)."""
    a = _get(session, principal, assignment_id)
    previous = a.role
    if body.role is not None:
        role = _role(body.role)
        _within(principal, a.customer_id, role)
        a.role = role
    if body.note is not None:
        a.note = body.note.strip() or None
    session.flush()
    _audit(session, principal, "changed", a, previous if previous != a.role else None)
    return _out(a, _names(session, {a.customer_id}), principal)


@router.delete("/customer-users/{assignment_id}", status_code=204)
def remove(
    assignment_id: int,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
) -> None:
    """Zuordnung entfernen (Admin bzw. Kunden-Admin) - wirkt bei der naechsten Anfrage des
    Benutzers."""
    a = _get(session, principal, assignment_id)
    _audit(session, principal, "removed", a)
    session.delete(a)


@router.get("/auth/me")
def me(
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Wer bin ich laut Orchestrator: Rollen, Mandanten, Kunden (auch aus der Zuordnung in
    Automato) - die Oberflaeche richtet sich danach. Seit den feineren Rollen dazu die Kunden-Rolle
    je Kunde und die Capabilities (Betreuer: zur Anzeige aus ihren Rollen)."""
    customers = []
    if principal.customer_ids:
        customers = [
            {"id": cid, "name": name, "role": principal.customer_role(cid)}
            for cid, name in session.execute(
                select(Customer.id, Customer.name)
                .where(Customer.id.in_(list(principal.customer_ids)))
                .order_by(Customer.name)
            )
        ]
    capabilities = (
        principal.capabilities
        if principal.is_customer
        else caps.staff_capabilities(principal.roles)
    )
    return {
        "subject": principal.subject,
        "roles": sorted(principal.roles),
        "tenant_ids": list(principal.tenant_ids) if principal.tenant_ids is not None else None,
        "customer_ids": list(principal.customer_ids)
        if principal.customer_ids is not None
        else None,
        "is_admin": principal.is_admin,
        "is_customer": principal.is_customer,
        "customer_source": principal.customer_source,
        "customers": customers,
        # feinere Rollen: hoechste Kunden-Rolle, je Kunde und die Capabilities
        "customer_role": caps.highest(r for _c, r in principal.customer_roles)
        if principal.is_customer
        else None,
        "customer_roles": {str(c["id"]): c["role"] for c in customers if c["role"]},
        "capabilities": sorted(capabilities),
        # eigene Kennungen (sub, Benutzername, E-Mail): die Oberflaeche erkennt die eigene
        # Zuordnung (aendern/entfernen nicht moeglich)
        "logins": list(principal.logins),
    }
