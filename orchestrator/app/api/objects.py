"""Objekt- und Inventar-Ansicht (ADR 0010).

- `GET /objects`              Firewall-Objekte wie auf dem Geraet (IPv4 und IPv6 gleichnamig =
                              zwei Objekte): Werte mit Abgleich gegen das IPAM, Verwendung in
                              Regeln (auch ueber Gruppen) und Gruppen, Befunde; Filter
                              Quellsystem/Kategorie/Suche/„nur Abweichungen".
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
    GROUP_CATEGORIES,
    SERVICE_CATEGORIES,
    DeviceObject,
    Finding,
    InvDevice,
    InvInterface,
    InvSubnet,
    InvVlan,
    InvVrf,
    MatchStatus,
    NetworkObject,
    SourceSystem,
    SubjectKind,
)
from app.store import naming_profiles as profile_store
from app.store import objects as objects_store
from app.store import reconcile, references, rule_index, sites

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
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    tid = _tenant(principal, tenant_id)
    rule_index.ensure(session, tid)
    stmt = select(DeviceObject).where(DeviceObject.tenant_id == tid)
    if source_system_id is not None:
        stmt = stmt.where(DeviceObject.source_system_id == source_system_id)
    if category:  # eine oder mehrere Kategorien, kommagetrennt
        stmt = stmt.where(DeviceObject.category.in_([c.strip() for c in category.split(",")]))
    stmt = stmt.order_by(DeviceObject.name, DeviceObject.family, DeviceObject.id)
    if not q and not issues_only:  # seitenweise per SQL (grosse Objekt-Bestaende)
        total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        page = list(session.scalars(stmt.offset(offset).limit(min(limit, 2000))))
        return {"total": total, "rows": _object_rows(session, tid, page)}
    objects = list(session.scalars(stmt))
    if q:
        # Name, Kommentar, Werte, FQDN, Interface, Mitglieder und die DNS-Namen der Werte aus
        # dem IPAM (dort steht der Server ohne Praefix: web01.example.ch fuer H_web01)
        ql = q.strip().lower()
        ctx = references.context(session, tid)

        def _hit(o: DeviceObject) -> bool:
            texts = [o.name, o.comment, o.fqdn, o.interface, *(o.values or []), *(o.members or [])]
            texts.append(str(o.attrs or ""))  # Service-Attribute (Ports, Kategorie, ...)
            texts.append({"v4": "ipv4", "v6": "ipv6"}.get(o.family or "", ""))
            if any(ql in (t or "").lower() for t in texts):
                return True
            names = [n for v in o.values or [] for n in references.dns_names(ctx, v)]
            return any(ql in n.lower() for n in names)

        objects = [o for o in objects if _hit(o)]
    if issues_only:
        issues = _issues(session, tid, objects)
        objects = [o for o in objects if issues.get(o.id)]
    page = objects[offset : offset + min(limit, 2000)]
    return {"total": len(objects), "rows": _object_rows(session, tid, page)}


def _issues(session: Session, tid: int, objects: list[DeviceObject]) -> dict[int, list[str]]:
    """Befunde je Objekt (aus dem Abgleich) und Werte ohne Treffer im IPAM - ohne die teure
    Aufbereitung der Referenzdaten (fuer den Filter "nur Abweichungen" ueber alle Objekte)."""
    by_subject: dict[tuple[int | None, str], list[str]] = defaultdict(list)
    for sid, subject, ftype in session.execute(
        select(Finding.source_system_id, Finding.subject_id, Finding.type).where(
            Finding.tenant_id == tid, Finding.subject_kind == SubjectKind.DEVICE_OBJECT
        )
    ):
        by_subject[(sid, subject)].append(ftype.value)
    bad = set(
        session.scalars(
            select(NetworkObject.name).where(
                NetworkObject.tenant_id == tid,
                NetworkObject.match_status.in_([MatchStatus.UNMATCHED, MatchStatus.AMBIGUOUS]),
            )
        )
    )
    out: dict[int, list[str]] = {}
    for o in objects:
        found = list(by_subject.get((o.source_system_id, reconcile.subject(o)), []))
        service = o.category in SERVICE_CATEGORIES
        if not service and any(objects_store.object_key(v) in bad for v in o.values or []):
            found.append("unmatched_object")
        out[o.id] = sorted(set(found))
    return out


def _object_rows(session: Session, tid: int, objects: list[DeviceObject]) -> list[dict]:
    """Zeilen der Objekt-Ansicht: Werte mit IPAM-Abgleich, Verwendung (Regeln direkt und ueber
    Gruppen aus dem Regelindex), Gruppen derselben Adressfamilie, Befunde."""
    used = rule_index.usage_counts(session, [o.id for o in objects])
    # Gruppen-Zugehoerigkeit ueber alle Gruppen des Mandanten (nicht nur die gefilterten Objekte);
    # IPv4-Gruppen enthalten IPv4-Objekte, IPv6-Gruppen IPv6-Objekte
    member_of: dict[tuple[int, str], list[tuple[str, str | None]]] = defaultdict(list)
    for sid, gname, gfamily, members in session.execute(
        select(
            DeviceObject.source_system_id,
            DeviceObject.name,
            DeviceObject.family,
            DeviceObject.members,
        ).where(DeviceObject.tenant_id == tid, DeviceObject.category.in_(GROUP_CATEGORIES))
    ):
        for m in members or []:
            member_of[(sid, m)].append((gname, gfamily))
    issues = _issues(session, tid, objects)

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
        groups = [
            g
            for g, gfam in member_of.get((o.source_system_id, o.name), [])
            if o.family is None or gfam is None or gfam == o.family
        ]
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
                "member_of": list(dict.fromkeys(groups)),
                "used_in_rules": used.get(o.id, 0),
                "comment": o.comment,
                "color": o.color,
                "interface": o.interface,
                "fqdn": o.fqdn,
                "attrs": o.attrs or {},
                "issues": issues.get(o.id, []),
                # Netz-Objekte (N_/T_/IPP_): Standort-Block aus dem Namen (ADR 0015)
                "site": site_index.from_name(o.name, prefixes, network_only=True),
            }
        )
    return rows


@router.get("/objects/{object_id}")
def get_object(
    object_id: int,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Detail eines Objekts wie in der NAT-Sicht: Attribute, Werte, Gruppen-Mitglieder und die
    Firewall-Regeln, die es verwenden (direkt oder ueber Gruppen, aus dem Regelindex)."""
    o = session.get(DeviceObject, object_id)
    if o is None:
        raise HTTPException(status_code=404, detail="object not found")
    tenant_scope(principal, o.tenant_id)
    rule_index.ensure(session, o.tenant_id)
    (row,) = _object_rows(session, o.tenant_id, [o])
    rule_ids = rule_index.rule_ids_using(session, o.id)
    directory = objects_store.Directory(session, {o.source_system_id}, names=o.members or [])
    members = []
    for name in o.members or []:
        m = directory.member(o, name)
        members.append(
            {
                "name": name,
                "category": m.category.value if m else None,
                "family": m.family if m else None,
                "values": m.values or [] if m else [],
                "comment": m.comment if m else None,
            }
        )
    rules = rule_index.load_rules(session, rule_ids[:_MAX_RULES])
    return {
        **row,
        "members_detail": members,
        "rule_count": len(rule_ids),
        "rules": [x.model_dump() for x in rule_index.outs(session, rules)],
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
    session: Session = Depends(get_session, scope="function"),
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
