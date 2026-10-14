"""Öffentliche Auth-Konfiguration fürs Web-UI (kein Token nötig).

Das SPA fragt hier ab, ob und gegen welches Keycloak es sich anmelden soll. Es werden
nur **öffentliche** OIDC-Client-Infos ausgeliefert (Issuer, public Client-ID) — keine
Secrets. Im dev-Modus meldet der Endpunkt `auth_mode=dev`; dann ist kein Login nötig.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.config import Settings, get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthConfigOut(BaseModel):
    auth_mode: str  # dev | oidc
    issuer: str
    client_id: str
    scope: str = "openid profile"


@router.get("/config", response_model=AuthConfigOut)
def auth_config(settings: Settings = Depends(get_settings)):
    return AuthConfigOut(
        auth_mode=settings.auth_mode,
        issuer=settings.oidc_issuer,
        client_id=settings.oidc_web_client_id,
    )
