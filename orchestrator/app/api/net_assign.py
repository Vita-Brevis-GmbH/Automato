"""Grosse Netze der Regeln von Hand zuordnen (0.111.0, ADR 0040): lesen = `viewer`, setzen und
loeschen = `operator` mit Audit. Ein Netz gehoert zu einer Zone, geht ueber eine Anbindung, der
Weg nach dem Routing stimmt (`route`) bzw. nur die genauer gerouteten Teile zaehlen (`ignore`).
Ohne Zuordnung entscheiden Zonen, Anbindungen und das Routing (`store/net_assign.py`)."""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.auth.deps import require_operator, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.i18n import L
from app.models import LogicalZone, NetAssignment, Tenant, TopoExit
from app.store import net_assign

router = APIRouter(prefix="/routing/net-assignments", tags=["routing"])


class NetAssignIn(BaseModel):
    tenant_id: int
    prefix: str = Field(max_length=64)
    kind: Literal["zone", "exit", "route", "ignore"]
    zone_id: int | None = None
    exit_id: int | None = None
    note: str | None = Field(default=None, max_length=500)


def _out(session: Session, a: NetAssignment) -> dict:
    zone = session.get(LogicalZone, a.zone_id) if a.zone_id else None
    ex = session.get(TopoExit, a.exit_id) if a.exit_id else None
    return {"id": a.id, "tenant_id": a.tenant_id, "prefix": a.prefix, "kind": a.kind,
            "zone_id": a.zone_id, "zone": zone.name if zone else None,
            "exit_id": a.exit_id, "exit": ex.name if ex else None, "note": a.note,
            "changed_by": a.changed_by, "changed_at": a.changed_at}  # fmt: skip


def _audit(session: Session, principal: Principal, action: str, a: NetAssignment) -> None:
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action=f"net_assignment.{action}",
        subject=str(a.id),
        detail={
            "tenant_id": a.tenant_id,
            "prefix": a.prefix,
            "kind": a.kind,
            "zone_id": a.zone_id,
            "exit_id": a.exit_id,
        },  # fmt: skip
    )


@router.get("")
def list_assignments(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    tid = tenant_scope(principal, tenant_id)
    if tid is None:
        raise HTTPException(status_code=422, detail="tenant_id required")
    rows = session.scalars(select(NetAssignment).where(NetAssignment.tenant_id == tid)
                           .order_by(NetAssignment.prefix))  # fmt: skip
    return [_out(session, a) for a in rows]


@router.put("")
def set_assignment(
    body: NetAssignIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    tid = tenant_scope(principal, body.tenant_id)
    if tid is None or session.get(Tenant, tid) is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    net = net_assign.as_net(body.prefix)
    if net is None:
        raise HTTPException(
            status_code=422,
            detail=L(
                "prefix: ein Netz (kein Host, nicht any)", "prefix: a network (no host, not any)"
            ),
        )
    zone_id = exit_id = None
    if body.kind == "zone":
        zone = session.get(LogicalZone, body.zone_id) if body.zone_id else None
        if zone is None or zone.tenant_id != tid:
            raise HTTPException(status_code=422, detail=L("Zone wählen", "choose a zone"))
        zone_id = zone.id
    elif body.kind == "exit":
        ex = session.get(TopoExit, body.exit_id) if body.exit_id else None
        if ex is None or ex.tenant_id != tid:
            raise HTTPException(status_code=422, detail=L("Anbindung wählen", "choose an exit"))
        exit_id = ex.id
    prefix = str(net)
    stmt = select(NetAssignment).where(NetAssignment.tenant_id == tid,
                                       NetAssignment.prefix == prefix)  # fmt: skip
    a = session.scalars(stmt).first()
    if a is None:
        a = NetAssignment(tenant_id=tid, prefix=prefix)
        session.add(a)
    a.kind, a.zone_id, a.exit_id = body.kind, zone_id, exit_id
    a.note = (body.note or "").strip() or None
    a.changed_by, a.changed_at = f"user:{principal.subject}", datetime.now(UTC)
    session.flush()
    _audit(session, principal, "set", a)
    return _out(session, a)


@router.delete("/{assignment_id}", status_code=204)
def delete_assignment(
    assignment_id: int,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> Response:
    a = session.get(NetAssignment, assignment_id)
    if a is None or tenant_scope(principal, a.tenant_id) != a.tenant_id:
        raise HTTPException(status_code=404, detail="assignment not found")
    _audit(session, principal, "deleted", a)
    session.delete(a)
    session.flush()
    return Response(status_code=204)
