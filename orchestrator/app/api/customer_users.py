"""Zuordnung Benutzer -> Kunde in Automato (0.53.0, ADR 0018 Schritt 4).

Keycloak meldet nur, wer jemand ist; welche Kunden ein Benutzer sieht, pflegt ein Admin hier
(`customer_user`). Die Kennung (Benutzername, E-Mail bzw. `sub`) wird klein gespeichert und
gegen das Token verglichen (`auth/assignments.py`). Rolle `viewer` liest die Doku des Kunden,
`operator` triagiert zusaetzlich seine Findings. Eine Zuordnung schraenkt den Benutzer immer
auf den Kunden ein - das eigene Konto laesst sich nicht zuordnen (Schutz vor Aussperren).
Automato schreibt nicht nach Keycloak. Nur Admin; jede Aenderung im Audit.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.auth.deps import require_admin, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
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


def _out(a: CustomerUser, names: dict[int, str]) -> dict:
    return {
        "id": a.id,
        "tenant_id": a.tenant_id,
        "customer_id": a.customer_id,
        "customer": names.get(a.customer_id),
        "login": a.login,
        "role": a.role,
        "note": a.note,
        "created_by": a.created_by,
        "created_at": a.created_at,
    }


def _names(session: Session, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    return dict(
        session.execute(select(Customer.id, Customer.name).where(Customer.id.in_(ids))).all()
    )


def _audit(session: Session, principal: Principal, action: str, a: CustomerUser) -> None:
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action=f"customer_user.{action}",
        subject=str(a.id),
        detail={
            "tenant_id": a.tenant_id,
            "customer_id": a.customer_id,
            "login": a.login,
            "role": a.role,
        },  # fmt: skip
    )


def _role(role: str) -> str:
    if role not in CUSTOMER_USER_ROLES:
        raise HTTPException(status_code=422, detail=f"unknown role {role!r}")
    return role


@router.get("/customer-users")
def list_assignments(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """Zuordnungen (je Mandant bzw. alle), nach Kunde und Kennung."""
    tid = tenant_scope(principal, tenant_id)
    stmt = select(CustomerUser)
    if tid is not None:
        stmt = stmt.where(CustomerUser.tenant_id == tid)
    rows = list(session.scalars(stmt.order_by(CustomerUser.customer_id, CustomerUser.login)))
    names = _names(session, {a.customer_id for a in rows})
    return [_out(a, names) for a in rows]


@router.post("/customer-users", status_code=201)
def assign(
    body: AssignmentIn,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Benutzer einem Kunden zuordnen (Admin)."""
    c = session.get(Customer, body.customer_id)
    if c is None:
        raise HTTPException(status_code=404, detail="customer not found")
    tenant_scope(principal, c.tenant_id)
    login = body.login.strip().lower()
    if login in {principal.subject.lower(), *principal.logins}:
        raise HTTPException(status_code=409, detail="own account cannot be assigned to a customer")
    clash = session.scalar(
        select(CustomerUser.id).where(CustomerUser.customer_id == c.id, CustomerUser.login == login)
    )
    if clash is not None:
        raise HTTPException(status_code=409, detail="assignment already exists")
    a = CustomerUser(tenant_id=c.tenant_id, customer_id=c.id, login=login, role=_role(body.role),
                     note=(body.note or "").strip() or None, created_by=principal.subject,
                     created_at=datetime.now(UTC))  # fmt: skip
    session.add(a)
    session.flush()
    _audit(session, principal, "assigned", a)
    return _out(a, {c.id: c.name})


def _get(session: Session, principal: Principal, assignment_id: int) -> CustomerUser:
    a = session.get(CustomerUser, assignment_id)
    if a is None:
        raise HTTPException(status_code=404, detail="assignment not found")
    tenant_scope(principal, a.tenant_id)
    return a


@router.patch("/customer-users/{assignment_id}")
def change(
    assignment_id: int,
    body: AssignmentPatch,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Rolle bzw. Notiz einer Zuordnung aendern (Admin)."""
    a = _get(session, principal, assignment_id)
    if body.role is not None:
        a.role = _role(body.role)
    if body.note is not None:
        a.note = body.note.strip() or None
    session.flush()
    _audit(session, principal, "changed", a)
    return _out(a, _names(session, {a.customer_id}))


@router.delete("/customer-users/{assignment_id}", status_code=204)
def remove(
    assignment_id: int,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
) -> None:
    """Zuordnung entfernen (Admin) - wirkt bei der naechsten Anfrage des Benutzers."""
    a = _get(session, principal, assignment_id)
    _audit(session, principal, "removed", a)
    session.delete(a)


@router.get("/auth/me")
def me(
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Wer bin ich laut Orchestrator: Rollen, Mandanten, Kunden (auch aus der Zuordnung in
    Automato) - die Oberflaeche richtet sich danach."""
    customers = []
    if principal.customer_ids:
        customers = [
            {"id": cid, "name": name}
            for cid, name in session.execute(
                select(Customer.id, Customer.name)
                .where(Customer.id.in_(list(principal.customer_ids)))
                .order_by(Customer.name)
            )
        ]
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
    }
