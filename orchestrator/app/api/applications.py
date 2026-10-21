"""Applikationen (ADR 0012): Stammdaten, Mitglieder (AP/EPG/Server), Vorschlaege, Doku.

Lesen = `viewer`, Schreiben = `operator`, alles mandantsgescoped und auditiert. Die
publizierten Dienste (VIPs) liegen unter /published-services.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import audit
from app.api.appdoc import DocTableOut, SiteRef
from app.audit import reads
from app.auth.deps import require_operator, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import Application, Site
from app.schemas.rules import RuleOut
from app.store import applications as store
from app.store import sites

router = APIRouter(prefix="/applications", tags=["applications"])

MemberKind = Literal["ap", "epg", "host"]
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
    member_count: int
    system_count: int


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
    code: str  # Projektnummer
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
    # where = [{id, source_system_id}] (auf welchen Firewalls)
    objects: list[dict] = []
    epgs: list[str] = []  # EPGs mit ganzem ACI-Pfad Tenant/AP/EPG
    portgroups: list[str] = []
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


def _site(session: Session, site_id: int | None) -> SiteRef | None:
    site = session.get(Site, site_id) if site_id else None
    ref = sites.SiteIndex(session, site.tenant_id).ref(site) if site else None
    return SiteRef(**ref) if ref else None


def _out(session: Session, app: Application, resolver: store.Resolver) -> ApplicationOut:
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
        member_count=len(app.members),
        system_count=store.system_count(session, app, resolver),
    )


def _detail(session: Session, app: Application) -> ApplicationDetailOut:
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
    return ApplicationDetailOut(**_out(session, app, res).model_dump(), members=members)


def _require_tenant(principal: Principal, tenant_id: int | None) -> int:
    tid = tenant_scope(principal, tenant_id)
    if tid is None:
        raise HTTPException(status_code=422, detail="tenant_id erforderlich")
    return tid


def _load(session: Session, principal: Principal, application_id: int) -> Application:
    app = store.get(session, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")
    tenant_scope(principal, app.tenant_id)  # fremder Mandant -> 403
    return app


def _actor(principal: Principal) -> str:
    return f"user:{principal.subject}"


def _text(value: str | None) -> str | None:
    return (value or "").strip() or None


@router.get("", response_model=list[ApplicationOut])
def list_applications(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    tenant_id = tenant_scope(principal, tenant_id)
    reads.maybe_audit_read(session, principal, tenant_id, "applications")
    resolvers: dict[int, store.Resolver] = {}
    out = []
    for app in store.list_applications(session, tenant_id):
        res = resolvers.setdefault(app.tenant_id, store.Resolver(session, app.tenant_id))
        out.append(_out(session, app, res))
    return out


@router.post("", response_model=ApplicationDetailOut, status_code=201)
def create_application(
    body: ApplicationIn,
    tenant_id: int | None = None,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
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
    return _detail(session, app)


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
            code=s["code"],
            name=s["name"],
            application_id=s["application_id"],
            members=[CandidateOut(**c.__dict__) for c in s["members"]],
        )
        for s in store.suggestions(session, tid)
    ]


@router.post("/suggestions/{code}/accept", response_model=ApplicationDetailOut)
def accept_suggestion(
    code: str,
    body: AcceptIn | None = None,
    tenant_id: int | None = None,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
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
    return _detail(session, app)


@router.get("/{application_id}", response_model=ApplicationDetailOut)
def get_application(
    application_id: int,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    app = _load(session, principal, application_id)
    reads.maybe_audit_read(session, principal, app.tenant_id, "applications")
    return _detail(session, app)


@router.patch("/{application_id}", response_model=ApplicationDetailOut)
def update_application(
    application_id: int,
    body: ApplicationPatch,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
):
    app = _load(session, principal, application_id)
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
            "fields": sorted(fields),
        },
    )
    return _detail(session, app)


@router.delete("/{application_id}", status_code=204)
def delete_application(
    application_id: int,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
):
    app = _load(session, principal, application_id)
    detail = {
        "application_id": app.id,
        "tenant_id": app.tenant_id,
        "name": app.name,
        "members": len(app.members),
    }
    session.delete(app)
    session.flush()
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
    application_id: int,
    body: MembersIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
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
    return _detail(session, app)


@router.delete("/{application_id}/members/{member_id}", response_model=ApplicationDetailOut)
def remove_member(
    application_id: int,
    member_id: int,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
):
    app = _load(session, principal, application_id)
    member = next((m for m in app.members if m.id == member_id), None)
    if member is None:
        raise HTTPException(status_code=404, detail="member not found")
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
        },
    )
    return _detail(session, app)


@router.get("/{application_id}/doc", response_model=ApplicationDocOut)
def application_doc(
    application_id: int,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    """Applikations-Doku ueber alle Systeme der Applikation (Tabellen 1–10)."""
    app = _load(session, principal, application_id)
    reads.maybe_audit_read(session, principal, app.tenant_id, "applications")
    doc = store.build_doc(session, app)
    return ApplicationDocOut(
        application=_out(session, app, store.Resolver(session, app.tenant_id)), **doc
    )
