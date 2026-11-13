"""Kunden fuer den NetBox-Host (1.11.0, ADR 0054): `GET /netbox-host`.

Nur mit dem Token des NetBox-Hosts (`AUTOMATO_NETBOX_HOST_TOKEN`, Bearer, `deploy/aufbau.sh
netbox-host-token`) - ein eigenes Token, nicht das des Cockpits: der NetBox-Host darf nur diese
Liste lesen. Ohne eingerichtetes Token gibt es den Endpunkt nicht (404), falsch = 401.
"""

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session
from app.i18n import L
from app.store import netbox_host

router = APIRouter(prefix="/netbox-host", tags=["netbox-host"])


def require_netbox_host_token(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.netbox_host_token:
        raise HTTPException(
            404, L("Zugang des NetBox-Hosts nicht eingerichtet", "NetBox host access not set up")
        )
    art, _, token = (authorization or "").partition(" ")
    if art.lower() != "bearer" or not hmac.compare_digest(
        token.strip().encode(), settings.netbox_host_token.encode()
    ):
        raise HTTPException(
            401,
            L("Token des NetBox-Hosts fehlt oder falsch", "NetBox host token missing or wrong"),
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.get("", dependencies=[Depends(require_netbox_host_token)])
def get_customers(
    session: Session = Depends(get_session, scope="function"),
    settings: Settings = Depends(get_settings),
) -> dict:
    return netbox_host.kunden(session, settings.front_domain)
