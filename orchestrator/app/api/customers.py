"""Kunden als Ausschnitt der Umgebung (ADR 0018, Schritt 1).

Ein Kunde wird ueber Selektoren beschrieben (ACI-Tenant/VRF/AP/EPG/BD, VDOM/Zone/Interface,
Praefixe, Kuerzel; ein- bzw. ausschliessend); die Zugehoerigkeit ist abgeleitet
(`store/customers.py`). Lesen: Viewer; anlegen, aendern, loeschen: Operator. Nur Daten der
Plattform - kein Zugriff auf Geraete.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.audit import reads
from app.auth.deps import require_operator, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import (
    Customer,
    CustomerMember,
    CustomerSelector,
    Epg,
    Finding,
    NatRule,
    NetworkObject,
    Rule,
    SourceSystem,
    Vm,
)
from app.models.customer import MEMBER_KINDS, SELECTOR_KINDS
from app.store import customers as store
from app.store import epgs as epg_store

router = APIRouter(prefix="/customers", tags=["customers"])


class SelectorIn(BaseModel):
    kind: str
    value: str = Field(min_length=1, max_length=400)
    source_system_id: int | None = None
    exclude: bool = False


class CustomerIn(BaseModel):
    tenant_id: int
    name: str = Field(min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=50)
    description: str | None = None
    shared: bool = False
    selectors: list[SelectorIn] = Field(default_factory=list, max_length=200)


class PreviewIn(BaseModel):
    tenant_id: int
    selectors: list[SelectorIn] = Field(default_factory=list, max_length=200)


def _tenant(session: Session, principal: Principal, tenant_id: int | None) -> int:
    tid = tenant_scope(principal, tenant_id)
    if tid is None:
        raise HTTPException(status_code=422, detail="tenant_id required")
    reads.maybe_audit_read(session, principal, tid, "customers")
    return tid


def _selectors(session: Session, tid: int, items: list[SelectorIn]) -> list[store.Selector]:
    """Selektoren pruefen: Art, Netz als CIDR, Quellsystem des Mandanten."""
    sids = set(session.scalars(select(SourceSystem.id).where(SourceSystem.tenant_id == tid)))
    out = []
    for s in items:
        if s.kind not in SELECTOR_KINDS:
            raise HTTPException(status_code=422, detail=f"unknown selector kind {s.kind!r}")
        value = s.value.strip()
        if s.kind == "prefix" and store._net(value) is None:
            raise HTTPException(status_code=422, detail=f"invalid prefix {value!r}")
        if s.source_system_id is not None:
            if s.kind not in ("fw_vdom", "fw_zone", "fw_interface"):
                raise HTTPException(status_code=422, detail="source system only for FortiGate")
            if s.source_system_id not in sids:
                raise HTTPException(status_code=404, detail="source system not found")
        out.append(store.Selector(s.kind, value, s.source_system_id, s.exclude))
    return out


def _out(c: Customer, counts: dict[str, int] | None = None) -> dict:
    return {
        "id": c.id,
        "tenant_id": c.tenant_id,
        "name": c.name,
        "code": c.code,
        "description": c.description,
        "shared": c.shared,
        "derived_at": c.derived_at,
        "selectors": [
            {
                "id": s.id,
                "kind": s.kind,
                "value": s.value,
                "source_system_id": s.source_system_id,
                "exclude": s.exclude,
            }
            for s in c.selectors
        ],  # fmt: skip
        "counts": counts or dict.fromkeys(MEMBER_KINDS, 0),
    }


def _audit(session: Session, principal: Principal, action: str, c: Customer) -> None:
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action=f"customer.{action}",
        subject=str(c.id),
        detail={
            "tenant_id": c.tenant_id,
            "customer_id": c.id,
            "name": c.name,
            "selectors": len(c.selectors),
        },  # fmt: skip
    )


def _get(session: Session, principal: Principal, customer_id: int) -> Customer:
    c = session.get(Customer, customer_id)
    if c is None:
        raise HTTPException(status_code=404, detail="customer not found")
    tenant_scope(principal, c.tenant_id)
    return c


def _apply(session: Session, c: Customer, body: CustomerIn, sel: list[store.Selector]) -> None:
    name = body.name.strip()
    clash = session.scalar(
        select(Customer.id).where(
            Customer.tenant_id == c.tenant_id, Customer.name == name, Customer.id != (c.id or 0)
        )
    )
    if clash is not None:
        raise HTTPException(status_code=409, detail="customer already exists")
    c.name, c.code = name, (body.code or "").strip() or None
    c.description, c.shared = body.description, body.shared
    c.updated_at = datetime.now(UTC)
    c.selectors = [
        CustomerSelector(kind=s.kind, value=s.value, source_system_id=s.source_system_id,
                         exclude=s.exclude)
        for s in sel
    ]  # fmt: skip


@router.get("")
def list_customers(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """Kunden der Umgebung mit Selektoren und Anzahl je Art (EPGs, Netze, Objekte, Regeln,
    VMs); geteilte zuerst."""
    tid = _tenant(session, principal, tenant_id)
    stmt = select(Customer).where(Customer.tenant_id == tid)
    if principal.is_customer:  # Kunden-Benutzer: nur die eigenen Kunden (ADR 0018)
        stmt = stmt.where(Customer.id.in_(list(principal.customer_ids or ())))
    rows = list(session.scalars(stmt.order_by(Customer.shared.desc(), Customer.name)))
    counts = store.counts(session, [c.id for c in rows])
    return [_out(c, counts[c.id]) for c in rows]


@router.post("/preview")
def preview(
    body: PreviewIn,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Was die Selektoren erfassen - ohne zu speichern: Anzahl und Beispiele je Art."""
    tid = _tenant(session, principal, body.tenant_id)
    scope = store.evaluate(session, tid, _selectors(session, tid, body.selectors))
    return {"counts": scope.counts(), "samples": store.samples(session, tid, scope)}


@router.get("/suggestions")
def suggestions(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """Je ACI-Tenant ohne Kunden ein Vorschlag, Kuerzel aus `TE_<kuerzel>`; `common` als
    geteilter Teil."""
    return store.suggestions(session, _tenant(session, principal, tenant_id))


@router.get("/unassigned")
def unassigned(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Was keinem Kunden gehoert (EPGs, Regeln, VMs): Anzahl und erste Beispiele."""
    return store.unassigned(session, _tenant(session, principal, tenant_id))


@router.post("", status_code=201)
def create_customer(
    body: CustomerIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Kunden anlegen (Operator); die Zugehoerigkeit wird sofort abgeleitet."""
    tid = tenant_scope(principal, body.tenant_id)
    sel = _selectors(session, tid, body.selectors)
    c = Customer(tenant_id=tid)
    _apply(session, c, body, sel)
    session.add(c)
    session.flush()
    store.derive(session, tid, c.id)
    _audit(session, principal, "created", c)
    return _out(c, store.counts(session, [c.id])[c.id])


@router.put("/{customer_id}")
def update_customer(
    customer_id: int,
    body: CustomerIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Kunden aendern (Operator) - Selektoren ersetzen, Zugehoerigkeit neu ableiten."""
    c = _get(session, principal, customer_id)
    if body.tenant_id != c.tenant_id:
        raise HTTPException(status_code=422, detail="tenant_id does not match")
    sel = _selectors(session, c.tenant_id, body.selectors)
    _apply(session, c, body, sel)
    session.flush()
    store.derive(session, c.tenant_id, c.id)
    _audit(session, principal, "updated", c)
    return _out(c, store.counts(session, [c.id])[c.id])


@router.delete("/{customer_id}", status_code=204)
def delete_customer(
    customer_id: int,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> None:
    """Kunden loeschen (Operator) - nur der Ausschnitt; gelesene Daten bleiben."""
    c = _get(session, principal, customer_id)
    _audit(session, principal, "deleted", c)
    session.execute(CustomerMember.__table__.delete().where(CustomerMember.customer_id == c.id))
    session.delete(c)


@router.get("/{customer_id}/members")
def members(
    customer_id: int,
    kind: str = Query("epg"),
    limit: int = Query(100, ge=1, le=1000),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """Zugehoerigkeit einer Art mit Namen und Grund (Selektor)."""
    c = _get(session, principal, customer_id)
    if principal.is_customer and c.id not in (principal.customer_ids or ()):
        raise HTTPException(status_code=404, detail="customer not found")
    reads.maybe_audit_read(session, principal, c.tenant_id, "customers")
    if kind not in MEMBER_KINDS:
        raise HTTPException(status_code=422, detail=f"unknown kind {kind!r}")
    rows = list(
        session.execute(
            select(CustomerMember.ref_id, CustomerMember.value, CustomerMember.via)
            .where(CustomerMember.customer_id == c.id, CustomerMember.kind == kind)
            .order_by(CustomerMember.id)
            .limit(limit)
        )
    )
    ids = [r.ref_id for r in rows if r.ref_id is not None]
    names: dict[int, str] = {}
    if kind == "epg" and ids:
        names = {
            e.id: epg_store.inventory_label(e)
            for e in session.scalars(select(Epg).where(Epg.id.in_(ids)))
        }
    elif kind == "object" and ids:
        names = dict(
            session.execute(
                select(NetworkObject.id, NetworkObject.name).where(NetworkObject.id.in_(ids))
            ).all()
        )
    elif kind == "rule" and ids:
        names = {
            rid: name or f"#{ext}"
            for rid, name, ext in session.execute(
                select(Rule.id, Rule.name, Rule.external_id).where(Rule.id.in_(ids))
            )
        }
    elif kind == "vm" and ids:
        names = dict(session.execute(select(Vm.id, Vm.name).where(Vm.id.in_(ids))).all())
    elif kind == "nat" and ids:
        names = dict(
            session.execute(select(NatRule.id, NatRule.name).where(NatRule.id.in_(ids))).all()
        )
    elif kind == "finding" and ids:
        names = {
            fid: f"{ftype.value}: {subject}"
            for fid, ftype, subject in session.execute(
                select(Finding.id, Finding.type, Finding.subject_id).where(Finding.id.in_(ids))
            )
        }
    return [
        {"id": r.ref_id, "name": r.value if kind == "net" else names.get(r.ref_id, "?"),
         "via": r.via}
        for r in rows
    ]  # fmt: skip
