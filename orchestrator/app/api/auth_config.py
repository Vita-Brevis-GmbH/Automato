"""Öffentliche Auth-Konfiguration fürs Web-UI (kein Token nötig).

Das SPA fragt hier ab, ob und gegen welches Keycloak es sich anmelden soll. Es werden
nur **öffentliche** OIDC-Client-Infos ausgeliefert (Issuer, public Client-ID, Link zur
Benutzerverwaltung) — keine Secrets. Im dev-Modus meldet der Endpunkt `auth_mode=dev`;
dann ist kein Login nötig.

Realm je Kunde (1.3.0, ADR 0050): mit `?host=<kurzname>-doc.<domain>` liefert er den Realm
dieses Kunden aus dem Kunden-Portal - die Oberfläche unter der Adresse des Kunden meldet sich
dort an. Ohne Treffer (bzw. ohne Datenbank) gilt der Realm von Automato.

Rücklink zum Kunden-Portal (1.17.0): unter derselben Adresse nennt `portal_url` die Startseite des
Kunden (`https://<kurzname>.<domain>`) - aus dem Kunden-Portal bzw. in einer eigenen Instanz aus
ihrer .env; leer = kein Link.
"""

import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings, get_settings
from app.db import get_session_factory
from app.store import portal

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthConfigOut(BaseModel):
    auth_mode: str  # dev | oidc
    issuer: str
    client_id: str
    scope: str = "openid profile"
    admin_console_url: str = ""  # Keycloak-Benutzerverwaltung des Realms (Link im UI)
    portal_url: str = ""  # Startseite des Kunden-Portals (1.17.0, Link im UI; leer = keiner)


def admin_console_url(settings: Settings) -> str:
    """Konfigurierte Admin-Konsole oder aus dem Issuer abgeleitet (leer, wenn unbekannt)."""
    if settings.oidc_admin_console_url:
        return settings.oidc_admin_console_url.rstrip("/")
    parts = urlsplit(settings.oidc_issuer)
    segments = [s for s in parts.path.split("/") if s]
    if parts.scheme and parts.netloc and len(segments) >= 2 and segments[-2] == "realms":
        return f"{parts.scheme}://{parts.netloc}/admin/master/console/#/{segments[-1]}"
    return ""


def _customer_issuer(settings: Settings, factory, host: str | None) -> str | None:
    """Issuer des Kunden-Realms zur Adresse (None = Realm von Automato)."""
    main = portal.realm_of(settings.oidc_issuer)
    if settings.auth_mode != "oidc" or not host or main is None:
        return None
    try:
        with factory() as session:
            realm = portal.realm_for_host(session, host)
    except SQLAlchemyError as exc:
        log.warning("Realm zur Adresse %s nicht lesbar: %s", host, exc)
        return None
    return f"{main[0]}/realms/{realm}" if realm and realm != main[1] else None


def _portal_url(settings: Settings, factory, host: str | None) -> str:
    """Startseite des Kunden-Portals zur Adresse der Oberfläche (leer = kein Rücklink)."""
    if settings.instanz_kunde:  # eigene Instanz (ADR 0061): Kunde und Domain aus ihrer .env
        return portal.instance_start(host, settings.instanz_kunde, settings.instanz_domain)
    if not settings.front_domain or not host:
        return ""
    try:
        with factory() as session:
            return portal.start_for_host(session, host, settings.front_domain)
    except SQLAlchemyError as exc:
        log.warning("Kunden-Portal zur Adresse %s nicht lesbar: %s", host, exc)
        return ""


@router.get("/config", response_model=AuthConfigOut)
def auth_config(
    host: str | None = None,
    settings: Settings = Depends(get_settings),
    factory=Depends(get_session_factory),
):
    customer = _customer_issuer(settings, factory, host)
    return AuthConfigOut(
        auth_mode=settings.auth_mode,
        issuer=customer or settings.oidc_issuer,
        client_id=settings.oidc_web_client_id,
        # Kunden-Realm: keine Benutzerverwaltung von Automato verlinken
        admin_console_url="" if customer else admin_console_url(settings),
        portal_url=_portal_url(settings, factory, host),
    )
