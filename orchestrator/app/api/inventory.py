"""Inventar fuers Cockpit (1.10.0, ADR 0053): `GET /inventory`.

Nur mit dem Token des Cockpits (`AUTOMATO_COCKPIT_TOKEN`, Bearer), nie mit einer Anmeldung von
Personen und nie ohne: die Antwort nennt die Kunden des Front-Proxys und ihre Adressen. Ohne
eingerichtetes Token gibt es den Endpunkt nicht (404). Nur lesen (`store/inventory.py`).
"""

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException

from app.config import Settings, get_settings
from app.i18n import L
from app.store import inventory

router = APIRouter(prefix="/inventory", tags=["inventory"])


def require_cockpit_token(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.cockpit_token:
        raise HTTPException(404, L("Inventar nicht eingerichtet", "inventory not set up"))
    art, _, token = (authorization or "").partition(" ")
    if art.lower() != "bearer" or not hmac.compare_digest(
        token.strip().encode(), settings.cockpit_token.encode()
    ):
        raise HTTPException(
            401, L("Token des Cockpits fehlt oder falsch", "cockpit token missing or wrong"),
            headers={"WWW-Authenticate": "Bearer"},
        )  # fmt: skip


@router.get("", dependencies=[Depends(require_cockpit_token)])
def get_inventory(settings: Settings = Depends(get_settings)) -> dict:
    return inventory.inventory(settings)
