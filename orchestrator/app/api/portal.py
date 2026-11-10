"""Kunden-Portal (1.3.0, ADR 0050): Kunden des Front-Proxys in der Oberflaeche pflegen.

Nur Admin. Jede Aenderung landet im Audit und als Soll-Stand (`soll.json`) im Ordner des
Host-Agenten (`store/portal.py`); der Agent auf der VM uebernimmt sie in den Front-Proxy
(`front.sh sync`) und meldet den Stand zurueck. Realm, Mandant und Kunden steuern die Anmeldung
der Benutzer eines Kunden-Realms (`auth/oidc.py`) - sie gehen nicht an den Host. Der Zugriff je
Kunde (1.5.0: ueberall, intern, netze) geht an den Host; der Front-Proxy sperrt danach.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.auth import realms
from app.auth.deps import require_admin
from app.auth.principal import Principal
from app.config import Settings, get_settings
from app.db import get_session
from app.i18n import L
from app.models import PortalCustomer
from app.store import portal

router = APIRouter(prefix="/portal", tags=["portal"])


class LinkIn(BaseModel):
    title: str = Field(default="", max_length=200)
    url: str = Field(default="", max_length=600)
    text: str = Field(default="", max_length=400)


class PortalIn(BaseModel):
    short_name: str = Field(max_length=60)
    name: str = Field(min_length=1, max_length=200)
    doc: bool = False
    um: bool = False
    cmdb: bool = False
    links: list[LinkIn] = Field(default_factory=list)
    realm: str | None = Field(default=None, max_length=80)
    tenant_id: int | None = None
    customer_ids: list[int] = Field(default_factory=list)
    default_role: str = "viewer"
    # Zugriff je Kunde (1.5.0): ueberall | intern | netze (dann access_nets)
    access: str = "ueberall"
    access_nets: list[str] = Field(default_factory=list, max_length=200)


def _keycloak(settings: Settings) -> dict:
    """Realm von Automato selbst und Basis-Adresse von Keycloak (Anzeige, Befehle)."""
    parsed = portal.realm_of(settings.oidc_issuer) if settings.auth_mode == "oidc" else None
    return {"base": parsed[0] if parsed else "", "main_realm": parsed[1] if parsed else ""}


def _apply(session: Session, pc: PortalCustomer, data: PortalIn) -> dict:
    """Angaben pruefen und uebernehmen; liefert die geaenderten Felder fuer das Audit."""
    try:
        short = portal.check_short_name(data.short_name)
        name = data.name.strip()
        if not name or any(c in name for c in "\t\r\n"):
            raise portal.PortalError(
                L(
                    "Anzeigename fehlt bzw. enthält Tabulatoren/Umbrüche",
                    "Display name missing or contains tabs/line breaks",
                )
            )
        links = portal.check_links([link.model_dump() for link in data.links])
        realm = portal.check_realm(data.realm)
        customers = portal.check_scope(session, data.tenant_id, data.customer_ids)
        role = portal.check_role(data.default_role)
        access, nets = portal.check_access(data.access, data.access_nets)
    except portal.PortalError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    clash = session.scalar(select(PortalCustomer).where(
        PortalCustomer.short_name == short, PortalCustomer.id != (pc.id or 0)))  # fmt: skip
    if clash is not None:
        raise HTTPException(
            status_code=409,
            detail=L(f"Kurzname '{short}' gibt es schon", f"Short name '{short}' already exists"),
        )
    if realm:
        other = session.scalar(select(PortalCustomer).where(
            PortalCustomer.realm == realm, PortalCustomer.id != (pc.id or 0)))  # fmt: skip
        if other is not None:
            raise HTTPException(status_code=409, detail=L(
                f"Realm '{realm}' gehört schon zu '{other.short_name}'",
                f"Realm '{realm}' already belongs to '{other.short_name}'"))  # fmt: skip
    new = {"short_name": short, "name": name, "doc": data.doc, "um": data.um,
           "cmdb": data.cmdb, "links": links, "realm": realm, "tenant_id": data.tenant_id,
           "customer_ids": customers, "default_role": role, "access": access,
           "access_nets": nets}  # fmt: skip
    changed = {k: v for k, v in new.items() if getattr(pc, k, None) != v}
    for key, value in new.items():
        setattr(pc, key, value)
    return changed


def _publish(session: Session, settings: Settings) -> str | None:
    """Soll-Stand fuer den Host-Agenten schreiben; Fehler als Hinweis (die Daten sind
    gespeichert, der Agent holt sie mit dem naechsten erfolgreichen Schreiben)."""
    realms.invalidate()
    try:
        portal.write_desired(session, settings.front_state_dir)
    except OSError as exc:
        return L(f"Gespeichert, aber Soll-Stand nicht geschrieben ({settings.front_state_dir}): "
                 f"{exc} – Ordner einrichten: sudo deploy/front/front.sh agent",
                 f"Saved, but the desired state was not written ({settings.front_state_dir}): "
                 f"{exc} – set up the folder: sudo deploy/front/front.sh agent")  # fmt: skip
    return None


@router.get("/customers")
def list_customers(
    _: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
    settings: Settings = Depends(get_settings),
):
    return {
        "customers": [portal.out(pc, settings.front_domain) for pc in portal.listing(session)],
        "agent": portal.agent_status(settings.front_state_dir),
        "domain": settings.front_domain,
        "keycloak": _keycloak(settings),
    }


@router.post("/customers", status_code=201)
def create_customer(
    data: PortalIn,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
    settings: Settings = Depends(get_settings),
):
    pc = PortalCustomer(links=[], customer_ids=[])
    changed = _apply(session, pc, data)
    pc.updated_by, pc.updated_at = principal.subject, datetime.now(UTC)
    session.add(pc)
    session.flush()
    audit.record(session, actor=principal.subject, action="portal.customer.created",
                 subject=pc.short_name, detail=changed)  # fmt: skip
    session.commit()
    note = _publish(session, settings)
    return {**portal.out(pc, settings.front_domain), "note": note}


@router.put("/customers/{pc_id}")
def update_customer(
    pc_id: int,
    data: PortalIn,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
    settings: Settings = Depends(get_settings),
):
    pc = session.get(PortalCustomer, pc_id)
    if pc is None:
        raise HTTPException(status_code=404, detail="portal customer not found")
    previous = pc.short_name
    changed = _apply(session, pc, data)
    pc.updated_by, pc.updated_at = principal.subject, datetime.now(UTC)
    audit.record(session, actor=principal.subject, action="portal.customer.updated",
                 subject=previous, detail=changed)  # fmt: skip
    session.commit()
    note = _publish(session, settings)
    return {**portal.out(pc, settings.front_domain), "note": note}


@router.delete("/customers/{pc_id}")
def delete_customer(
    pc_id: int,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
    settings: Settings = Depends(get_settings),
):
    pc = session.get(PortalCustomer, pc_id)
    if pc is None:
        raise HTTPException(status_code=404, detail="portal customer not found")
    audit.record(session, actor=principal.subject, action="portal.customer.deleted",
                 subject=pc.short_name, detail={"realm": pc.realm})  # fmt: skip
    session.delete(pc)
    session.commit()
    return {"deleted": pc_id, "note": _publish(session, settings)}
