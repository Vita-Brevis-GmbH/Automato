"""Logische Zonen, Zonenmatrix und Zonenplan (0.51.0, ADR 0024).

Zonen werden in Automato gepflegt: Selektoren wie beim Kunden (ACI-Tenant/VRF/AP/EPG/BD,
VDOM/Zone/Interface, Netz, Kuerzel, IPAM-Merkmal) und dazu `exit` (Netze hinter einer
Anbindung). Die Netze je Zone, die Matrix und der Plan sind abgeleitet (`store/zones.py`).
Lesen: Viewer; Zonen und Absicht je Zonenpaar aendern: Operator. Nicht fuer Kunden-Benutzer
(Freigabeliste, ADR 0018) - deren Sicht auf ihre Zonen folgt mit 0.53.0. Nur Daten der
Plattform - kein Zugriff auf Geraete.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.audit import reads
from app.auth.deps import require_operator, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import (
    INTENTS,
    ZONE_KINDS,
    ZONE_SELECTOR_KINDS,
    Customer,
    LogicalZone,
    LogicalZoneSelector,
    SourceSystem,
    ZonePolicy,
)
from app.store import customers as customer_store
from app.store import reconcile
from app.store import zones as store

router = APIRouter(prefix="/zones", tags=["zones"])

_COLOR = r"^#[0-9a-fA-F]{6}$"


class SelectorIn(BaseModel):
    kind: str
    value: str = Field(min_length=1, max_length=400)
    source_system_id: int | None = None
    exclude: bool = False


class ZoneIn(BaseModel):
    tenant_id: int
    name: str = Field(min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=50)
    kind: str = "internal"
    customer_id: int | None = None
    color: str | None = Field(default=None, pattern=_COLOR)
    position: int = Field(default=0, ge=0, le=10000)
    description: str | None = None
    selectors: list[SelectorIn] = Field(default_factory=list, max_length=200)


class PreviewIn(BaseModel):
    tenant_id: int
    zone_id: int | None = None
    selectors: list[SelectorIn] = Field(default_factory=list, max_length=200)


class PolicyIn(BaseModel):
    tenant_id: int
    src_zone_id: int
    dst_zone_id: int
    intent: str | None = None  # leer = keine Absicht (entfernen)
    note: str | None = Field(default=None, max_length=2000)


def _tenant(session: Session, principal: Principal, tenant_id: int | None) -> int:
    tid = tenant_scope(principal, tenant_id)
    if tid is None:
        raise HTTPException(status_code=422, detail="tenant_id required")
    reads.maybe_audit_read(session, principal, tid, "zones")
    return tid


def _selectors(session: Session, tid: int, items: list[SelectorIn]) -> list[store.Selector]:
    """Selektoren pruefen: Art, Netz als CIDR, Quellsystem des Mandanten."""
    sids = set(session.scalars(select(SourceSystem.id).where(SourceSystem.tenant_id == tid)))
    out = []
    for s in items:
        if s.kind not in ZONE_SELECTOR_KINDS:
            raise HTTPException(status_code=422, detail=f"unknown selector kind {s.kind!r}")
        value = s.value.strip()
        if s.kind == "prefix" and customer_store._net(value) is None:
            raise HTTPException(status_code=422, detail=f"invalid prefix {value!r}")
        if s.source_system_id is not None:
            if s.kind not in customer_store.FW_KINDS:
                raise HTTPException(status_code=422, detail="source system only for FortiGate")
            if s.source_system_id not in sids:
                raise HTTPException(status_code=404, detail="source system not found")
        out.append(store.Selector(s.kind, value, s.source_system_id, s.exclude))
    return out


def _out(z: LogicalZone, nets: store.ZoneNets | None = None,
         customers: dict[int, str] | None = None) -> dict:  # fmt: skip
    return {
        **store.zone_out(z, nets),
        "tenant_id": z.tenant_id,
        "description": z.description,
        "customer": (customers or {}).get(z.customer_id) if z.customer_id else None,
        "updated_at": z.updated_at,
        "selectors": [
            {
                "id": s.id,
                "kind": s.kind,
                "value": s.value,
                "source_system_id": s.source_system_id,
                "exclude": s.exclude,
            }
            for s in z.selectors
        ],  # fmt: skip
    }


def _customers(session: Session, tid: int) -> dict[int, str]:
    return dict(
        session.execute(select(Customer.id, Customer.name).where(Customer.tenant_id == tid)).all()
    )


def _audit(session: Session, principal: Principal, action: str, z: LogicalZone) -> None:
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action=f"zone.{action}",
        subject=str(z.id),
        detail={
            "tenant_id": z.tenant_id,
            "zone_id": z.id,
            "name": z.name,
            "kind": z.kind,
            "selectors": len(z.selectors),
        },  # fmt: skip
    )


def _get(session: Session, principal: Principal, zone_id: int) -> LogicalZone:
    z = session.get(LogicalZone, zone_id)
    if z is None:
        raise HTTPException(status_code=404, detail="zone not found")
    tenant_scope(principal, z.tenant_id)
    return z


def _apply(session: Session, z: LogicalZone, body: ZoneIn, sel: list[store.Selector]) -> None:
    name = body.name.strip()
    clash = session.scalar(
        select(LogicalZone.id).where(
            LogicalZone.tenant_id == z.tenant_id,
            LogicalZone.name == name,
            LogicalZone.id != (z.id or 0),
        )
    )
    if clash is not None:
        raise HTTPException(status_code=409, detail="zone already exists")
    if body.kind not in ZONE_KINDS:
        raise HTTPException(status_code=422, detail=f"unknown zone kind {body.kind!r}")
    if body.customer_id is not None:
        c = session.get(Customer, body.customer_id)
        if c is None or c.tenant_id != z.tenant_id:
            raise HTTPException(status_code=404, detail="customer not found")
    z.name, z.code = name, (body.code or "").strip() or None
    z.kind, z.customer_id, z.color = body.kind, body.customer_id, body.color
    z.position, z.description = body.position, (body.description or "").strip() or None
    z.updated_at = datetime.now(UTC)
    z.selectors = [
        LogicalZoneSelector(kind=s.kind, value=s.value, source_system_id=s.source_system_id,
                            exclude=s.exclude)
        for s in sel
    ]  # fmt: skip


@router.get("")
def list_zones(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """Zonen der Umgebung in ihrer Reihenfolge, mit Selektoren und Anzahl Netze/EPGs."""
    tid = _tenant(session, principal, tenant_id)
    items = store.load(session, tid)
    nets = {n.id: n for n in store.derive(session, tid, items)}
    names = _customers(session, tid)
    return [_out(z, nets.get(z.id), names) for z in items]


@router.post("/preview")
def preview(
    body: PreviewIn,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Was die Selektoren erfassen - ohne zu speichern: Netze, EPGs, gleiche Praefixe in
    anderen Zonen."""
    tid = _tenant(session, principal, body.tenant_id)
    return store.preview(session, tid, _selectors(session, tid, body.selectors), body.zone_id)


@router.get("/matrix")
def matrix(
    tenant_id: int | None = None,
    source_system_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Zonenmatrix: je Paar Quell-/Zielzone die Regeln (erlaubend, sperrend), Absicht und
    Verstoesse; dazu `any` und `none` (Adressen ohne Zone)."""
    tid = _tenant(session, principal, tenant_id)
    return store.matrix(session, tid, source_system_id)


@router.get("/matrix/rules")
def matrix_rules(
    src: str,
    dst: str,
    tenant_id: int | None = None,
    source_system_id: int | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Regeln einer Zelle (Quellzone -> Zielzone; Zone-ID, `any` bzw. `none`)."""
    tid = _tenant(session, principal, tenant_id)
    try:
        cells = store.parse_cell(src), store.parse_cell(dst)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="src/dst: zone id, any or none") from exc
    return store.cell_rules(session, tid, *cells, limit=limit, offset=offset,
                            source_system_id=source_system_id)  # fmt: skip


@router.put("/policy")
def set_policy(
    body: PolicyIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Absicht fuer ein Zonenpaar setzen (allow, limited, deny) bzw. entfernen (leer)."""
    tid = tenant_scope(principal, body.tenant_id)
    for zid in (body.src_zone_id, body.dst_zone_id):
        z = session.get(LogicalZone, zid)
        if z is None or z.tenant_id != tid:
            raise HTTPException(status_code=404, detail="zone not found")
    if body.intent is not None and body.intent not in INTENTS:
        raise HTTPException(status_code=422, detail=f"unknown intent {body.intent!r}")
    p = session.scalar(
        select(ZonePolicy).where(
            ZonePolicy.src_zone_id == body.src_zone_id,
            ZonePolicy.dst_zone_id == body.dst_zone_id,
        )
    )
    if body.intent is None:
        if p is not None:
            session.delete(p)
    else:
        if p is None:
            p = ZonePolicy(tenant_id=tid, src_zone_id=body.src_zone_id,
                           dst_zone_id=body.dst_zone_id)  # fmt: skip
            session.add(p)
        p.intent, p.note = body.intent, (body.note or "").strip() or None
        p.updated_by, p.updated_at = principal.subject, datetime.now(UTC)
    session.flush()
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="zone_policy.set",
        subject=f"{body.src_zone_id}>{body.dst_zone_id}",
        detail={
            "tenant_id": tid,
            "src_zone_id": body.src_zone_id,
            "dst_zone_id": body.dst_zone_id,
            "intent": body.intent,
        },  # fmt: skip
    )
    reconcile.refresh_zone_findings(session, tid)
    return {"src": body.src_zone_id, "dst": body.dst_zone_id, "intent": body.intent,
            "note": p.note if body.intent is not None else None}  # fmt: skip


@router.get("/plan")
def plan(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Zonenplan: je Zone die Knoten (Firewall/VDOM, ACI-VRF), an denen sie haengt, und die
    Uebergaenge zwischen Zonen ueber einen Knoten; dazu Netze ohne Zone."""
    return store.plan(session, _tenant(session, principal, tenant_id))


@router.post("", status_code=201)
def create_zone(
    body: ZoneIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Zone anlegen (Operator); ohne Reihenfolge ans Ende."""
    tid = tenant_scope(principal, body.tenant_id)
    sel = _selectors(session, tid, body.selectors)
    z = LogicalZone(tenant_id=tid)
    _apply(session, z, body, sel)
    if not body.position:
        last = session.scalar(
            select(func.max(LogicalZone.position)).where(LogicalZone.tenant_id == tid)
        )
        z.position = (last or 0) + 10
    session.add(z)
    session.flush()
    _audit(session, principal, "created", z)
    reconcile.refresh_zone_findings(session, tid)
    nets = store.derive(session, tid, [z])
    return _out(z, nets[0], _customers(session, tid))


@router.put("/{zone_id}")
def update_zone(
    zone_id: int,
    body: ZoneIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Zone aendern (Operator) - Selektoren ersetzen."""
    z = _get(session, principal, zone_id)
    if body.tenant_id != z.tenant_id:
        raise HTTPException(status_code=422, detail="tenant_id does not match")
    sel = _selectors(session, z.tenant_id, body.selectors)
    _apply(session, z, body, sel)
    session.flush()
    _audit(session, principal, "updated", z)
    reconcile.refresh_zone_findings(session, z.tenant_id)
    nets = store.derive(session, z.tenant_id, [z])
    return _out(z, nets[0], _customers(session, z.tenant_id))


@router.delete("/{zone_id}", status_code=204)
def delete_zone(
    zone_id: int,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> None:
    """Zone loeschen (Operator) - mit ihrer Absicht je Zonenpaar; gelesene Daten bleiben."""
    z = _get(session, principal, zone_id)
    tid = z.tenant_id
    _audit(session, principal, "deleted", z)
    session.execute(
        ZonePolicy.__table__.delete().where(
            (ZonePolicy.src_zone_id == z.id) | (ZonePolicy.dst_zone_id == z.id)
        )
    )
    session.delete(z)
    session.flush()
    reconcile.refresh_zone_findings(session, tid)


@router.get("/{zone_id}/nets")
def zone_nets(
    zone_id: int,
    limit: int = Query(500, ge=1, le=5000),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Netze einer Zone mit Grund (Selektor bzw. EPG)."""
    z = _get(session, principal, zone_id)
    reads.maybe_audit_read(session, principal, z.tenant_id, "zones")
    nets = store.derive(session, z.tenant_id, [z])[0]
    ordered = sorted(nets.nets.items(), key=lambda x: store._net_key(x[0]))
    return {
        "total": len(ordered),
        "items": [{"network": str(n), "via": why} for n, why in ordered[:limit]],
    }
