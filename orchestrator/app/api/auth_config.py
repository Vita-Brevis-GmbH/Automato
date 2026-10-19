"""Öffentliche Auth-Konfiguration fürs Web-UI (kein Token nötig).

Das SPA fragt hier ab, ob und gegen welches Keycloak es sich anmelden soll. Es werden
nur **öffentliche** OIDC-Client-Infos ausgeliefert (Issuer, public Client-ID, Link zur
Benutzerverwaltung) — keine Secrets. Im dev-Modus meldet der Endpunkt `auth_mode=dev`;
dann ist kein Login nötig.
"""

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.config import Settings, get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthConfigOut(BaseModel):
    auth_mode: str  # dev | oidc
    issuer: str
    client_id: str
    scope: str = "openid profile"
    admin_console_url: str = ""  # Keycloak-Benutzerverwaltung des Realms (Link im UI)


def admin_console_url(settings: Settings) -> str:
    """Konfigurierte Admin-Konsole oder aus dem Issuer abgeleitet (leer, wenn unbekannt)."""
    if settings.oidc_admin_console_url:
        return settings.oidc_admin_console_url.rstrip("/")
    parts = urlsplit(settings.oidc_issuer)
    segments = [s for s in parts.path.split("/") if s]
    if parts.scheme and parts.netloc and len(segments) >= 2 and segments[-2] == "realms":
        return f"{parts.scheme}://{parts.netloc}/admin/master/console/#/{segments[-1]}"
    return ""


@router.get("/config", response_model=AuthConfigOut)
def auth_config(settings: Settings = Depends(get_settings)):
    return AuthConfigOut(
        auth_mode=settings.auth_mode,
        issuer=settings.oidc_issuer,
        client_id=settings.oidc_web_client_id,
        admin_console_url=admin_console_url(settings),
    )
