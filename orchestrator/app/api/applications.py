"""Applikationen (ADR 0012): Stammdaten, Mitglieder (AP/EPG/Server), Vorschlaege, Doku.

Lesen = `viewer`, Schreiben = `operator`, alles mandantsgescoped und auditiert. Die
publizierten Dienste (VIPs) liegen unter /published-services.

Kunden-Benutzer mit der Kunden-Rolle `editor` (feinere Rollen, ADR 0018 Ergaenzung) pflegen
Applikationen, die ganz zu ihren Kunden gehoeren (`customer_scope.Ownership`): Stammdaten,
Regeln zuordnen bzw. ausschliessen (und rueckgaengig) und Regeln je Kategorie ausblenden - nur
mit Regeln ihres Ausschnitts. Anlegen, Loeschen, Import, Vorschlaege und die Mitglieder AP, EPG
und Server bleiben den Betreuern.
"""

import re
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import audit
from app.api.appdoc import DocTableOut, SiteRef
from app.api.customer_filter import actor, customer_view, own_view, ownership
from app.api.customer_filter import ids as view_ids
from app.api.customer_filter import visible as in_view
from app.audit import reads
from app.auth import capabilities as caps
from app.auth.deps import require_operator, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session, get_session_factory
from app.i18n import L
from app.models import Application, Rule, Site, SourceSystem, Tenant
from app.schemas.rules import RuleOut
from app.store import app_import, app_paths, app_plan, app_refresh, peer_names, sites, word_export
from app.store import applications as store
from app.store import epgs as epg_store

router = APIRouter(prefix="/applications", tags=["applications"])

MemberKind = Literal["ap", "epg", "host", "rule", "norule"]  # rule/norule seit 0.60.0
Criticality = Literal["low", "medium", "high", "critical"]
Purpose = Literal["dev", "test", "prod"]


class MemberIn(BaseModel):
    kind: MemberKind
    ref: str = Field(min_length=1, max_length=400)


class ApplicationIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    external_name: str | None = Field(default=None, max_length=200)
    internal_name: str | None = Field(default=None, max_length=200)
    purpose: Purpose | None = None
    code: str | None = Field(default=None, max_length=50)
    owner: str | None = Field(default=None, max_length=200)
    criticality: Criticality | None = None
    site_id: int | None = None
    description: str | None = None
    infrastructure: bool = False  # Monitoring, Backup, DNS u. a. (0.88.0)
    members: list[MemberIn] = []


class ApplicationPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    external_name: str | None = Field(default=None, max_length=200)
    internal_name: str | None = Field(default=None, max_length=200)
    purpose: Purpose | None = None
    code: str | None = Field(default=None, max_length=50)
    owner: str | None = Field(default=None, max_length=200)
    criticality: Criticality | None = None
    site_id: int | None = None
    description: str | None = None
    infrastructure: bool | None = None


class MembersIn(BaseModel):
    members: list[MemberIn] = Field(min_length=1)


class MemberOut(BaseModel):
    id: int
    kind: str
    ref: str
    label: str
    origin: str
    exists: bool  # aktuell gelesen (sonst: nicht mehr gefunden)
    systems: list[str]  # Server bzw. EPGs (`Tenant/AP/EPG`), die das Mitglied umfasst


class ApplicationOut(BaseModel):
    id: int
    tenant_id: int
    name: str
    external_name: str | None = None
    internal_name: str | None = None
    purpose: str | None = None  # dev | test | prod
    code: str | None
    owner: str | None
    criticality: str | None
    site: SiteRef | None
    description: str | None
    infrastructure: bool = False  # 0.88.0
    member_count: int
    system_count: int
    vm_count: int = 0  # VMs an den Portgruppen ihrer EPGs bzw. hinter ihren Servern (0.75.0)
    # Feinere Rollen (ADR 0018): darf der Kunden-Benutzer sie pflegen (Kunden-Rolle editor und
    # ganz in seinem Ausschnitt)? Betreuer: None - fuer sie gilt die Rolle
    editable: bool | None = None


class ApplicationDetailOut(ApplicationOut):
    members: list[MemberOut]


class CandidateOut(BaseModel):
    kind: str
    ref: str
    label: str
    detail: str | None = None
    project: str | None = None
    applications: list[int] = []  # Applikationen, in denen der Kandidat schon steht


class SuggestionOut(BaseModel):
    key: str  # <ACI-Tenant>|<Projektnummer> bzw. die Nummer (0.74.1)
    code: str  # Projektnummer
    tenant: str | None = None  # ACI-Tenant der APs
    name: str
    application_id: int | None  # gesetzt: ergaenzt eine bestehende Applikation
    members: list[CandidateOut]


class AcceptIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)


class RuleCounts(BaseModel):
    specific: int = 0  # Regeln, die das System selbst bzw. die EPG nennen
    unspecific: int = 0  # nur ueber ein Subnetz, any, vzAny bzw. die Preferred Group


class DocSystemOut(BaseModel):
    """System der Applikation: eine VM (vCenter) bzw. ein Server ohne VM (0.14.0)."""

    key: str  # vm:<id> | host:<id> (Filter der Regeln)
    kind: str  # vm | host
    name: str
    ips: list[str] = []
    dns: list[str] = []  # DNS-Namen der IPs aus dem IPAM
    servers: list[str] = []  # Host-Objekte mit diesen IPs (Regel-Endpunkte, Befunde)
    # Firewall-Adressobjekte mit genau dieser IP je Name: name, name_ok (passt zum DNS-Namen),
    # where = [{id, source_system_id, vdom}] (auf welchen Firewalls bzw. VDOMs)
    objects: list[dict] = []
    epgs: list[str] = []  # EPGs mit ganzem ACI-Pfad Tenant/AP/EPG
    portgroups: list[str] = []
    # je NIC (0.43.1): name, portgroup, vlan, ips, epg (Tenant/AP/EPG), match (name | vlan)
    nics: list[dict] = []
    hints: list[str] = []  # z. B. VM-Name nennt ein anderes Projekt als das AP der Portgruppe
    vm_id: int | None = None
    power_state: str | None = None
    guest_os: str | None = None
    cluster: str | None = None
    host: str | None = None  # ESXi-Host
    site: SiteRef | None = None
    member: str  # warum das System dazugehoert (Mitglied, Portgruppe -> EPG, IP = Server)
    rules: RuleCounts = RuleCounts()


class DocEpgOut(BaseModel):
    key: str  # epg:<id>
    id: int
    label: str  # Tenant/AP/EPG
    dn: str | None = None
    tenant: str | None = None
    ap: str | None = None
    name: str
    bd: str | None = None
    vrf: str | None = None
    pref_group: bool = False
    subnets: list[str] = []
    vm_count: int = 0
    endpoint_count: int = 0
    member: str
    rules: RuleCounts = RuleCounts()


class DocHitOut(BaseModel):
    """Warum eine Regel fuer ein System der Applikation gilt (je Regel, System und Seite)."""

    rule_id: int
    system: str  # Schluessel aus systems/epgs
    table: int  # Kategorie 1-10
    direction: str  # in (System ist Ziel) | out (System ist Quelle)
    side: str = ""  # src | dst - Seite der Regel
    object: str = ""  # Objekt der Regel, das das System erfasst (H_web01, N_srv16, any, EPG …)
    value: str = ""  # sein Bereich (10.20.0.0/16; leer bei any/EPG)
    via: str  # Bezug: direkt, Objekt/Gruppe, Netz, any, vzAny <VRF>, ESG <Name>, EPG <Pfad>
    scope: str  # specific | subnet | any | vzany | pref_group
    counterpart: str = ""
    services: str = ""
    # ACI: EPG (Tenant/AP/EPG), ueber die der Contract fuer das System gilt (Filter, 0.42.3)
    epg: str | None = None
    # ACI (0.43.2): {"own": [DN], "counter": [DN]} - Beteiligte des Contracts fuer das System
    parties: dict | None = None
    # Routing (0.24.0, ADR 0019): possible | impossible | unknown - kann die Regel Verkehr zum
    # bzw. vom System ueberhaupt sehen; impossible mit Begruendung
    relevance: str = "unknown"
    relevance_text: str = ""
    member_id: int | None = None  # direkt zugeordnet (0.60.0): das Mitglied
    # ACI ist zustandslos (0.69.1): Treffer der Rueckrichtung (Provider -> Consumer, Filter
    # ohne Ports); `side` bleibt die Seite des Contracts, `direction` ist umgekehrt
    reverse: bool = False
    # L3Outs (ADR 0034): L3Out des Zugriffs; Contract zu externen EPGs mit Firewall dahinter =
    # Hilfs-Contract (`helper`, an den Firewall-Regeln `helper_for`); an der Firewall-Regel ihre
    # Hilfs-Contracts je L3Out (`helpers`: l3out, rule_ids, names, missing); Hinweis am Contract
    # ohne Firewall dahinter (`l3out_note`)
    l3out: str | None = None
    helper: bool = False
    helper_for: list[int] = []
    helpers: list[dict] = []
    l3out_note: str = ""


class DocSummaryOut(BaseModel):
    vms: int = 0
    servers: int = 0  # Server ohne VM
    epgs: int = 0
    rules: int = 0
    firewall: int = 0
    aci: int = 0
    specific: int = 0
    unspecific: int = 0
    by_scope: dict[str, int] = {}  # subnet | any | vzany | pref_group
    findings: int = 0
    impossible: int = 0  # Regeln nur mit routingtechnisch unmoeglichen Treffern
    assigned: int = 0  # direkt zugeordnete Regeln (0.60.0)
    excluded: int = 0  # ausgeschlossene Regeln (0.60.0)
    hidden: int = 0  # in den Kategorien ausgeblendete Zeilen (0.69.0)
    helpers: int = 0  # Hilfs-Contracts ueber L3Outs, an den Firewall-Regeln (ADR 0034)


class DocFindingOut(BaseModel):
    id: int
    type: str
    severity: str
    status: str
    subject_kind: str
    subject_id: str
    message: str
    hint: str | None = None
    rule_id: int | None = None
    epgs: list[str] = []  # EPGs (Tenant/AP/EPG) des Befunds - Filter nach Tenant, AP, EPG


class ApplicationDocOut(BaseModel):
    application: ApplicationOut
    tenant_code: str | None
    summary: DocSummaryOut
    systems: list[DocSystemOut]
    epgs: list[DocEpgOut]
    hits: list[DocHitOut]
    rules: list[RuleOut]
    findings: list[DocFindingOut]
    tables: list[DocTableOut]
    # 0.60.0: direkt zugeordnete bzw. ausgeschlossene Regeln, Abhaengigkeiten zu anderen
    rule_members: list[dict] = []
    dependencies: dict = {}
    # 0.69.0: ausgeblendete Regeln je Kategorie (`active` = steht in einer Kategorie)
    hides: list[dict] = []
    # 0.78.2: Contracts Richtung L3Out, die weniger oeffnen als die Firewall-Regeln
    narrower: list[dict] = []
    # ADR 0034: Hilfs-Contracts ueber L3Outs (nicht in `rules`) mit ihren Firewall-Regeln
    helpers: list[dict] = []
    # 0.101.0: Namen der Gegenstellen eingeschaltet (Einstellung des Mandanten, nur Betreuer)
    peer_names: bool = False


class RuleMemberIn(BaseModel):
    rule_id: int
    exclude: bool = False  # True: aus der Doku der Applikation ausschliessen


class HideIn(BaseModel):
    """Regel(n) einer Zeile in einer Kategorie ausblenden (0.69.0): IPv4/IPv6-Paar = zwei."""

    rule_ids: list[int] = Field(min_length=1, max_length=4)
    category: int | None = Field(default=None, ge=1, le=10)  # leer = alle Kategorien
    reason: str = Field(min_length=5, max_length=1000)


def _site(session: Session, site_id: int | None) -> SiteRef | None:
    site = session.get(Site, site_id) if site_id else None
    ref = sites.SiteIndex(session, site.tenant_id).ref(site) if site else None
    return SiteRef(**ref) if ref else None


def _out(session: Session, app: Application, resolver: store.Resolver,
         vms: store.VmCounter | None = None) -> ApplicationOut:  # fmt: skip
    return ApplicationOut(
        id=app.id,
        tenant_id=app.tenant_id,
        name=app.name,
        external_name=app.external_name,
        internal_name=app.internal_name,
        purpose=app.purpose,
        code=app.code,
        owner=app.owner,
        criticality=app.criticality,
        site=_site(session, app.site_id),
        description=app.description,
        infrastructure=bool(app.infrastructure),
        member_count=len(app.members),
        system_count=store.system_count(session, app, resolver),
        vm_count=(vms or store.VmCounter(session, app.tenant_id)).count(app, resolver),
    )


def _detail(session: Session, app: Application, editable: bool | None = None
            ) -> ApplicationDetailOut:  # fmt: skip
    res = store.Resolver(session, app.tenant_id)
    members = [
        MemberOut(
            id=m.id,
            kind=m.kind,
            ref=m.ref,
            label=r.label,
            origin=m.origin,
            exists=r.exists,
            systems=systems,
        )
        for m, r, systems in store.resolved_members(session, app, res)
    ]
    out = _out(session, app, res).model_dump() | {"editable": editable}
    return ApplicationDetailOut(**out, members=members)


def _require_tenant(principal: Principal, tenant_id: int | None) -> int:
    tid = tenant_scope(principal, tenant_id)
    if tid is None:
        raise HTTPException(
            status_code=422, detail=L("tenant_id erforderlich", "tenant_id required")
        )
    return tid


def _load(session: Session, principal: Principal, application_id: int) -> Application:
    app = store.get(session, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")
    tenant_scope(principal, app.tenant_id)  # fremder Mandant -> 403
    return app


def _refresh(background: BackgroundTasks, factory: sessionmaker, tenant_id: int,
             application_id: int) -> None:  # fmt: skip
    """Applikation geaendert: Abweichungen Regel <-> Weg neu (0.59.0) und - weil Befunde ueber
    ihre Regel Kunden gehoeren - die Findings der Kunden; seit 0.74.1 hinter der Antwort
    (`store/app_refresh.py`), das Speichern wartet nicht mehr darauf."""
    app_refresh.schedule(background, factory, tenant_id, application_id)


def _actor(principal: Principal) -> str:
    return f"user:{principal.subject}"


def _text(value: str | None) -> str | None:
    return (value or "").strip() or None


@router.get("", response_model=list[ApplicationOut])
def list_applications(
    tenant_id: int | None = None,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    tenant_id = tenant_scope(principal, tenant_id)
    reads.maybe_audit_read(session, principal, tenant_id, "applications")
    view = customer_view(session, principal, tenant_id, customer_id)
    mine = view.applications(session) if view else None
    owner = _owner(session, principal, tenant_id)
    resolvers: dict[int, store.Resolver] = {}
    counters: dict[int, store.VmCounter] = {}
    out = []
    for app in store.list_applications(session, tenant_id):
        if mine is not None and not mine(app):
            continue
        res = resolvers.setdefault(app.tenant_id, store.Resolver(session, app.tenant_id))
        vms = counters.setdefault(app.tenant_id, store.VmCounter(session, app.tenant_id))
        row = _out(session, app, res, vms)
        if principal.is_customer:  # feinere Rollen: darf er sie pflegen?
            row.editable = owner is not None and owner.application(app, res)
        out.append(row)
    return out


@router.post("", response_model=ApplicationDetailOut, status_code=201)
def create_application(
    background: BackgroundTasks,
    body: ApplicationIn,
    tenant_id: int | None = None,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
    factory: sessionmaker = Depends(get_session_factory),
):
    tid = _require_tenant(principal, tenant_id)
    name = body.name.strip()
    if store.by_name(session, tid, name) is not None:
        raise HTTPException(status_code=409, detail="application already exists")
    members = [(m.kind, m.ref) for m in body.members]
    try:
        store.check_site(session, tid, body.site_id)
        store.validate_members(session, tid, members)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    app = Application(
        tenant_id=tid,
        name=name,
        external_name=_text(body.external_name),
        internal_name=_text(body.internal_name),
        purpose=body.purpose,
        code=(body.code or "").strip() or None,
        owner=body.owner,
        criticality=body.criticality,
        site_id=body.site_id,
        description=body.description,
        infrastructure=body.infrastructure,
    )
    session.add(app)
    session.flush()
    store.add_members(session, app, members)
    audit.record(
        session,
        actor=_actor(principal),
        action="application.created",
        subject=str(app.id),
        detail={
            "application_id": app.id,
            "tenant_id": tid,
            "name": app.name,
            "code": app.code,
            "members": len(app.members),
        },
    )
    _refresh(background, factory, app.tenant_id, app.id)
    return _detail(session, app)


class ImportIn(BaseModel):
    csv: str = Field(min_length=1, max_length=app_import.MAX_BYTES)


class DeleteManyIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=2000)


@router.get("/import/template")
def import_template(principal: Principal = Depends(require_operator)):
    """Vorlage fuer den CSV-Import (0.75.0): Kopfzeile und ein Beispiel, Trennzeichen `;`."""
    body = "\ufeff" + app_import.template()  # BOM: Excel erkennt UTF-8
    name = L("applikationen-vorlage.csv", "applications-template.csv")
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/import")
def import_applications(
    body: ImportIn,
    tenant_id: int | None = None,
    apply: bool = False,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
):
    """Stammdaten aus CSV (0.75.0, Ausnahme vom „kein CSV-Import“ nur dafuer): ohne `apply`
    die Vorschau je Zeile, mit `apply=true` werden die Zeilen ohne Fehler uebernommen. Die
    Mitglieder folgen in der Oberflaeche."""
    tid = _require_tenant(principal, tenant_id)
    try:
        if not apply:
            return app_import.plan(session, tid, body.csv).out()
        result, touched = app_import.apply(session, tid, body.csv)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    counts = result.counts()
    audit.record(
        session,
        actor=_actor(principal),
        action="application.imported",
        subject=str(tid),
        detail={
            "tenant_id": tid,
            "created": counts["create"],
            "updated": counts["update"],
            "errors": counts["error"],
            "application_ids": touched[:500],
        },
    )
    return result.out()


@router.post("/delete")
def delete_applications(
    body: DeleteManyIn,
    background: BackgroundTasks,
    tenant_id: int | None = None,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
    factory: sessionmaker = Depends(get_session_factory),
):
    """Mehrere Applikationen loeschen (0.75.0, z. B. ohne VMs und ohne Funktion). Gelesene Daten
    bleiben; ihre Befunde fallen im Hintergrund weg."""
    tid = _require_tenant(principal, tenant_id)
    apps = list(
        session.scalars(
            select(Application).where(
                Application.tenant_id == tid, Application.id.in_(set(body.ids))
            )
        )
    )
    for app in apps:
        audit.record(
            session,
            actor=_actor(principal),
            action="application.deleted",
            subject=str(app.id),
            detail={
                "application_id": app.id,
                "tenant_id": tid,
                "name": app.name,
                "members": len(app.members),
            },
        )
        session.delete(app)
    session.flush()
    for app in apps:
        _refresh(background, factory, tid, app.id)
    return {"deleted": len(apps)}


@router.get("/candidates", response_model=list[CandidateOut])
def list_candidates(
    response: Response,
    tenant_id: int | None = None,
    kind: MemberKind | None = None,
    q: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    """Moegliche Mitglieder (APs, EPGs, Server) mit Projektnummer und bestehender Zuordnung."""
    tid = _require_tenant(principal, tenant_id)
    items = store.candidates(session, tid)
    if kind:
        items = [c for c in items if c.kind == kind]
    if q:
        needle = q.lower()
        items = [
            c
            for c in items
            if needle in c.label.lower()
            or needle in c.ref.lower()
            or needle in (c.detail or "").lower()
        ]
    response.headers["X-Total-Count"] = str(len(items))
    return [CandidateOut(**c.__dict__) for c in items[:limit]]


@router.get("/suggestions", response_model=list[SuggestionOut])
def list_suggestions(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    tid = _require_tenant(principal, tenant_id)
    return [
        SuggestionOut(
            key=s["key"],
            code=s["code"],
            tenant=s["tenant"],
            name=s["name"],
            application_id=s["application_id"],
            members=[CandidateOut(**c.__dict__) for c in s["members"]],
        )
        for s in store.suggestions(session, tid)
    ]


@router.post("/suggestions/{code}/accept", response_model=ApplicationDetailOut)
def accept_suggestion(
    background: BackgroundTasks,
    code: str,
    body: AcceptIn | None = None,
    tenant_id: int | None = None,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
    factory: sessionmaker = Depends(get_session_factory),
):
    """Vorschlag uebernehmen (neue Applikation bzw. Mitglieder der bestehenden ergaenzen)."""
    tid = _require_tenant(principal, tenant_id)
    try:
        result = store.accept_suggestion(session, tid, code, body.name if body else None)
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="no suggestion for this project number")
    app, added, created = result
    detail = {"application_id": app.id, "tenant_id": tid, "suggestion": code}
    if created:
        detail |= {"name": app.name, "code": app.code, "members": added}
    else:
        detail |= {"count": added}
    audit.record(
        session,
        actor=_actor(principal),
        action="application.created" if created else "application.member_added",
        subject=str(app.id),
        detail=detail,
    )
    _refresh(background, factory, app.tenant_id, app.id)
    return _detail(session, app)


def _visible(session: Session, principal: Principal, app: Application):
    """Kunden-Benutzer: nur Applikationen in ihrem Ausschnitt (sonst 404, ADR 0018)."""
    view = own_view(session, principal, app.tenant_id)
    if view is not None and not view.applications(session)(app):
        raise HTTPException(status_code=404, detail="application not found")
    return view


def _owner(session: Session, principal: Principal, tenant_id: int | None):
    """Kunden-Benutzer (feinere Rollen): was ganz seinen Kunden mit `applications.edit` gehoert
    (None = keiner); Betreuer: None."""
    if not principal.is_customer or tenant_id is None:
        return None
    return ownership(session, principal, tenant_id, caps.APPS_EDIT)


def _editable(session: Session, principal: Principal, app: Application) -> bool | None:
    if not principal.is_customer:
        return None
    owner = _owner(session, principal, app.tenant_id)
    return owner is not None and owner.application(app, store.Resolver(session, app.tenant_id))


def _maintain(session: Session, principal: Principal, app: Application):
    """Pflege durch einen Kunden-Benutzer: Applikation in seinem Ausschnitt (sonst 404) und ganz
    bei seinen Kunden mit `applications.edit` (sonst 403). Betreuer: None (die Rolle genuegt)."""
    if not principal.is_customer:
        return None
    _visible(session, principal, app)
    owner = _owner(session, principal, app.tenant_id)
    if owner is None or not owner.application(app, store.Resolver(session, app.tenant_id)):
        raise HTTPException(
            status_code=403,
            detail=L(
                "Applikation liegt nicht ganz beim Kunden – Pflege durch den Betreuer",
                "application not entirely within the customer – maintained by staff",
            ),
        )
    return owner


def _own_rule(owner, rule_id: int | None) -> None:
    """Kunden-Benutzer: nur Regeln seines Ausschnitts (sonst nicht auffindbar)."""
    if owner is not None and rule_id not in owner.rules:
        raise HTTPException(status_code=404, detail="rule not found")


@router.get("/{application_id}", response_model=ApplicationDetailOut)
def get_application(
    application_id: int,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    app = _load(session, principal, application_id)
    _visible(session, principal, app)
    reads.maybe_audit_read(session, principal, app.tenant_id, "applications")
    return _detail(session, app, _editable(session, principal, app))


@router.patch("/{application_id}", response_model=ApplicationDetailOut)
def update_application(
    background: BackgroundTasks,
    application_id: int,
    body: ApplicationPatch,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
    factory: sessionmaker = Depends(get_session_factory),
):
    app = _load(session, principal, application_id)
    owner = _maintain(session, principal, app)  # Kunden-Benutzer: nur eigene (editor)
    fields = body.model_fields_set
    if "name" in fields and body.name is not None:
        name = body.name.strip()
        other = store.by_name(session, app.tenant_id, name)
        if other is not None and other.id != app.id:
            raise HTTPException(status_code=409, detail="application already exists")
        app.name = name
    if "site_id" in fields:
        try:
            store.check_site(session, app.tenant_id, body.site_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        app.site_id = body.site_id
    if "code" in fields:
        app.code = (body.code or "").strip() or None
    for attr in ("external_name", "internal_name"):
        if attr in fields:
            setattr(app, attr, _text(getattr(body, attr)))
    for attr in ("owner", "criticality", "purpose", "description"):
        if attr in fields:
            setattr(app, attr, getattr(body, attr))
    if "infrastructure" in fields and body.infrastructure is not None:
        app.infrastructure = body.infrastructure
    store.touch(app)
    session.flush()
    audit.record(
        session,
        actor=_actor(principal),
        action="application.updated",
        subject=str(app.id),
        detail={
            "application_id": app.id,
            "tenant_id": app.tenant_id,
            "name": app.name,
            "code": app.code,
            "criticality": app.criticality,
            "purpose": app.purpose,
            "site_id": app.site_id,
            "infrastructure": app.infrastructure,
            "fields": sorted(fields),
            **actor(principal),
        },
    )
    _refresh(background, factory, app.tenant_id, app.id)
    return _detail(session, app, True if owner is not None else None)


@router.delete("/{application_id}", status_code=204)
def delete_application(
    background: BackgroundTasks,
    application_id: int,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
    factory: sessionmaker = Depends(get_session_factory),
):
    app = _load(session, principal, application_id)
    detail = {
        "application_id": app.id,
        "tenant_id": app.tenant_id,
        "name": app.name,
        "members": len(app.members),
    }
    tid = app.tenant_id
    session.delete(app)
    session.flush()
    _refresh(background, factory, tid, application_id)  # ihre Befunde fallen weg
    audit.record(
        session,
        actor=_actor(principal),
        action="application.deleted",
        subject=str(application_id),
        detail=detail,
    )
    return Response(status_code=204)


@router.post("/{application_id}/members", response_model=ApplicationDetailOut)
def add_members(
    background: BackgroundTasks,
    application_id: int,
    body: MembersIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
    factory: sessionmaker = Depends(get_session_factory),
):
    app = _load(session, principal, application_id)
    try:
        added = store.add_members(session, app, [(m.kind, m.ref) for m in body.members])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if added:
        first = body.members[0]
        audit.record(
            session,
            actor=_actor(principal),
            action="application.member_added",
            subject=str(app.id),
            detail={
                "application_id": app.id,
                "tenant_id": app.tenant_id,
                "kind": first.kind,
                "ref": first.ref,
                "count": added,
            },
        )
    _refresh(background, factory, app.tenant_id, app.id)
    return _detail(session, app)


@router.delete("/{application_id}/members/{member_id}", response_model=ApplicationDetailOut)
def remove_member(
    background: BackgroundTasks,
    application_id: int,
    member_id: int,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
    factory: sessionmaker = Depends(get_session_factory),
):
    app = _load(session, principal, application_id)
    owner = _maintain(session, principal, app)  # Kunden-Benutzer: nur eigene (editor)
    member = next((m for m in app.members if m.id == member_id), None)
    if member is None:
        raise HTTPException(status_code=404, detail="member not found")
    if owner is not None:  # Kunden-Benutzer: nur Regeln (zuordnen/ausschliessen rueckgaengig)
        if member.kind not in store.RULE_KINDS:
            raise HTTPException(
                status_code=403,
                detail=L(
                    "AP, EPG und Server pflegt der Betreuer",
                    "AP, EPG and servers are maintained by staff",
                ),
            )
        rule = store.Resolver(session, app.tenant_id).rule(member.ref)
        _own_rule(owner, rule.id if rule else None)
    app.members.remove(member)
    store.touch(app)
    session.flush()
    audit.record(
        session,
        actor=_actor(principal),
        action="application.member_removed",
        subject=str(app.id),
        detail={
            "application_id": app.id,
            "tenant_id": app.tenant_id,
            "kind": member.kind,
            "ref": member.ref,
            **actor(principal),
        },
    )
    _refresh(background, factory, app.tenant_id, app.id)
    return _detail(session, app, True if owner is not None else None)


@router.get("/{application_id}/connections")
def application_connections(
    application_id: int,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Verbindungen Ende zu Ende (0.45.0): je Verbindung der Weg ueber Firewalls und ACI mit
    der entscheidenden Policy bzw. dem Contract je Hop und dem Abgleich mit den Regeln der
    Doku. Nicht fuer Kunden-Benutzer (Freigabeliste): der Weg zeigt die Infrastruktur."""
    app = _load(session, principal, application_id)
    reads.maybe_audit_read(session, principal, app.tenant_id, "applications")
    out = app_paths.build(session, app)
    # Netzwerkplan (0.67.0), kompakt nach AP/EPG (0.74.0)
    out["plan"] = app_plan.build(out["connections"], members=out["members"])
    return out


_EXPORT = {"svg": ("image/svg+xml", "svg"), "drawio": ("application/xml", "drawio")}


@router.get("/{application_id}/connections/plan/export")
def export_connections_plan(
    application_id: int,
    format: str = Query("svg", pattern="^(svg|drawio)$"),  # noqa: A002 - Name der Abfrage
    key: Annotated[list[str] | None, Query(description="nur diese Verbindungen")] = None,
    rules: bool = True,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> Response:
    """Netzwerkplan der Applikation (0.67.0) als SVG bzw. draw.io - wie in der Ansicht; mit
    `key` nur die Verbindungen des Filters, mit `rules=false` ohne die Ebene „Regeln“ (0.74.0).
    Nicht fuer Kunden-Benutzer (Freigabeliste)."""
    if key and len(key) > app_paths.MAX_TRACES:
        raise HTTPException(
            status_code=422, detail=L("zu viele Verbindungen", "too many connections")
        )
    app = _load(session, principal, application_id)
    reads.maybe_audit_read(session, principal, app.tenant_id, "applications")
    built = app_paths.build(session, app)
    plan = app_plan.build(built["connections"], set(key) if key else None, built["members"])
    when = datetime.now(UTC)
    title = f"{L('Netzwerkplan', 'Network diagram')} {app.name}"
    body = (app_plan.svg if format == "svg" else app_plan.drawio)(plan, title, when, rules)
    media, ext = _EXPORT[format]
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", app.name).strip("-").lower() or "applikation"
    name = f"{L('netzwerkplan', 'network-diagram')}-{slug}-{when:%Y-%m-%d}.{ext}"
    return Response(content=body, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})  # fmt: skip


@router.get("/{application_id}/export")
def export_application(
    application_id: int,
    format: str = Query("zip", pattern="^(docx|zip)$"),  # noqa: A002 - Name der Abfrage
    sections: str | None = Query(None, max_length=200),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> Response:
    """Export fuer Confluence (0.71.0, ADR 0026): die Applikations-Doku als Word (`docx`) bzw.
    als ZIP mit Word und dem Netzwerkplan als draw.io (`zip`) - zum Verbessern in Word/draw.io
    und Import von Hand. Seit 0.73.0 `sections` (Komma-Liste aus `word_export.SECTIONS`, leer =
    alle) wie im Export-Dialog; den Plan legt das ZIP nur mit `plan` bei. Nicht fuer
    Kunden-Benutzer (Verbindungen zeigen die Infrastruktur)."""
    want = None
    if sections:
        want = {s.strip() for s in sections.split(",") if s.strip()}
        if not want or not want <= set(word_export.SECTIONS):
            raise HTTPException(
                status_code=422, detail=L("unbekannter Abschnitt", "unknown section")
            )
    with_plan = want is None or "plan" in want
    app = _load(session, principal, application_id)
    reads.maybe_audit_read(session, principal, app.tenant_id, "applications")
    names = peer_names.enabled(session, app.tenant_id, principal.is_customer)  # 0.95.0
    doc = store.build_doc(session, app, None, names=names)
    if want is None or "dependencies" in want:  # 0.88.0
        doc["dependencies"] = store.dependencies(session, app, doc)
    out = ApplicationDocOut(
        application=_out(session, app, store.Resolver(session, app.tenant_id)), **doc
    ).model_dump(mode="json")
    needs_paths = want is None or bool(want & {"connections", "plan"})
    conns = app_paths.build(session, app) if needs_paths else None
    when = datetime.now(UTC)
    tenant = session.get(Tenant, app.tenant_id)
    sources = dict(session.execute(
        select(SourceSystem.id, SourceSystem.name).where(SourceSystem.tenant_id == app.tenant_id)
    ).all())  # fmt: skip
    base = (f"{L('applikation', 'application')}-{word_export.slug(app.name, 'applikation')}"
            f"-{when:%Y-%m-%d}")  # fmt: skip
    plan_file = f"{base}-{L('netzwerkplan', 'network-diagram')}.drawio"
    body = word_export.application(out, conns, sources, when, tenant.name if tenant else None,
                                   plan_file if format == "zip" and with_plan else None,
                                   want)  # fmt: skip
    if format == "docx":
        media, name = _DOCX, f"{base}.docx"
    else:
        files = [(f"{base}.docx", body)]
        if with_plan and conns is not None:
            plan = app_plan.drawio(app_plan.build(conns["connections"],
                                                  members=conns["members"]),
                                   f"{L('Netzwerkplan', 'Network diagram')} {app.name}",
                                   when)  # fmt: skip
            files.append((plan_file, plan))
        body = word_export.bundle(files)
        media, name = "application/zip", f"{base}.zip"
    return Response(content=body, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})  # fmt: skip


_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.get("/{application_id}/doc", response_model=ApplicationDocOut)
def application_doc(
    application_id: int,
    dependencies: bool = False,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    """Applikations-Doku ueber alle Systeme der Applikation (Tabellen 1–10). Abhaengigkeiten zu
    anderen Applikationen (0.60.0) seit 0.73.0 nur mit `dependencies=true`; die Oberflaeche holt
    sie seit 0.88.0 einzeln ueber `GET …/dependencies`. Gegenstellen nur mit Adresse nennen
    seit 0.95.0 ihren Namen, wenn der Mandant es vorsieht - nie fuer Kunden-Benutzer."""
    app = _load(session, principal, application_id)
    view = _visible(session, principal, app)
    reads.maybe_audit_read(session, principal, app.tenant_id, "applications")
    editable = _editable(session, principal, app)
    names = peer_names.enabled(session, app.tenant_id, principal.is_customer)
    doc = store.build_doc(session, app, view_ids(session, view, "rule"), names=names)
    doc["peer_names"] = names
    if dependencies:
        visible = _visible_apps(session, principal, app)
        doc["dependencies"] = store.dependencies(session, app, doc, visible)
    out = _out(session, app, store.Resolver(session, app.tenant_id))
    out.editable = editable
    return ApplicationDocOut(application=out, **doc)


def _visible_apps(session: Session, principal: Principal, app: Application) -> set[int] | None:
    """Kunden-Benutzer: nur Applikationen in ihrem Ausschnitt (sonst None = alle)."""
    view = _visible(session, principal, app)
    if view is None:
        return None
    mine = view.applications(session)
    stmt = select(Application).where(Application.tenant_id == app.tenant_id)
    return {a.id for a in session.scalars(stmt) if mine(a)}


@router.get("/{application_id}/epgs/{epg_id}")
def application_epg(
    application_id: int,
    epg_id: int,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """EPG der Applikations-Doku im Detail (0.95.0, Anwender: „Detailseite statt zu den Regeln
    springen“): die Lage wie im Regel-Detail (`epgs.details` - Tenant, AP, BD, VRF, Subnetze mit
    IPAM, Endpunkte, VMs, Leaf-Ports und VLANs). Nur EPGs der Applikation; Kunden-Benutzer nur
    in ihrem Ausschnitt (sonst 404), Geraetenamen neutral wie ueberall (`customer_alias`)."""
    app = _load(session, principal, application_id)
    view = _visible(session, principal, app)
    reads.maybe_audit_read(session, principal, app.tenant_id, "applications")
    _hosts, epgs = store.app_systems(session, app)
    epg = next((e for e in epgs if e.id == epg_id), None)
    if epg is None:
        raise HTTPException(status_code=404, detail="epg not found")
    if view is not None:
        in_view(session, view, "epg", epg.id)
    return epg_store.details(session, app.tenant_id, [epg])[epg.id]


@router.get("/{application_id}/dependencies")
def application_dependencies(
    application_id: int,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Abhaengigkeiten zu anderen Applikationen (0.60.0, seit 0.88.0 wieder in der Doku): die
    Oberflaeche holt sie erst, wenn der Bereich aufgeklappt wird. Je Eintrag `infrastructure`
    (Monitoring, Backup, DNS u. a.) - getrennt von den fachlichen Abhaengigkeiten."""
    app = _load(session, principal, application_id)
    view = _visible(session, principal, app)
    reads.maybe_audit_read(session, principal, app.tenant_id, "applications")
    doc = store.build_doc(session, app, view_ids(session, view, "rule"))
    deps = store.dependencies(session, app, doc, _visible_apps(session, principal, app))
    return {**deps, "infrastructure": bool(app.infrastructure)}


@router.post("/{application_id}/hidden", status_code=201)
def hide_rules(
    application_id: int,
    body: HideIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """Regel in einer Kategorie (bzw. in allen) der Doku ausblenden - mit Begruendung, im Audit
    (0.69.0). Die Regel bleibt sonst in der Doku; der Verweis haelt ueber neue Laeufe."""
    app = _load(session, principal, application_id)
    owner = _maintain(session, principal, app)  # Kunden-Benutzer: nur eigene (editor)
    reason = body.reason.strip()
    if len(reason) < 5:
        raise HTTPException(status_code=422, detail=L("Begründung zu kurz", "reason too short"))
    rules = []
    for rule_id in dict.fromkeys(body.rule_ids):
        rule = session.get(Rule, rule_id)
        if rule is None or rule.tenant_id != app.tenant_id:
            raise HTTPException(status_code=404, detail="rule not found")
        _own_rule(owner, rule.id)
        rules.append(rule)
    out = []
    for rule in rules:
        hide = store.hide_rule(session, app, rule, body.category, reason, _actor(principal))
        audit.record(
            session,
            actor=_actor(principal),
            action="application.rule_hidden",
            subject=str(app.id),
            detail={
                "application_id": app.id,
                "tenant_id": app.tenant_id,
                "rule_ref": hide.rule_ref,
                "category": hide.category,
                "reason": reason[:500],
                **actor(principal),
            },  # fmt: skip
        )
        out.append(store.hide_out(hide))
    return out


@router.delete("/{application_id}/hidden/{hide_id}", status_code=204)
def show_rule(
    application_id: int,
    hide_id: int,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> Response:
    """Ausgeblendete Regel wieder einblenden (0.69.0), im Audit mit der alten Begruendung."""
    app = _load(session, principal, application_id)
    owner = _maintain(session, principal, app)  # Kunden-Benutzer: nur eigene (editor)
    hide = next((h for h in app.hides if h.id == hide_id), None)
    if hide is None:
        raise HTTPException(status_code=404, detail="hide not found")
    if owner is not None:  # Kunden-Benutzer: nur Regeln seines Ausschnitts
        rule = store.Resolver(session, app.tenant_id).rule(hide.rule_ref)
        if rule is None or rule.id not in owner.rules:
            raise HTTPException(status_code=404, detail="hide not found")
    app.hides.remove(hide)
    session.flush()
    audit.record(
        session,
        actor=_actor(principal),
        action="application.rule_shown",
        subject=str(app.id),
        detail={
            "application_id": app.id,
            "tenant_id": app.tenant_id,
            "rule_ref": hide.rule_ref,
            "category": hide.category,
            "reason": hide.reason[:500],
            **actor(principal),
        },  # fmt: skip
    )
    return Response(status_code=204)


@router.post("/{application_id}/rules", response_model=ApplicationDetailOut)
def assign_rule(
    background: BackgroundTasks,
    application_id: int,
    body: RuleMemberIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
    factory: sessionmaker = Depends(get_session_factory),
):
    """Regel direkt zuordnen bzw. ausschliessen (0.60.0): Mitglied `rule` bzw. `norule` mit
    stabilem Verweis (Quellsystem, VDOM, Policy-ID bzw. DN) - die Gegenrichtung faellt weg."""
    app = _load(session, principal, application_id)
    owner = _maintain(session, principal, app)  # Kunden-Benutzer: nur eigene (editor)
    rule = session.get(Rule, body.rule_id)
    if rule is None or rule.tenant_id != app.tenant_id:
        raise HTTPException(status_code=404, detail="rule not found")
    _own_rule(owner, rule.id)
    ref = store.rule_ref(rule)
    kind = "norule" if body.exclude else "rule"
    for m in [m for m in app.members if m.ref == ref and m.kind in store.RULE_KINDS]:
        if m.kind != kind:
            app.members.remove(m)
    store.add_members(session, app, [(kind, ref)])
    audit.record(
        session,
        actor=_actor(principal),
        action="application.member_added",
        subject=str(app.id),
        detail={
            "application_id": app.id,
            "tenant_id": app.tenant_id,
            "kind": kind,
            "ref": ref,
            **actor(principal),
        },  # fmt: skip
    )
    _refresh(background, factory, app.tenant_id, app.id)
    return _detail(session, app, True if owner is not None else None)
