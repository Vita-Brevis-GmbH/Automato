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

from app.api.rules import rule_outs
from app.auth.deps import require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import (
    GROUP_CATEGORIES,
    SERVICE_CATEGORIES,
    DeviceObject,
    Finding,
    InvDevice,
    InvInterface,
    InvSubnet,
    InvVlan,
    InvVrf,
    Rule,
    RuleObjectRef,
    SourceSystem,
    SubjectKind,
)
from app.store import naming_profiles as profile_store
from app.store import objects as objects_store
from app.store import references, rule_search, sites

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
    if category:  # eine oder mehrere Kategorien, kommagetrennt
        stmt = stmt.where(DeviceObject.category.in_([c.strip() for c in category.split(",")]))
    objects = list(session.scalars(stmt.order_by(DeviceObject.name, DeviceObject.id)))
    if q:
        # Name, Kommentar, Werte, FQDN, Interface, Mitglieder und die DNS-Namen der Werte aus
        # dem IPAM (dort steht der Server ohne Praefix: web01.example.ch fuer H_web01)
        ql = q.strip().lower()
        ctx = references.context(session, tid)

        def _hit(o: DeviceObject) -> bool:
            texts = [o.name, o.comment, o.fqdn, o.interface, *(o.values or []), *(o.members or [])]
            texts.append(str(o.attrs or ""))  # Service-Attribute (Ports, Kategorie, ...)
            if any(ql in (t or "").lower() for t in texts):
                return True
            names = [n for v in o.values or [] for n in references.dns_names(ctx, v)]
            return any(ql in n.lower() for n in names)

        objects = [o for o in objects if _hit(o)]

    rows = _object_rows(session, tid, objects)
    if issues_only:
        rows = [r for r in rows if r["issues"]]
    return {"total": len(rows), "rows": rows[offset : offset + min(limit, 2000)]}


def _object_rows(session: Session, tid: int, objects: list[DeviceObject]) -> list[dict]:
    """Zeilen der Objekt-Ansicht: Werte mit IPAM-Abgleich, Verwendung, Gruppen, Befunde."""
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
    # Gruppen-Zugehoerigkeit ueber alle Gruppen des Mandanten (nicht nur die gefilterten Objekte)
    member_of: dict[tuple[int, str], list[str]] = defaultdict(list)
    for sid, gname, members in session.execute(
        select(DeviceObject.source_system_id, DeviceObject.name, DeviceObject.members).where(
            DeviceObject.tenant_id == tid, DeviceObject.category.in_(GROUP_CATEGORIES)
        )
    ):
        for m in members or []:
            member_of[(sid, m)].append(gname)
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
    site_index = sites.SiteIndex(session, tid)
    prefixes = profile_store.prefixes_for_tenant(session, tid)
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
                "fqdn": o.fqdn,
                "attrs": o.attrs or {},
                "issues": sorted(set(row_issues)),
                # Netz-Objekte (N_/T_/IPP_): Standort-Block aus dem Namen (ADR 0015)
                "site": site_index.from_name(o.name, prefixes, network_only=True),
            }
        )
    return rows


@router.get("/objects/{object_id}")
def get_object(
    object_id: int,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session),
) -> dict:
    """Detail eines Objekts wie in der NAT-Sicht: Attribute, Werte, Gruppen-Mitglieder und die
    Firewall-Regeln, die es verwenden (direkt oder ueber Gruppen)."""
    o = session.get(DeviceObject, object_id)
    if o is None:
        raise HTTPException(status_code=404, detail="object not found")
    tenant_scope(principal, o.tenant_id)
    (row,) = _object_rows(session, o.tenant_id, [o])
    rules = list(session.scalars(select(Rule).where(Rule.source_system_id == o.source_system_id)))
    search = rule_search.RuleSearch(session, rules)
    using = [r for r in rules if search.uses_object(r, o.name)]
    members = []
    for name in o.members or []:
        found = search.device.get((o.source_system_id, name.lower()), [])
        m = found[0] if found else None
        members.append(
            {
                "name": name,
                "category": m.category.value if m else None,
                "values": m.values or [] if m else [],
                "comment": m.comment if m else None,
            }
        )
    return {
        **row,
        "members_detail": members,
        "rule_count": len(using),
        "rules": [x.model_dump() for x in rule_outs(session, using[:_MAX_RULES], search)],
    }


_MAX_RULES = 500  # Regeln im Objekt-Detail (Gesamtzahl in rule_count)

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
    site_index = sites.SiteIndex(session, tid)
    prefixes = profile_store.prefixes_for_tenant(session, tid)
    system_sites = dict(
        session.execute(
            select(SourceSystem.id, SourceSystem.site_id).where(SourceSystem.tenant_id == tid)
        ).all()
    )
    rows = []
    for r in session.scalars(stmt):
        row = {c.name: getattr(r, c.name) for c in model.__table__.columns}
        row["source"] = names.get(r.source_system_id)
        if kind == "devices":
            # Standort aus dem Geraete-/Cluster-Namen, sonst der des Quellsystems (ADR 0015)
            row["site"] = site_index.from_name(r.name, prefixes)
            row["site_from"] = "name" if row["site"] else None
            if row["site"] is None and system_sites.get(r.source_system_id):
                row["site"] = site_index.ref(system_sites[r.source_system_id])
                row["site_from"] = "system" if row["site"] else None
        elif kind == "subnets":
            row["site"] = site_index.from_name(r.context_name, prefixes)
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
