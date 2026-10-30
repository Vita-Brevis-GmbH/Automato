"""Objekt- und Inventar-Ansicht (ADR 0010).

- `GET /objects`              Firewall-Objekte wie auf dem Geraet (IPv4 und IPv6 gleichnamig =
                              zwei Objekte): Werte mit Abgleich gegen das IPAM, Verwendung in
                              Regeln (auch ueber Gruppen) und Gruppen, Befunde; Filter
                              Quellsystem/Kategorie/Suche/„nur Abweichungen", Typ (`obj_type`,
                              z. B. dynamische SDN-Objekte) und Standardobjekte des Herstellers
                              (`predefined=include|exclude|only`, 0.36.0: zuunterst); `types` =
                              Anzahl je Typ fuer den Filter; seit 0.117.0 Praefix der
                              Namenskonvention (`prefix`, `-` = ohne) mit `prefixes` = Anzahl je
                              Praefix des Namensprofils.
- `GET /objects/summary`      Anzahl je Kategorie, Typ und Praefix (Uebersicht, 0.117.0).
- `GET /inventory/{art}`      Netzinventar: devices | interfaces | vlans | subnets | vrfs;
                              Subnetze mit Abgleich gegen das fuehrende IPAM und die Anreicherung.
Lesen = `viewer`, mandantsgescoped. Rein lesend.
"""

from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, func, literal, or_, select
from sqlalchemy.orm import Session

from app import naming
from app.api.customer_filter import customer_view, own_view
from app.audit import reads
from app.auth.deps import require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import (
    GROUP_CATEGORIES,
    SERVICE_CATEGORIES,
    DeviceObject,
    InvDevice,
    InvInterface,
    InvSubnet,
    InvVlan,
    InvVrf,
    SourceSystem,
)
from app.naming_defaults import DEFAULT_DICTIONARY
from app.store import customer_infra, references, rule_index, sites
from app.store import naming_profiles as profile_store
from app.store import objects as objects_store

router = APIRouter(tags=["objects"])

_ISSUE_STATUSES = {"unmatched", "ambiguous"}


def _tenant(principal: Principal, tenant_id: int | None) -> int:
    tid = tenant_scope(principal, tenant_id)
    if tid is None:
        raise HTTPException(status_code=422, detail="tenant_id required")
    return tid


def _predefined():
    """SQL: Standardobjekt des Herstellers (wie `naming.predefined_service`)."""
    name = func.lower(DeviceObject.name)
    return and_(
        DeviceObject.category.in_(SERVICE_CATEGORIES),
        or_(
            and_(
                or_(DeviceObject.obj_type.is_(None), DeviceObject.obj_type != "vzFilter"),
                name.in_(sorted(naming.FORTIOS_PREDEFINED_SERVICES)),
            ),
            and_(
                DeviceObject.obj_type == "vzFilter",
                name.in_(sorted(naming.ACI_DEFAULT_FILTERS)),
            ),
        ),
    )


def name_prefixes(session: Session, tid: int) -> dict[str, str]:
    """Praefixe der Objektnamen aus dem Namensprofil des Mandanten (Woerterbuch `object_prefix`,
    auch eigene wie `CVS_`) -> Bedeutung; ohne gepflegte Eintraege die Vorgabe der Plattform."""
    entries = profile_store.entries_for_tenant(session, tid, "object_prefix")
    return entries or dict(DEFAULT_DICTIONARY["object_prefix"])


def _prefix_of(prefixes: list[str]):
    """SQL: das Praefix des Namens (laengstes zuerst, wie `naming.parse_object_name`), sonst ''."""
    ordered = sorted(prefixes, key=len, reverse=True)
    if not ordered:
        return literal("")
    return case(*[(DeviceObject.name.startswith(p, autoescape=True), p) for p in ordered], else_="")


def _prefix_counts(session: Session, stmt, prefixes: list[str]) -> dict[str, int]:
    sub = stmt.add_columns(_prefix_of(prefixes).label("prefix")).subquery()
    return {
        p or "": n
        for p, n in session.execute(select(sub.c.prefix, func.count()).group_by(sub.c.prefix))
    }


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
    obj_type: str | None = Query(None, description="Typ(en), kommagetrennt; `-` = ohne Typ"),
    prefix: str | None = Query(
        None, max_length=500, description="Praefix(e) der Konvention, kommagetrennt; `-` = ohne"
    ),
    predefined: Literal["include", "exclude", "only"] = "include",
    object_id: int | None = Query(None, description="nur dieses Objekt (Link „Objekt öffnen“)"),
    customer_id: int | None = None,
    limit: int = 200,
    offset: int = 0,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    tid = _tenant(principal, tenant_id)
    reads.maybe_audit_read(session, principal, tid, "objects")
    rule_index.ensure(session, tid)
    stmt = select(DeviceObject).where(DeviceObject.tenant_id == tid)
    view = customer_view(session, principal, tid, customer_id)
    if view is not None:  # Objekte des Kunden: Werte bzw. von seinen Regeln verwendet
        stmt = stmt.where(DeviceObject.id.in_(view.device_objects()))
    own = view.ids("rule") if principal.is_customer and view else None  # nur seine Regeln
    if source_system_id is not None:
        stmt = stmt.where(DeviceObject.source_system_id == source_system_id)
    if object_id is not None:
        stmt = stmt.where(DeviceObject.id == object_id)
    if category:  # eine oder mehrere Kategorien, kommagetrennt
        stmt = stmt.where(DeviceObject.category.in_([c.strip() for c in category.split(",")]))
    # Typen (vor dem Typ-Filter gezaehlt): fuer die Auswahl, z. B. „dynamic“ (SDN/FortiManager)
    types = _types(session, stmt)
    if obj_type:
        wanted = [t.strip() for t in obj_type.split(",") if t.strip()]
        named = [t for t in wanted if t != "-"]
        stmt = stmt.where(
            or_(
                DeviceObject.obj_type.in_(named),
                *([DeviceObject.obj_type.is_(None)] if "-" in wanted else []),
            )
        )
    # Praefix der Namenskonvention (0.117.0, Anwender: „filtern nach Typ gemäss Namenskonvention
    # (V-EXT, CVS …)“): Anzahl je Praefix vor dem Praefix-Filter
    known = list(name_prefixes(session, tid))
    prefix_counts = _prefix_counts(session, stmt, known)
    if prefix:
        wanted = [p.strip() for p in prefix.split(",") if p.strip()]
        stmt = stmt.where(_prefix_of(known).in_([("" if p == "-" else p) for p in wanted]))
    pre = _predefined()
    if predefined == "exclude":
        stmt = stmt.where(~pre)
    elif predefined == "only":
        stmt = stmt.where(pre)
    predefined_count = (
        session.scalar(select(func.count()).select_from(stmt.where(pre).subquery())) or 0
    )
    # Standardobjekte des Herstellers zuunterst (Dev-Befund 0.35.0: nicht verwenden)
    stmt = stmt.order_by(case((pre, 1), else_=0), DeviceObject.name, DeviceObject.family,
                         DeviceObject.id)  # fmt: skip
    extra = {"types": types, "predefined": predefined_count, "prefixes": prefix_counts}
    if not q and not issues_only:  # seitenweise per SQL (grosse Objekt-Bestaende)
        total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        page = list(session.scalars(stmt.offset(offset).limit(min(limit, 2000))))
        return {"total": total, "rows": _object_rows(session, tid, page, own), **extra}
    objects = list(session.scalars(stmt))
    if q:
        # Name, Kommentar, Werte, FQDN, Interface, Mitglieder und die DNS-Namen der Werte aus
        # dem IPAM (dort steht der Server ohne Praefix: web01.example.ch fuer H_web01)
        ql = q.strip().lower()
        ctx = references.context(session, tid)

        def _hit(o: DeviceObject) -> bool:
            texts = [o.name, o.comment, o.fqdn, o.interface, o.vdom]
            texts += [*(o.values or []), *(o.members or [])]
            texts.append(str(o.attrs or ""))  # Service-Attribute (Ports, Kategorie, ...)
            texts.append({"v4": "ipv4", "v6": "ipv6"}.get(o.family or "", ""))
            if any(ql in (t or "").lower() for t in texts):
                return True
            names = [n for v in o.values or [] for n in references.dns_names(ctx, v)]
            return any(ql in n.lower() for n in names)

        objects = [o for o in objects if _hit(o)]
    if issues_only:
        issues = objects_store.object_issues(session, tid, objects)
        objects = [o for o in objects if issues.get(o.id)]
    page = objects[offset : offset + min(limit, 2000)]
    return {"total": len(objects), "rows": _object_rows(session, tid, page, own), **extra}


@router.get("/objects/summary")
def objects_summary(
    tenant_id: int | None = None,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Anzahl Objekte je Kategorie, Typ und Praefix der Namenskonvention (Uebersicht, 0.117.0);
    mit Kunde nur seine Objekte."""
    tid = _tenant(principal, tenant_id)
    stmt = select(DeviceObject).where(DeviceObject.tenant_id == tid)
    view = customer_view(session, principal, tid, customer_id)
    if view is not None:
        stmt = stmt.where(DeviceObject.id.in_(view.device_objects()))
    sub = stmt.subquery()
    by_category = {
        c.value if hasattr(c, "value") else str(c): n
        for c, n in session.execute(
            select(sub.c.category, func.count()).group_by(sub.c.category)
        )
    }  # fmt: skip
    prefixes = name_prefixes(session, tid)
    return {
        "total": sum(by_category.values()),
        "by_category": by_category,
        "by_type": _types(session, stmt),
        "by_prefix": _prefix_counts(session, stmt, list(prefixes)),
        "prefixes": prefixes,
    }


def _types(session: Session, stmt) -> dict[str, int]:
    """Anzahl Objekte je Typ (`obj_type`; leer = ohne Typ) fuer die gefilterte Menge."""
    sub = stmt.subquery()
    return {
        t or "": n
        for t, n in session.execute(select(sub.c.obj_type, func.count()).group_by(sub.c.obj_type))
    }


def _object_rows(session: Session, tid: int, objects: list[DeviceObject], only=None) -> list[dict]:
    """Zeilen der Objekt-Ansicht: Werte mit IPAM-Abgleich, Verwendung (Regeln direkt und ueber
    Gruppen aus dem Regelindex), Gruppen derselben Adressfamilie, Befunde."""
    used = rule_index.usage_counts(session, [o.id for o in objects], only)
    # Gruppen-Zugehoerigkeit ueber alle Gruppen des Mandanten (nicht nur die gefilterten Objekte);
    # IPv4-Gruppen enthalten IPv4-Objekte, IPv6-Gruppen IPv6-Objekte - je VDOM (ADR 0019)
    member_of: dict[tuple[int, str | None, str], list[tuple[str, str | None]]] = defaultdict(list)
    for sid, vdom, gname, gfamily, members in session.execute(
        select(
            DeviceObject.source_system_id,
            DeviceObject.vdom,
            DeviceObject.name,
            DeviceObject.family,
            DeviceObject.members,
        ).where(DeviceObject.tenant_id == tid, DeviceObject.category.in_(GROUP_CATEGORIES))
    ):
        for m in members or []:
            member_of[(sid, vdom, m)].append((gname, gfamily))
    issues = objects_store.object_issues(session, tid, objects)

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
            for g, gfam in member_of.get((o.source_system_id, o.vdom, o.name), [])
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
                "vdom": o.vdom,
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
                # Standardobjekt des Herstellers (FortiOS-Service, ACI-Filter in common)
                "predefined": naming.predefined_service(o.category.value, o.obj_type, o.name),
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
    view = own_view(session, principal, o.tenant_id)
    reads.maybe_audit_read(session, principal, o.tenant_id, "objects")
    if view is not None and o.id not in set(session.scalars(view.device_objects())):
        raise HTTPException(status_code=404, detail="object not found")  # ADR 0018
    rule_index.ensure(session, o.tenant_id)
    own = view.ids("rule") if view else None
    (row,) = _object_rows(session, o.tenant_id, [o], own)
    rule_ids = rule_index.rule_ids_using(session, o.id)
    if view is not None:  # nur die eigenen Regeln, keine Gesamtzahl darueber hinaus
        mine = set(session.scalars(own))
        rule_ids = [r for r in rule_ids if r in mine]
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
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    tid = _tenant(principal, tenant_id)
    # Kunde oben gewaehlt (0.76.0): nur sein Ausschnitt, geteilte Zeilen markiert (`shared`)
    infra = customer_infra.of(session, customer_view(session, principal, tid, customer_id))
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
        mark = _inventory_mark(infra, kind, r) if infra is not None else None
        if infra is not None and mark is None:
            continue
        row = {c.name: getattr(r, c.name) for c in model.__table__.columns}
        row["source"] = names.get(r.source_system_id)
        if infra is not None:
            row["shared"] = mark == customer_infra.SHARED
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


def _inventory_mark(infra: customer_infra.Infra, kind: str, r) -> str | None:
    """Inventar im Ausschnitt eines Kunden: Geraete an seinen Ports bzw. Kontexten, Ports mit
    seinen EPGs/VLANs/Netzen, VLANs seiner EPGs bzw. Netze, Subnetze in seinen Netzen, VRFs."""
    if kind == "devices":
        return infra.device_mark(r.source_system_id, r.name)
    if kind == "interfaces":
        return infra.ports.get((r.source_system_id, r.device, r.name))
    if kind == "vlans":
        return customer_infra._mark(
            *(infra.epg_paths.get(p) for p in r.epgs or []), infra.vlans.get(r.vid)
        )
    if kind == "subnets":
        return infra.net_mark(r.prefix)
    if kind == "vrfs":
        return infra.context_mark(r.source_system_id, r.name)
    return None


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
