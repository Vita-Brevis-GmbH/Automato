"""Objekt- und Inventar-Ansicht (ADR 0010).

- `GET /objects`              Firewall-Objekte wie auf dem Geraet: Werte mit Abgleich gegen das
                              IPAM (Referenz/Anreicherung), Verwendung in Regeln/Gruppen und
                              Befunde; Filter Quellsystem/Kategorie/Suche/„nur Abweichungen".
- `GET /inventory/{art}`      Netzinventar: devices | interfaces | vlans | subnets | vrfs;
                              Subnetze mit Abgleich gegen das fuehrende IPAM und die Anreicherung.
Lesen = `viewer`, mandantsgescoped. Rein lesend.
"""

from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import (
    SERVICE_CATEGORIES,
    DeviceObject,
    Finding,
    InvDevice,
    InvInterface,
    InvSubnet,
    InvVlan,
    InvVrf,
    RuleObjectRef,
    SourceSystem,
    SubjectKind,
)
from app.store import objects as objects_store
from app.store import references

router = APIRouter(tags=["objects"])

_ISSUE_STATUSES = {"unmatched", "ambiguous"}


def _tenant(principal: Principal, tenant_id: int | None) -> int:
    tid = tenant_scope(principal, tenant_id)
    if tid is None:
        raise HTTPException(status_code=422, detail="tenant_id required")
    return tid


def _source_names(session: Session, tid: int) -> dict[int, str]:
    return dict(
        session.execute(
            select(SourceSystem.id, SourceSystem.name).where(SourceSystem.tenant_id == tid)
        ).all()
    )


@router.get("/objects")
def list_objects(
    tenant_id: int | None = None,
    source_system_id: int | None = None,
    category: str | None = None,
    q: str | None = None,
    issues_only: bool = False,
    limit: int = 200,
    offset: int = 0,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session),
) -> dict:
    tid = _tenant(principal, tenant_id)
    stmt = select(DeviceObject).where(DeviceObject.tenant_id == tid)
    if source_system_id is not None:
        stmt = stmt.where(DeviceObject.source_system_id == source_system_id)
    if category:
        stmt = stmt.where(DeviceObject.category == category)
    objects = list(session.scalars(stmt.order_by(DeviceObject.name, DeviceObject.id)))
    if q:
        ql = q.strip().lower()
        objects = [
            o
            for o in objects
            if ql in o.name.lower()
            or ql in (o.comment or "").lower()
            or any(ql in v.lower() for v in o.values or [])
        ]

    used = (
        dict(
            session.execute(
                select(RuleObjectRef.device_object_id, func.count())
                .where(RuleObjectRef.device_object_id.in_([o.id for o in objects]))
                .group_by(RuleObjectRef.device_object_id)
            ).all()
        )
        if objects
        else {}
    )
    member_of: dict[tuple[int, str], list[str]] = defaultdict(list)
    for o in objects:
        for m in o.members or []:
            member_of[(o.source_system_id, m)].append(o.name)
    issues: dict[tuple[int | None, str], list[str]] = defaultdict(list)
    for f in session.scalars(
        select(Finding).where(
            Finding.tenant_id == tid, Finding.subject_kind == SubjectKind.DEVICE_OBJECT
        )
    ):
        issues[(f.source_system_id, f.subject_id)].append(f.type.value)

    infos = objects_store.value_infos(
        session,
        tid,
        [v for o in objects if o.category not in SERVICE_CATEGORIES for v in o.values or []],
    )
    names = _source_names(session, tid)
    rows = []
    for o in objects:
        service = o.category in SERVICE_CATEGORIES
        values = [
            {"value": v}
            if service
            else (infos.get(v).model_dump() if infos.get(v) else {"value": v})
            for v in o.values or []
        ]
        row_issues = list(issues.get((o.source_system_id, o.name), []))
        if not service and any(v.get("match_status") in _ISSUE_STATUSES for v in values):
            row_issues.append("unmatched_object")
        rows.append(
            {
                "id": o.id,
                "source_system_id": o.source_system_id,
                "source": names.get(o.source_system_id),
                "category": o.category.value,
                "name": o.name,
                "obj_type": o.obj_type,
                "family": o.family,
                "values": values,
                "members": o.members or [],
                "member_of": member_of.get((o.source_system_id, o.name), []),
                "used_in_rules": used.get(o.id, 0),
                "comment": o.comment,
                "color": o.color,
                "interface": o.interface,
                "issues": sorted(set(row_issues)),
            }
        )
    if issues_only:
        rows = [r for r in rows if r["issues"]]
    return {"total": len(rows), "rows": rows[offset : offset + min(limit, 2000)]}


_KINDS = {
    "devices": InvDevice,
    "interfaces": InvInterface,
    "vlans": InvVlan,
    "subnets": InvSubnet,
    "vrfs": InvVrf,
}
_ORDER = {
    "devices": ("name",),
    "interfaces": ("device", "name"),
    "vlans": ("vid",),
    "subnets": ("prefix",),
    "vrfs": ("name",),
}


@router.get("/inventory/{kind}")
def list_inventory(
    kind: Literal["devices", "interfaces", "vlans", "subnets", "vrfs"],
    tenant_id: int | None = None,
    source_system_id: int | None = None,
    q: str | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session),
) -> list[dict]:
    tid = _tenant(principal, tenant_id)
    model = _KINDS[kind]
    stmt = select(model).where(model.tenant_id == tid)
    if source_system_id is not None:
        stmt = stmt.where(model.source_system_id == source_system_id)
    stmt = stmt.order_by(*(getattr(model, c) for c in _ORDER[kind]), model.id)
    names = _source_names(session, tid)
    ctx = references.context(session, tid) if kind == "subnets" else None
    rows = []
    for r in session.scalars(stmt):
        row = {c.name: getattr(r, c.name) for c in model.__table__.columns}
        row["source"] = names.get(r.source_system_id)
        if ctx is not None:
            row["ipam"] = _prefix_info(ctx.primary, r.prefix)
            row["enrichment"] = _prefix_info(
                ctx.enrichments[0] if ctx.enrichments else None, r.prefix
            )
        rows.append(row)
    if q:
        ql = q.strip().lower()
        rows = [r for r in rows if any(ql in str(v).lower() for v in r.values() if v is not None)]
    return rows


def _prefix_info(index, prefix: str) -> dict | None:
    if index is None:
        return None
    ref = index.prefix(prefix)
    return {
        "source": index.source.name,
        "found": ref is not None,
        "vlan": ref.vlan_vid if ref else None,
        "description": ref.description if ref else None,
        "site": ref.site if ref else None,
        "url": ref.url if ref else None,
    }
