"""Applikations-Doku (UC1, systemzentriert): die 10 Kategorien je System.

Liefert pro System (Host) die nach Richtung/Scope/Enforcement gruppierten Tabellen
(`docs/usecases/uc1-applikations-doku-spec.md`). Lesen = `viewer`, mandantsgescoped.
Seit 0.104.0 der Export als Markdown bzw. Word vom Server (`word_export.system`, derselbe
Aufbau wie die Applikations-Doku) - bisher baute die Oberflaeche das Markdown selbst.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import audit
from app.api.customer_filter import customer_view, own_view
from app.api.customer_filter import ids as view_ids
from app.audit import reads
from app.auth.deps import require_operator, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.i18n import L
from app.models import Tenant
from app.store import appdoc as store
from app.store import appdoc_zones, customer_alias, peer_names, word_export

router = APIRouter(prefix="/appdoc", tags=["appdoc"])


class SystemRefOut(BaseModel):
    name: str
    ip: str | None
    rule_count: int
    kind: str


class DocTableOut(BaseModel):
    index: int
    title: str
    enforcement: str
    columns: list[str]  # Schluessel der Zeilen (deutsch, bleiben fuer Vergleiche)
    # Anzeige der Spalten parallel zu `columns` in der Sprache der Anfrage (0.87.0, ADR 0028)
    labels: list[str] = []
    # Spalten als Text; dazu `_rule` (Regel-ID), `_dir` (in | out) und `_scope` (Art des Bezugs)
    rows: list[dict[str, str | int | list[int] | list[str] | None]]
    # 0.69.0 (Applikations-Doku): ausgeblendete Zeilen mit `_hides` (Begruendung, wer, wann)
    hidden: list[dict] = []
    # 0.101.0: Zonen der Kategorie mit ihren Netzen (Kopf; nicht fuer Kunden-Benutzer)
    zones: list[dict] = []


class SiteRef(BaseModel):
    id: int
    code: str
    name: str
    kind: str = "virtual"  # virtual | physical (ADR 0015)
    physical: list[str] = []  # virtuell: Codes der physischen Standorte
    virtual: list[str] = []  # physisch: Codes der virtuellen Standorte


class DocVmOut(BaseModel):
    """VM hinter dem System (vCenter, ADR 0017): beim Server die VM der IP, bei der EPG die VMs
    an ihren Portgruppen."""

    name: str
    power_state: str | None = None
    portgroup: str | None = None
    ips: list[str] = []
    epg: str | None = None


class ImpossibleOut(BaseModel):
    """Regel, die das System nach dem Routing ihrer Firewall nicht sehen kann (0.24.0)."""

    rule_id: int | None
    description: str = ""
    side: str | None = None  # src | dst
    text: str = ""


class SystemDocOut(BaseModel):
    system: str
    system_ip: str | None
    tenant_code: str | None
    site: SiteRef | None
    vms: list[DocVmOut] = []
    tables: list[DocTableOut]
    impossible: list[ImpossibleOut] = []  # nicht in den Tabellen (Routing)


class SiteAssign(BaseModel):
    site_id: int | None = None


@router.get("/systems", response_model=list[SystemRefOut])
def list_systems(
    tenant_id: int | None = None,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    tenant_id = tenant_scope(principal, tenant_id)
    reads.maybe_audit_read(session, principal, tenant_id, "appdoc")
    view = customer_view(session, principal, tenant_id, customer_id)
    own = view_ids(session, view, "rule") if principal.is_customer else None
    return [
        SystemRefOut(name=s.name, ip=s.ip, rule_count=s.rule_count, kind=s.kind)
        for s in store.list_systems(session, tenant_id, view, own)
    ]


@router.get("/systems/{system}", response_model=SystemDocOut)
def system_doc(
    system: str,
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    return _system_doc(session, principal, system, tenant_scope(principal, tenant_id))


@router.get("/systems/{system}/export")
def export_system_doc(
    system: str,
    tenant_id: int | None = None,
    format: str = Query("md", pattern="^(md|docx)$"),  # noqa: A002 - Name der Abfrage
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> Response:
    """Doku je System als Markdown bzw. Word (0.104.0) - Inhalt wie `GET /systems/{system}`,
    Kunden-Benutzer nur ihre Regeln und ohne Geraetenamen."""
    tenant_id = tenant_scope(principal, tenant_id)
    doc = SystemDocOut(**_system_doc(session, principal, system, tenant_id)).model_dump(mode="json")
    tenant = session.get(Tenant, tenant_id) if tenant_id is not None else None
    if principal.is_customer and tenant_id is not None:  # die Middleware sieht nur JSON
        doc, tenant = customer_alias.Aliases(session, [tenant_id]).clean(doc), None
    when = datetime.now(UTC)
    body = word_export.system(doc, when, tenant.name if tenant else None, format)
    name = f"{L('doku', 'doc')}-{word_export.slug(system, 'system')}-{when:%Y-%m-%d}.{format}"
    media = ("text/markdown; charset=utf-8" if format == "md" else
             "application/vnd.openxmlformats-officedocument.wordprocessingml.document")  # fmt: skip
    return Response(content=body, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})  # fmt: skip


def _system_doc(session: Session, principal: Principal, system: str, tenant_id: int | None) -> dict:
    reads.maybe_audit_read(session, principal, tenant_id, "appdoc")
    view = own_view(session, principal, tenant_id) if tenant_id is not None else None
    if principal.is_customer and view is None:
        raise HTTPException(status_code=403, detail="tenant required")
    if view is not None and system not in {
        s.name for s in store.list_systems(session, tenant_id, view)
    }:
        raise HTTPException(status_code=404, detail="system not found")  # ADR 0018
    doc = store.build_doc(session, system, tenant_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="system not found")
    if view is not None:  # nur die Regeln des Kunden
        own = view_ids(session, view, "rule") or set()
        for t in doc["tables"]:
            t["rows"] = [r for r in t["rows"] if r.get(store.RULE) in own]
        doc["impossible"] = [x for x in doc["impossible"] if x["rule_id"] in own]
    return _with_names(session, principal, tenant_id, doc)


def _with_names(session: Session, principal: Principal, tenant_id: int | None, doc: dict) -> dict:
    """Gegenstellen nur mit Adresse samt Namen (0.95.0) - Einstellung des Mandanten, nie fuer
    Kunden-Benutzer. Seit 0.101.0 auch die Zonen je Kategorie (Kunden-Benutzer ohne)."""
    rows = [r for t in doc["tables"] for r in t["rows"]]
    tid = tenant_id if tenant_id is not None else peer_names.tenant_of(session, rows)
    if peer_names.enabled(session, tid, principal.is_customer):
        peer_names.apply(session, tid, rows)
    if principal.is_customer or tid is None:
        appdoc_zones.strip(doc["tables"], store.ZONE_COL)
    else:
        appdoc_zones.heads(appdoc_zones.ZoneMap(session, tid), doc["tables"])
    return doc


@router.patch("/systems/{system}/site", response_model=SystemDocOut)
def assign_system_site(
    system: str,
    body: SiteAssign,
    tenant_id: int | None = None,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
):
    tenant_id = tenant_scope(principal, tenant_id)
    try:
        obj = store.assign_site(session, system, tenant_id, body.site_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if obj is None:
        raise HTTPException(status_code=404, detail="system not found")
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="system.site_assigned",
        subject=system,
        detail={"tenant_id": obj.tenant_id, "site_id": body.site_id},
    )
    doc = store.build_doc(session, system, tenant_id)
    return _with_names(session, principal, obj.tenant_id, doc)
