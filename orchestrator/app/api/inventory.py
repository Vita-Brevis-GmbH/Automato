"""Inventar fuers Cockpit (1.10.0, ADR 0053): `GET /inventory`.

Nur mit dem Token des Cockpits (`AUTOMATO_COCKPIT_TOKEN`, Bearer, `auth/cockpit.py`), nie mit
einer Anmeldung von Personen und nie ohne: die Antwort nennt die Kunden des Front-Proxys und ihre
Adressen. Ohne eingerichtetes Token gibt es den Endpunkt nicht (404). Nur lesen
(`store/inventory.py`).
"""

from fastapi import APIRouter, Depends

from app.auth.cockpit import require_cockpit_token
from app.config import Settings, get_settings
from app.store import inventory

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("", dependencies=[Depends(require_cockpit_token)])
def get_inventory(settings: Settings = Depends(get_settings)) -> dict:
    return inventory.inventory(settings)
