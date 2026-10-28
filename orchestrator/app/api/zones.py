"""Logische Zonen, Zonenmatrix und Zonenplan (0.51.0, ADR 0024).

Zonen werden in Automato gepflegt: Selektoren wie beim Kunden (ACI-Tenant/VRF/AP/EPG/BD,
VDOM/Zone/Interface, Netz, Kuerzel, IPAM-Merkmal) und dazu `exit` (Netze hinter einer
Anbindung). Die Netze je Zone, die Matrix und der Plan sind abgeleitet (`store/zones.py`).
Lesen: Viewer; Zonen und Absicht je Zonenpaar aendern: Operator. Kunden-Benutzer (seit 0.53.0,
ADR 0018 Schritt 4) lesen ihre Zonen, die Matrix mit ihren Zonen (fremde Kunden als „anderer
Kunde“) und ihren Plan samt Export - keine Selektoren, Vorschau, Versionen oder Netze ohne Zone.
Nur Daten der Plattform - kein Zugriff auf Geraete.

Seit 0.52.0: Export des Plans (draw.io, SVG, PDF; `store/plan_export.py`) und Versionen
(`store/plan_versions.py`): Stand speichern (Operator), ansehen, vergleichen, exportieren,
loeschen (Admin).

Seit 0.54.0: Vorschlaege aus dem Gelesenen (`store/zone_detect.py`, lesen Viewer):
uebernehmen (neue Zone bzw. Selektoren einer bestehenden ergaenzen), ausblenden und wieder
vorschlagen (Operator). Nicht fuer Kunden-Benutzer.

Seit 0.55.0: die Vorschlaege richten sich nach dem Grundschema im Namensprofil; die Absichten,
die es zwischen bestehenden Zonen vorgibt, uebernimmt `POST /zones/schema/intents` (Operator).
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app import audit
from app.api.customer_filter import customer_view
from app.audit import reads
from app.auth.deps import require_admin, require_operator, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session, get_session_factory
from app.i18n import L
from app.models import (
    EXIT_SELECTOR_KINDS,
    INTENTS,
    ZONE_KINDS,
    ZONE_SELECTOR_KINDS,
    Customer,
    LogicalZone,
    LogicalZoneSelector,
    SourceSystem,
    Tenant,
    ZonePlanVersion,
    ZonePolicy,
)
from app.store import (
    app_refresh,
    appdoc_catalog,
    customer_alias,
    plan_export,
    plan_import,
    plan_versions,
    plan_view,
    reconcile,
    word_export,
    zone_assist,
    zone_detect,
    zone_services,
    zone_struct,
    zone_types,
)
from app.store import customers as customer_store
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
    # Bereich der Kategorien in der Applikations-Doku (0.101.0); nicht mitgeschickt = bleibt
    appdoc_area: str | None = None
    # Zonentyp von Hand (0.105.0): Zone des Grundschemas; leer = automatisch; nicht mitgeschickt
    # = bleibt
    zone_type: str | None = Field(default=None, max_length=200)


class AppdocAreasIn(BaseModel):
    """Zonen den Kategorien der Applikations-Doku zuordnen (0.101.0): Zone -> Bereich
    (`inet`, `bit`, `vpn`, `sra`, `res`) bzw. leer = keiner; nicht genannte Zonen bleiben."""

    tenant_id: int
    areas: dict[int, str | None] = Field(max_length=1000)


class PreviewIn(BaseModel):
    tenant_id: int
    zone_id: int | None = None
    selectors: list[SelectorIn] = Field(default_factory=list, max_length=200)


class AssistIn(BaseModel):
    """Zone beim Erfassen (0.66.0): Name, Kurzname, Art und die Selektoren bis jetzt."""

    tenant_id: int
    name: str = Field(default="", max_length=200)
    code: str | None = Field(default=None, max_length=50)
    kind: str | None = Field(default=None, max_length=32)
    zone_id: int | None = None
    selectors: list[SelectorIn] = Field(default_factory=list, max_length=200)


class VersionIn(BaseModel):
    tenant_id: int
    label: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=4000)


class PlanImportIn(BaseModel):
    """draw.io-Datei des Zonenplans zurueck einlesen (0.83.0): welche Teile uebernommen
    werden."""

    tenant_id: int
    content: str = Field(min_length=1, max_length=2_000_000)
    positions: bool = True
    renames: bool = True
    intents: bool = True
    new_zones: bool = False


class ArrangementIn(BaseModel):
    """Anordnung des Zonenplans (0.61.0): Position je Zone (`{"<id>": [x, y]}`) in der Ansicht
    `overview`, `all` bzw. `customer:<id>`; gesetzte Zonen ersetzen ihre Position."""

    tenant_id: int
    view: str = Field(pattern=r"^(overview|all|customer:\d+)$")
    positions: dict[str, list[float]] = Field(max_length=plan_view.MAX_POSITIONS)


class KeysIn(BaseModel):
    tenant_id: int
    keys: list[str] = Field(min_length=1, max_length=500)  # Vorschlaege (normalisierter Name)


class SchemaIntentsIn(BaseModel):
    tenant_id: int
    pairs: list[tuple[int, int]] | None = Field(
        default=None, max_length=5000
    )  # leer = alle fehlenden
    overwrite: bool = False  # auch abweichende Absichten ersetzen (nur mit `pairs`)


class PolicyIn(BaseModel):
    tenant_id: int
    src_zone_id: int
    dst_zone_id: int
    intent: str | None = None  # leer = keine Absicht (entfernen)
    note: str | None = Field(default=None, max_length=2000)
    # Absicht je Dienst (0.57.0): bei „eingeschraenkt“ die erlaubten Dienste
    services: list[str] | None = Field(default=None, max_length=200)


def _scope(principal: Principal) -> tuple[int, ...] | None:
    """Kunden eines Kunden-Benutzers (Sicht nur auf seine Zonen, 0.53.0); None = Betreuer."""
    return tuple(principal.customer_ids or ()) if principal.is_customer else None


def _filter_scope(session: Session, principal: Principal, tid: int,
                  customer_id: int | None) -> tuple[int, ...] | None:  # fmt: skip
    """Sicht fuer Matrix und Liste: Kunden-Benutzer wie bisher, Betreuer mit oben gewaehltem
    Kunden (0.76.0) dessen Sicht - die Gegenseite fremder Kunden als „anderer Kunde“."""
    if principal.is_customer or customer_id is None:
        return _scope(principal)
    customer_view(session, principal, tid, customer_id)  # 404 bei fremdem Kunden
    return (customer_id,)


def _plan_filter(
    q: str = Query("", max_length=200, description="Suche: Zone, Kurzname, Kunde, Knoten, Netz"),
    focus: Annotated[
        list[str] | None, Query(description="Fokus auf Zonen (ID, customer:<id>)")
    ] = None,
    depth: int = Query(1, ge=0, le=plan_view.MAX_DEPTH, description="Nachbarn bis so weit"),
    hide_layer: Annotated[
        list[str] | None, Query(description="Ebenen ausblenden (dc, edge, …)")
    ] = None,
    hide: Annotated[list[str] | None, Query(description="nodes, labels, contacts, empty")] = None,
    flows: bool = Query(False, description="Kommunikation (Regeln je Zonenpaar) einblenden"),
) -> plan_view.Filter:
    """Filter des Zonenplans (0.61.0) - Oberflaeche und Export gleich."""
    layers, elements = hide_layer or [], hide or []
    unknown = [x for x in layers if x not in plan_view.LAYERS]
    unknown += [x for x in elements if x not in plan_view.ELEMENTS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown layer: {', '.join(unknown)}")
    return plan_view.Filter(q=q, focus=(focus or [])[:200], depth=depth, hide_layers=set(layers),
                            hide=set(elements), flows=flows)  # fmt: skip


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
            if s.kind not in (*customer_store.FW_KINDS, *EXIT_SELECTOR_KINDS):
                raise HTTPException(status_code=422, detail="source system only for FortiGate")
            if s.source_system_id not in sids:
                raise HTTPException(status_code=404, detail="source system not found")
        out.append(store.Selector(s.kind, value, s.source_system_id, s.exclude))
    return out


def _out(z: LogicalZone, nets: store.ZoneNets | None = None,
         customers: dict[int, str] | None = None, color: dict | None = None) -> dict:  # fmt: skip
    return {
        **store.zone_out(z, nets, color),
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


def _customer_out(z: LogicalZone, nets: store.ZoneNets | None, names: dict[int, str],
                  own: bool, color: dict | None = None) -> dict:  # fmt: skip
    """Zone in der Sicht eines Kunden-Benutzers: ohne Selektoren (Infrastruktur); geteilte
    Zonen nur mit Name, Art und Farbe."""
    out = _out(z, nets if own else None, names, color)
    out["selectors"] = []
    if not own:
        out["description"] = None
    out["own"] = own
    return out


def _customers(session: Session, tid: int) -> dict[int, str]:
    return dict(
        session.execute(select(Customer.id, Customer.name).where(Customer.tenant_id == tid)).all()
    )


def _audit(session: Session, principal: Principal, action: str, z: LogicalZone,
           via: str | None = None) -> None:  # fmt: skip
    detail = {
        "tenant_id": z.tenant_id,
        "zone_id": z.id,
        "name": z.name,
        "kind": z.kind,
        "selectors": len(z.selectors),
    }
    if via:
        detail["via"] = via
    audit.record(session, actor=f"user:{principal.subject}", action=f"zone.{action}",
                 subject=str(z.id), detail=detail)  # fmt: skip


def _get(session: Session, principal: Principal, zone_id: int) -> LogicalZone:
    z = session.get(LogicalZone, zone_id)
    if z is None:
        raise HTTPException(status_code=404, detail="zone not found")
    tenant_scope(principal, z.tenant_id)
    return z


def _rederive(session: Session, tid: int, customer_ids: set[int | None]) -> None:
    """Zugehoerigkeit der Kunden neu ableiten, deren Zonen sich geaendert haben (0.53.0)."""
    for cid in sorted(c for c in customer_ids if c is not None):
        customer_store.derive(session, tid, cid)


def _zone_type(session: Session, tenant_id: int, value: str | None) -> str | None:
    """Zonentyp von Hand pruefen (0.105.0): eine Zone des Grundschemas (wie dort geschrieben);
    leer = automatisch."""
    value = (value or "").strip() or None
    if value is None:
        return None
    hit = zone_types.schema_for(session, tenant_id).zone(value)
    if hit is None:
        raise HTTPException(status_code=422, detail=f"unknown zone type {value!r}")
    return hit.name


def _area(value: str | None, catalog: appdoc_catalog.Catalog | None = None) -> str | None:
    """Bereich der Kategorien pruefen (0.101.0; seit 0.104.0 gegen den Katalog des Mandanten,
    Namensprofil); leer = keiner."""
    value = (value or "").strip() or None
    known = catalog.areas if catalog is not None else appdoc_catalog.default().areas
    if value is not None and (value not in known or known[value].off):
        raise HTTPException(status_code=422, detail=f"unknown appdoc area {value!r}")
    return value


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
    if "appdoc_area" in body.model_fields_set:  # 0.101.0: nur, wenn mitgeschickt
        z.appdoc_area = _area(body.appdoc_area, appdoc_catalog.for_tenant(session, z.tenant_id))
    if "zone_type" in body.model_fields_set:  # 0.105.0: nur, wenn mitgeschickt
        z.zone_type = _zone_type(session, z.tenant_id, body.zone_type)
    z.updated_at = datetime.now(UTC)
    z.selectors = [
        LogicalZoneSelector(kind=s.kind, value=s.value, source_system_id=s.source_system_id,
                            exclude=s.exclude)
        for s in sel
    ]  # fmt: skip


@router.get("")
def list_zones(
    tenant_id: int | None = None,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """Zonen der Umgebung in ihrer Reihenfolge, mit Selektoren und Anzahl Netze/EPGs; mit
    Kunde (0.76.0) seine Zonen und die geteilten an seinen Knoten (`shared`)."""
    tid = _tenant(session, principal, tenant_id)
    items = store.load(session, tid)
    color = store.colors(session, tid, items)
    chosen: tuple[set[int], set[int]] | None = None  # (sichtbar, eigen) bei gewaehltem Kunden
    if not principal.is_customer and customer_id is not None:
        scope = _filter_scope(session, principal, tid, customer_id)
        plan = store.plan_with_layout(session, tid, scope)
        cview = store.customer_view(items, scope)
        chosen = ({z["id"] for z in plan["zones"]} | cview.own, set(cview.own))
    if principal.is_customer:  # eigene Zonen und die geteilten, an denen er haengt (Plan)
        plan = store.plan_with_layout(session, tid, _scope(principal))
        view = store.customer_view(items, _scope(principal))
        visible = {z["id"] for z in plan["zones"]} | view.own
        nets = {n.id: n for n in store.derive(session, tid, items)}
        names = _customers(session, tid)
        return [_customer_out(z, nets.get(z.id), names, z.id in view.own, color.get(z.id))
                for z in items if z.id in visible]  # fmt: skip
    data = customer_store._Data(session, tid)
    st = zone_struct.build(session, tid, items, data)
    nets = {n.id: n for n in store.derive(session, tid, items, data, st)}
    names = _customers(session, tid)
    zone_nodes = st.zone_nodes()
    by_id = {z.id: z for z in items}
    out = []
    for z in items:
        if chosen is not None and z.id not in chosen[0]:
            continue
        row = _out(z, nets.get(z.id), names, color.get(z.id))
        if chosen is not None:
            row["shared"] = z.id not in chosen[1]
        row["nodes"] = [st.graph.nodes[k].label for k in zone_nodes.get(z.id, [])]
        row["transitions"] = [
            {"to": _side_name(t.b if t.a == z.id else t.a, by_id),
             "to_id": t.b if t.a == z.id else t.a,
             "name": _transition_name(z, t.b if t.a == z.id else t.a, by_id),
             "count": len(t.networks)}
            for t in st.of_zone(z.id)
        ]  # fmt: skip
        out.append(row)
    return out


def _side_name(side, by_id: dict[int, LogicalZone]) -> str:
    if isinstance(side, int):
        z = by_id.get(side)
        return z.name if z else f"#{side}"
    return store.pseudo_name(side)


def _side(side, by_id: dict[int, LogicalZone]) -> dict:
    z = by_id.get(side) if isinstance(side, int) else None
    if z is None:
        return {"name": _side_name(side, by_id), "code": None, "kind": side, "position": 0}
    return {"name": z.name, "code": z.code, "kind": z.kind, "position": z.position}


def _transition_name(z: LogicalZone, other, by_id: dict[int, LogicalZone]) -> str:
    return store.transition_name(_side(z.id, {z.id: z, **by_id}), _side(other, by_id))


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


@router.post("/assist")
def assist(
    body: AssistIn,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Vorschlaege beim Erfassen einer Zone (0.66.0): Firewall-Zonen, Interfaces, VDOMs, IPAM-Netze
    und -Merkmale, ACI-Objekte und Anbindungen, deren Name zur Zone passt - je mit Netzen und
    Ueberschneidungen. Nur lesend."""
    tid = _tenant(session, principal, body.tenant_id)
    return zone_assist.suggest(session, tid, body.name, body.code, body.kind,
                               _selectors(session, tid, body.selectors), body.zone_id)  # fmt: skip


@router.get("/suggestions")
def suggestions(
    tenant_id: int | None = None,
    dismissed: bool = Query(False, description="auch ausgeblendete"),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Vorschlaege fuer Zonen aus dem Gelesenen (Transfernetze, FortiGate-Zonen und
    -Interfaces, IPAM-Merkmal, ACI-VRFs, Anbindungen) und die Netze ohne Zone und Vorschlag."""
    tid = _tenant(session, principal, tenant_id)
    return zone_detect.suggest(session, tid, include_dismissed=dismissed)


@router.post("/suggestions/accept")
def accept_suggestions(
    body: KeysIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Vorschlaege uebernehmen (Operator): neue Zone mit den Selektoren des Vorschlags bzw.
    die Selektoren einer bestehenden Zone ergaenzen. Ausgeblendete auch."""
    tid = tenant_scope(principal, body.tenant_id)
    if session.get(Tenant, tid) is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    found = {s["key"]: s for s in zone_detect.suggest(session, tid, True)["items"]}
    keys = list(dict.fromkeys(body.keys))
    missing = [k for k in keys if k not in found]
    last = session.scalar(
        select(func.max(LogicalZone.position)).where(LogicalZone.tenant_id == tid)
    )
    position, owners = last or 0, set()
    created, updated = [], []
    for key in keys:
        if key in missing:
            continue
        s = found[key]
        sel = [
            LogicalZoneSelector(kind=x["kind"], value=x["value"],
                                source_system_id=x["source_system_id"], exclude=x["exclude"])
            for x in s["selectors"]
        ]  # fmt: skip
        if s["action"] == "extend":
            z = _get(session, principal, s["zone_id"])
            z.selectors = [*z.selectors, *sel]
            z.updated_at = datetime.now(UTC)
            session.flush()
            _audit(session, principal, "updated", z, via="suggestion")
            updated.append(z.id)
        else:
            name, n = s["name"], 1
            taken = set(
                session.scalars(select(LogicalZone.name).where(LogicalZone.tenant_id == tid))
            )
            while name in taken:
                n += 1
                name = f"{s['name']} ({n})"
            position += 10
            # Beschreibung wird gespeichert: deutsch (ADR 0028)
            sources = ", ".join(zone_detect.SOURCE_LABEL[x] for x in s["sources"])
            z = LogicalZone(tenant_id=tid, name=name[:200], kind=s["kind"], color=s["color"],
                            code=(s.get("code") or None) and s["code"][:50],
                            customer_id=s["customer_id"], position=position, selectors=sel,
                            description=f"Vorschlag aus {sources}")  # fmt: skip
            session.add(z)
            session.flush()
            _audit(session, principal, "created", z, via="suggestion")
            created.append(z.id)
        owners.add(z.customer_id)
    zone_detect.undismiss(session, tid, [k for k in keys if k not in missing])
    _rederive(session, tid, owners)
    reconcile.refresh_zone_findings(session, tid)
    return {"created": created, "updated": updated, "missing": missing}


def _dismiss(session: Session, principal: Principal, body: KeysIn, undo: bool) -> dict:
    tid = tenant_scope(principal, body.tenant_id)
    if session.get(Tenant, tid) is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    keys = [k.strip()[:200] for k in body.keys if k.strip()]
    if undo:
        changed = zone_detect.undismiss(session, tid, keys)
    else:
        changed = zone_detect.dismiss(session, tid, keys, principal.subject)
    if changed:
        audit.record(
            session,
            actor=f"user:{principal.subject}",
            action=f"zone_suggestion.{'undismissed' if undo else 'dismissed'}",
            subject=str(tid),
            detail={"tenant_id": tid, "keys": changed},
        )
    return {"keys": changed}


@router.post("/suggestions/dismiss")
def dismiss_suggestions(
    body: KeysIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Vorschlaege nicht mehr zeigen (Operator) - nur Daten der Plattform."""
    return _dismiss(session, principal, body, undo=False)


@router.post("/suggestions/dismiss/undo")
def undismiss_suggestions(
    body: KeysIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Ausgeblendete Vorschlaege wieder zeigen (Operator)."""
    return _dismiss(session, principal, body, undo=True)


@router.post("/schema/intents")
def apply_schema_intents(
    body: SchemaIntentsIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Absichten aus dem Grundschema setzen (Operator): ohne `pairs` alle fehlenden; mit
    `pairs` nur diese, abweichende nur mit `overwrite`."""
    tid = tenant_scope(principal, body.tenant_id)
    if session.get(Tenant, tid) is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    wanted = {tuple(p) for p in body.pairs} if body.pairs is not None else None
    changed = []
    for x in zone_detect.schema_intents(session, tid):
        pair = (x["src_zone_id"], x["dst_zone_id"])
        if wanted is not None and pair not in wanted:
            continue
        if x["current"] is not None and not (body.overwrite and wanted is not None):
            continue
        p = session.scalar(
            select(ZonePolicy).where(ZonePolicy.src_zone_id == pair[0],
                                     ZonePolicy.dst_zone_id == pair[1])
        )  # fmt: skip
        if p is None:
            p = ZonePolicy(tenant_id=tid, src_zone_id=pair[0], dst_zone_id=pair[1])
            session.add(p)
        note = f"Grundschema: {x['schema_src']} → {x['schema_dst']}"
        p.intent, p.note = x["intent"], (f"{note} ({x['note']})" if x["note"] else note)[:2000]
        # Dienste aus dem Schema (`automato_services`, 0.57.0), nur bei „eingeschraenkt“
        p.services = (x.get("services") or None) if x["intent"] == "limited" else None
        p.updated_by, p.updated_at = principal.subject, datetime.now(UTC)
        session.flush()
        audit.record(
            session,
            actor=f"user:{principal.subject}",
            action="zone_policy.set",
            subject=f"{pair[0]}>{pair[1]}",
            detail={
                "tenant_id": tid,
                "src_zone_id": pair[0],
                "dst_zone_id": pair[1],
                "intent": x["intent"],
                "services": p.services,
                "via": "schema",
            },  # fmt: skip
        )
        changed.append({"src": pair[0], "dst": pair[1], "intent": x["intent"],
                        "before": x["current"]})  # fmt: skip
    if changed:
        reconcile.refresh_zone_findings(session, tid)
    return {"changed": changed}


@router.get("/matrix")
def matrix(
    tenant_id: int | None = None,
    source_system_id: int | None = None,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Zonenmatrix: je Paar Quell-/Zielzone die Regeln (erlaubend, sperrend), Absicht und
    Verstoesse; dazu `any` und `none` (Adressen ohne Zone)."""
    tid = _tenant(session, principal, tenant_id)
    scope = _filter_scope(session, principal, tid, customer_id)
    return store.matrix(session, tid, source_system_id, customer_ids=scope)


@router.get("/matrix/rules")
def matrix_rules(
    src: str,
    dst: str,
    tenant_id: int | None = None,
    source_system_id: int | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    customer_id: int | None = None,
    impossible: bool = False,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Regeln einer Zelle (Quellzone -> Zielzone; Zone-ID, `any` bzw. `none`); mit
    `impossible` die Regeln, die das Routing fuer die Zelle ausschliesst (0.81.0)."""
    tid = _tenant(session, principal, tenant_id)
    scope = _filter_scope(session, principal, tid, customer_id)
    try:
        cells = store.parse_cell(src), store.parse_cell(dst)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="src/dst: zone id, any, none or other") from exc
    return store.cell_rules(session, tid, *cells, limit=limit, offset=offset,
                            source_system_id=source_system_id,
                            customer_ids=scope, impossible=impossible)  # fmt: skip


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
    services = None
    if body.intent == "limited":  # Dienste nur bei „eingeschraenkt“
        try:
            services = zone_services.normalize(session, tid, body.services)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
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
        p.services = services
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
            "services": services,
        },  # fmt: skip
    )
    reconcile.refresh_zone_findings(session, tid)
    return {"src": body.src_zone_id, "dst": body.dst_zone_id, "intent": body.intent,
            "note": p.note if body.intent is not None else None,
            "services": services or []}  # fmt: skip


@router.get("/plan")
def plan(
    tenant_id: int | None = None,
    customer_id: int | None = Query(None, description="Plan dieses Kunden"),
    everything: bool = Query(False, alias="all", description="alle Zonen einzeln"),
    view_filter: plan_view.Filter = Depends(_plan_filter),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Zonenplan nach dem Zonenmodell: Zonen mit ihren Knoten und Netzen, Uebergaenge zwischen
    den Zonen; ohne Kunde die Uebersicht (Kunden je als eine Zone), mit Kunde dessen Plan. Seit
    0.61.0 gefiltert (Suche, Fokus, Ebenen) und mit der gespeicherten Anordnung."""
    tid = _tenant(session, principal, tenant_id)
    return store.plan_with_layout(session, tid, _scope(principal), customer_id, everything,
                                  view_filter)  # fmt: skip


@router.put("/plan/arrangement")
def save_arrangement(
    body: ArrangementIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Anordnung speichern (Operator, 0.61.0): gezogene Zonen behalten ihren Platz in dieser
    Ansicht - auch im Export und in neuen Versionen."""
    tid = tenant_scope(principal, body.tenant_id)
    try:
        positions = plan_view.clean_positions(body.positions)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row = plan_view.save(session, tid, body.view, positions, principal.subject)
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="zone_plan.arranged",
        subject=body.view,
        detail={"tenant_id": tid, "view": body.view, "zones": len(positions)},
    )
    return plan_view.arrangement_out(row, body.view)


def _plan_reading(session: Session, tid: int, content: str) -> "plan_import.Reading":
    try:
        return plan_import.read(session, tid, content)
    except plan_import.SchemaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/plan/import/preview")
def plan_import_preview(
    body: PlanImportIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Zonenplan aus draw.io (0.83.0, Operator): was die Datei gegenueber Automato aendert -
    Lage, Namen, Absichten, neue Formen (schreibt nichts)."""
    tid = tenant_scope(principal, body.tenant_id)
    return _plan_reading(session, tid, body.content).out()


@router.post("/plan/import")
def plan_import_apply(
    body: PlanImportIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Zonenplan aus draw.io uebernehmen (0.83.0, Operator): je Teil auf Wunsch; Audit
    `zone_plan.imported`."""
    tid = tenant_scope(principal, body.tenant_id)
    reading = _plan_reading(session, tid, body.content)
    try:
        done = plan_import.apply(session, tid, reading, principal.subject, body.positions,
                                 body.renames, body.intents, body.new_zones)  # fmt: skip
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="zone_plan.imported",
        subject=reading.view,
        detail={
            "tenant_id": tid,
            "view": reading.view,
            "positions": done["positions"],
            "renames": [[r["from"], r["to"]] for r in done["renames"]],
            "intents": done["intents"],
            "new_zones": done["new_zones"],
        },  # fmt: skip
    )
    if done["intents"] or done["renames"] or done["new_zones"]:
        reconcile.refresh_zone_findings(session, tid)  # Absichten, Zonen ausserhalb des Schemas
    return {"view": reading.view, **done}


@router.delete("/plan/arrangement", status_code=204)
def reset_arrangement(
    tenant_id: int,
    view: str = Query(pattern=r"^(overview|all|customer:\d+)$"),
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> None:
    """Anordnung zuruecksetzen (Operator): die Ansicht ordnet wieder nach Ebenen."""
    tid = tenant_scope(principal, tenant_id)
    if plan_view.reset(session, tid, view):
        audit.record(
            session,
            actor=f"user:{principal.subject}",
            action="zone_plan.arrangement_reset",
            subject=view,
            detail={"tenant_id": tid, "view": view},
        )


_FORMATS = {
    "svg": ("image/svg+xml", "svg"),
    "drawio": ("application/vnd.jgraph.mxfile", "drawio"),
    "pdf": ("application/pdf", "pdf"),
    # Export fuer Confluence (0.71.0, ADR 0026): Word bzw. Word + draw.io im ZIP
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    "zip": ("application/zip", "zip"),
}


def _version(session: Session, principal: Principal, version_id: int) -> ZonePlanVersion:
    v = session.get(ZonePlanVersion, version_id)
    if v is None or v.deleted_at is not None:
        raise HTTPException(status_code=404, detail="version not found")
    tenant_scope(principal, v.tenant_id)
    return v


@router.get("/plan/export")
def export_plan(
    format: str = Query("svg", pattern="^(svg|drawio|pdf|docx|zip)$"),  # noqa: A002 - Name der API
    tenant_id: int | None = None,
    version_id: int | None = None,
    customer_id: int | None = None,
    everything: bool = Query(False, alias="all"),
    view_filter: plan_view.Filter = Depends(_plan_filter),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> Response:
    """Zonenplan als Datei: draw.io (bearbeitbar), SVG oder PDF (mit Matrix und Tabellen), seit
    0.71.0 Word (`docx`) bzw. Word + draw.io im ZIP (`zip`) fuer den Import in Confluence -
    aktueller Stand (wie in der Ansicht gefiltert und angeordnet) bzw. eine gespeicherte
    Version."""
    if version_id is not None:
        if principal.is_customer:  # Versionen zeigen die ganze Umgebung
            raise HTTPException(status_code=403, detail="not available for customer users")
        v = _version(session, principal, version_id)
        tid = v.tenant_id
        reads.maybe_audit_read(session, principal, tid, "zones")
        plan, matrix = v.data.get("plan") or {}, v.data.get("matrix")
        when, meta = v.created_at, {"number": v.number, "label": v.label}
        name = f"zonenplan-v{v.number}"
    else:
        tid = _tenant(session, principal, tenant_id)
        plan = store.plan_with_layout(session, tid, _scope(principal), customer_id, everything,
                                      view_filter)  # fmt: skip
        matrix = None
        if format in ("pdf", "docx", "zip"):
            matrix = store.matrix(session, tid, customer_ids=_scope(principal))
        when, meta = datetime.now(UTC), None
        name = f"zonenplan-{when:%Y-%m-%d}"
    tenant = session.get(Tenant, tid)
    title = L(f"Zonenplan {tenant.name}", f"Zone plan {tenant.name}") if tenant else L(
        "Zonenplan", "Zone plan")  # fmt: skip
    view = (plan.get("view") or {}) if version_id is None else {}
    if view.get("customer"):
        title += f" – {view['customer']}"
    if principal.is_customer:  # Titel mit den eigenen Kunden, nicht der Umgebung
        names = sorted(n for cid, n in _customers(session, tid).items()
                       if cid in (principal.customer_ids or ()))  # fmt: skip
        title = L("Zonenplan ", "Zone plan ") + (", ".join(names) or L("Kunde", "Customer"))
        # ohne Geraetenamen (0.58.0) - die Middleware sieht die Datei nicht als JSON
        aliases = customer_alias.Aliases(session, [tid])
        plan, matrix = aliases.clean(plan), aliases.clean(matrix)
    media, ext = _FORMATS[format]
    if format == "svg":
        body: bytes | str = plan_export.svg(plan, title, when, meta)
    elif format == "drawio":
        body = plan_export.drawio(plan, title, when, meta)
    elif format in ("docx", "zip"):
        tenant_label = None if principal.is_customer or tenant is None else tenant.name
        drawio_name = f"{name}.drawio"
        body = word_export.zone_plan(plan, matrix, title, when, tenant_label,
                                     drawio_name if format == "zip" else None, meta)  # fmt: skip
        if format == "zip":
            drawing = plan_export.drawio(plan, title, when, meta)
            body = word_export.bundle([(f"{name}.docx", body), (drawio_name, drawing)])
    else:
        body = plan_export.pdf(plan, matrix, title, when, meta)
    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}.{ext}"'},
    )


@router.get("/plan/versions")
def list_versions(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Gespeicherte Versionen (neuste zuerst) und der Fingerabdruck des aktuellen Stands:
    gleich wie eine Version = seither unveraendert."""
    tid = _tenant(session, principal, tenant_id)
    items = list(
        session.scalars(
            select(ZonePlanVersion)
            .where(ZonePlanVersion.tenant_id == tid, ZonePlanVersion.deleted_at.is_(None))
            .order_by(ZonePlanVersion.number.desc())
        )
    )
    current = plan_versions.fingerprint(plan_versions.snapshot(session, tid))
    return {
        "current_fingerprint": current,
        "items": [plan_versions.version_out(v) for v in items],
    }


@router.post("/plan/versions", status_code=201)
def save_version(
    body: VersionIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Aktuellen Stand als Version speichern (Operator): Zonen, Plan, Matrix, Absichten."""
    tid = tenant_scope(principal, body.tenant_id)
    v = plan_versions.save(session, tid, body.label.strip(), (body.note or "").strip() or None,
                           principal.subject)  # fmt: skip
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="zone_plan.version_saved",
        subject=str(v.id),
        detail={"tenant_id": tid, "version_id": v.id, "number": v.number, "label": v.label},
    )
    return plan_versions.version_out(v)


@router.get("/plan/versions/{version_id}")
def get_version(
    version_id: int,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Eine Version mit dem gespeicherten Stand (Plan mit Zeichnung, Matrix, Zonen)."""
    v = _version(session, principal, version_id)
    reads.maybe_audit_read(session, principal, v.tenant_id, "zones")
    out = plan_versions.version_out(v, with_data=True)
    out["data"] = {**out["data"], "plan": {**(out["data"].get("plan") or {})}}
    out["data"]["plan"]["layout"] = plan_export.layout(out["data"]["plan"])
    return out


@router.get("/plan/versions/{version_id}/diff")
def diff_version(
    version_id: int,
    to: int | None = Query(None, description="andere Version; leer = aktueller Stand"),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Was sich seit der Version geaendert hat (bzw. bis zur Version `to`)."""
    v = _version(session, principal, version_id)
    reads.maybe_audit_read(session, principal, v.tenant_id, "zones")
    if to is None:
        new, against = plan_versions.snapshot(session, v.tenant_id), None
    else:
        other = _version(session, principal, to)
        if other.tenant_id != v.tenant_id:
            raise HTTPException(status_code=404, detail="version not found")
        new, against = other.data, other.number
    return {"from": v.number, "to": against, **plan_versions.diff(v.data, new)}


@router.delete("/plan/versions/{version_id}", status_code=204)
def delete_version(
    version_id: int,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
) -> None:
    """Version loeschen (Admin): der gespeicherte Stand faellt weg, die Nummer wird nicht wieder
    vergeben."""
    v = _version(session, principal, version_id)
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="zone_plan.version_deleted",
        subject=str(v.id),
        detail={
            "tenant_id": v.tenant_id,
            "version_id": v.id,
            "number": v.number,
            "label": v.label,
        },  # fmt: skip
    )
    v.data, v.deleted_at, v.deleted_by = {}, datetime.now(UTC), principal.subject


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
    _rederive(session, tid, {z.customer_id})
    reconcile.refresh_zone_findings(session, tid)
    nets = store.derive(session, tid, [z])
    return _out(z, nets[0], _customers(session, tid))


@router.put("/appdoc-areas")
def set_appdoc_areas(
    body: AppdocAreasIn,
    background: BackgroundTasks,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
    factory: sessionmaker = Depends(get_session_factory),
) -> list[dict]:
    """Zonen den Kategorien der Applikations-Doku zuordnen (0.101.0, Operator): je Zone der
    Bereich (Internet, Unternehmensnetzwerk, VPN, Remote Access, andere Security Tenants) bzw.
    keiner. Die Doku ordnet danach beim naechsten Laden neu; Befunde der Applikationen ziehen
    nach. Liefert die Zonen mit ihrem Bereich."""
    tid = tenant_scope(principal, body.tenant_id)
    items = {z.id: z for z in store.load(session, tid)}
    unknown = [zid for zid in body.areas if zid not in items]
    if unknown:
        raise HTTPException(status_code=404, detail="zone not found")
    changes: list[list] = []
    catalog = appdoc_catalog.for_tenant(session, tid)
    for zid, value in body.areas.items():
        area = _area(value, catalog)
        z = items[zid]
        if z.appdoc_area != area:
            z.appdoc_area, z.updated_at = area, datetime.now(UTC)
            changes.append([zid, area])
    session.flush()
    if changes:
        audit.record(session, actor=f"user:{principal.subject}", action="zone.appdoc_areas_set",
                     subject=str(tid), detail={"tenant_id": tid, "changes": changes})  # fmt: skip
        app_refresh.schedule_tenant(background, factory, tid)  # Befunde nach den Kategorien
    return [{"id": z.id, "name": z.name, "appdoc_area": z.appdoc_area} for z in items.values()]


@router.get("/appdoc/catalog")
def appdoc_catalog_of_tenant(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Kategorien der Applikations-Doku des Mandanten (0.104.0): Katalog aus Vorgaben und
    Namensprofil (Auswahl je Zone im Netzwerkdesign → Kategorien) und je Zone ohne Kategorie
    der Vorschlag nach ihrem Namen."""
    tid = tenant_scope(principal, tenant_id)
    catalog = appdoc_catalog.for_tenant(session, tid)
    items = store.load(session, tid)
    suggestions = {}
    for z in items:
        if not catalog.area(z.appdoc_area) and (key := catalog.suggest(z.name, z.code)):
            suggestions[z.id] = key
    # Zonentypen (0.105.0): Zonen des Grundschemas zur Auswahl, je Zone der wirksame Typ
    schema = zone_types.schema_for(session, tid)
    return {**catalog.out(), "suggestions": suggestions, "has_flows": catalog.has_flows,
            "zone_types": [z.name for z in schema.zones],
            "types": zone_types.types(session, tid, items, schema),
            "auto_types": zone_types.types(session, tid, items, schema, manual=False)}  # fmt: skip


class AppdocZoneIn(BaseModel):
    id: int
    appdoc_area: str | None = None  # nicht mitgeschickt = bleibt
    zone_type: str | None = Field(default=None, max_length=200)  # 0.105.0; fehlt = bleibt
    # Netze der Zone (Selektoren „Netz“) bzw. ihre Ausschluesse („ohne“ Netz); None = bleibt
    nets: list[str] | None = Field(default=None, max_length=500)
    excluded: list[str] | None = Field(default=None, max_length=500)


class AppdocZonesIn(BaseModel):
    tenant_id: int
    zones: list[AppdocZoneIn] = Field(max_length=1000)


def _prefix_list(values: list[str]) -> list[str]:
    """Netze pruefen und schreiben wie gelesen (CIDR, ohne doppelte)."""
    out: list[str] = []
    for v in values:
        net = customer_store._net(v.strip())
        if net is None:
            raise HTTPException(status_code=422, detail=f"invalid prefix {v.strip()!r}")
        if str(net) not in out:
            out.append(str(net))
    return out


def _prefixes_of(z: LogicalZone, exclude: bool) -> list[str]:
    return [s.value for s in z.selectors if s.kind == "prefix" and s.exclude is exclude]


@router.put("/appdoc")
def set_appdoc_zones(
    body: AppdocZonesIn,
    background: BackgroundTasks,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
    factory: sessionmaker = Depends(get_session_factory),
) -> list[dict]:
    """Kategorien der Applikations-Doku in Netzwerkdesign → Zonen pflegen (0.102.0, Operator,
    Anwender: „das Zuordnen gehört zu den Zonen ins Netzwerkdesign“): je Zone die Kategorie und
    ihre Netze mit Ausschluessen (Selektoren „Netz“ bzw. „ohne“ Netz); die anderen Selektoren
    (ACI, Firewall, IPAM, Anbindung) bleiben. Spaeter laesst sich daraus z. B. ein BGP-Filter je
    Zone ableiten. Liefert die Zonen mit Kategorie, Netzen und Ausschluessen."""
    tid = tenant_scope(principal, body.tenant_id)
    items = {z.id: z for z in store.load(session, tid)}
    if any(x.id not in items for x in body.zones):
        raise HTTPException(status_code=404, detail="zone not found")
    changes: list[dict] = []
    owners: set[int | None] = set()
    catalog = appdoc_catalog.for_tenant(session, tid)
    for x in body.zones:
        z, entry = items[x.id], {}
        if "appdoc_area" in x.model_fields_set:
            area = _area(x.appdoc_area, catalog)
            if z.appdoc_area != area:
                z.appdoc_area = entry["area"] = area
        if "zone_type" in x.model_fields_set:  # Zonentyp von Hand (0.105.0)
            kind = _zone_type(session, tid, x.zone_type)
            if z.zone_type != kind:
                z.zone_type = entry["type"] = kind
                owners.add(z.customer_id)  # Befund „ausserhalb des Schemas“ neu
        if x.nets is not None or x.excluded is not None:
            nets = _prefix_list(x.nets) if x.nets is not None else _prefixes_of(z, False)
            excluded = (_prefix_list(x.excluded) if x.excluded is not None
                        else _prefixes_of(z, True))  # fmt: skip
            if (nets, excluded) != (_prefixes_of(z, False), _prefixes_of(z, True)):
                others = [s for s in z.selectors if s.kind != "prefix"]
                z.selectors = others + [
                    LogicalZoneSelector(kind="prefix", value=v, exclude=ex)
                    for ex, values in ((False, nets), (True, excluded)) for v in values
                ]  # fmt: skip
                entry["nets"], entry["excluded"] = len(nets), len(excluded)
                owners.add(z.customer_id)
        if entry:
            z.updated_at = datetime.now(UTC)
            changes.append({"zone_id": z.id, **entry})
    session.flush()
    if changes:
        audit.record(session, actor=f"user:{principal.subject}", action="zone.appdoc_set",
                     subject=str(tid), detail={"tenant_id": tid, "changes": changes})  # fmt: skip
        if owners:  # Netze geaendert: Kunden auf den Zonen, Befunde der Zonen
            _rederive(session, tid, owners)
            reconcile.refresh_zone_findings(session, tid)
        app_refresh.schedule_tenant(background, factory, tid)  # Befunde nach den Kategorien
    return [{"id": z.id, "name": z.name, "appdoc_area": z.appdoc_area, "zone_type": z.zone_type,
             "nets": _prefixes_of(z, False), "excluded": _prefixes_of(z, True)}
            for z in items.values()]  # fmt: skip


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
    before = z.customer_id
    _apply(session, z, body, sel)
    session.flush()
    _audit(session, principal, "updated", z)
    _rederive(session, z.tenant_id, {before, z.customer_id})
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
    owner = z.customer_id
    session.delete(z)
    session.flush()
    _rederive(session, tid, {owner})
    reconcile.refresh_zone_findings(session, tid)


class NodesIn(BaseModel):
    nodes: list[str] = Field(min_length=1, max_length=500)  # Knoten `<Quellsystem>|<Kontext>`


@router.post("/{zone_id}/nodes")
def assign_nodes(
    zone_id: int,
    body: NodesIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Knoten (Firewall bzw. VDOM, ACI-VRF) der Zone zuordnen (Operator): als Selektor VDOM
    bzw. ACI-VRF - die Netze daran werden Netze der Zone bzw. Uebergaenge (0.56.0)."""
    z = _get(session, principal, zone_id)
    from app.store.paths import Graph

    g = Graph(session, z.tenant_id)
    have = {(s.kind, s.value.casefold(), s.source_system_id) for s in z.selectors}
    added = []
    for raw in dict.fromkeys(body.nodes):
        sid, _, ctx = raw.partition("|")
        key = (int(sid), ctx) if sid.isdigit() else None
        node = g.nodes.get(key) if key else None
        if node is None:
            raise HTTPException(status_code=404, detail=f"node not found: {raw}")
        sel = ("aci_vrf", ctx, None) if node.kind == "vrf" else ("fw_vdom", ctx, key[0])
        if (sel[0], sel[1].casefold(), sel[2]) in have:
            continue
        z.selectors = [*z.selectors, LogicalZoneSelector(kind=sel[0], value=sel[1],
                                                         source_system_id=sel[2])]  # fmt: skip
        added.append(node.label)
    if added:
        z.updated_at = datetime.now(UTC)
        session.flush()
        _audit(session, principal, "updated", z, via="node")
        _rederive(session, z.tenant_id, {z.customer_id})
        reconcile.refresh_zone_findings(session, z.tenant_id)
    return {"zone_id": z.id, "added": added}


@router.get("/{zone_id}/nets")
def zone_nets(
    zone_id: int,
    limit: int = Query(500, ge=1, le=5000),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Netze einer Zone mit Grund (Selektor bzw. EPG)."""
    z = _get(session, principal, zone_id)
    if principal.is_customer and z.customer_id not in (principal.customer_ids or ()):
        raise HTTPException(status_code=404, detail="zone not found")  # nur eigene Zonen
    reads.maybe_audit_read(session, principal, z.tenant_id, "zones")
    items = store.load(session, z.tenant_id)
    data = customer_store._Data(session, z.tenant_id)
    st = zone_struct.build(session, z.tenant_id, items, data)
    nets = next(n for n in store.derive(session, z.tenant_id, items, data, st) if n.id == z.id)
    ordered = sorted(nets.nets.items(), key=lambda x: store._net_key(x[0]))
    by_id = {x.id: x for x in items}
    customer = principal.is_customer
    view = store.customer_view(items, principal.customer_ids) if customer else None
    own_ids = {zone_struct.node_id(k) for k, zid in st.node_zone.items() if zid == z.id}
    transitions = []
    for t in st.of_zone(z.id):
        other = t.b if t.a == z.id else t.a
        networks = [
            {"network": n["network"],
             "legs": [leg for leg in n["legs"] if not customer or leg["node"] in own_ids],
             "routers": [] if customer else n["routers"]}
            for n in t.networks
        ]  # fmt: skip
        if view is not None and view.mask(other) == store.OTHER:  # Zone eines anderen Kunden
            masked = {"name": store.other_name(), "code": None, "kind": "customer", "position": 0}
            transitions.append({"to": masked["name"], "to_id": store.OTHER,
                                "name": store.transition_name(_side(z.id, by_id), masked),
                                "networks": networks[:limit]})  # fmt: skip
            continue
        transitions.append({"to": _side_name(other, by_id), "to_id": other,
                            "name": _transition_name(z, other, by_id),
                            "networks": networks[:limit]})  # fmt: skip
    return {
        "total": len(ordered),
        "items": [{"network": str(n), "via": why} for n, why in ordered[:limit]],
        "nodes": [st.graph.nodes[k].label for k in st.zone_nodes().get(z.id, [])],
        "transitions": transitions,
    }
