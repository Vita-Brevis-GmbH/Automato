"""Zugang des Cockpits (1.10.0 Inventar, ADR 0053; 1.11.0 Freigaben, ADR 0054).

Nur mit dem Token des Cockpits (`AUTOMATO_COCKPIT_TOKEN`, Bearer, `deploy/aufbau.sh
cockpit-token`), nie mit der Anmeldung einer Person und nie ohne. Ohne eingerichtetes Token gibt
es die Endpunkte nicht (404), ein falsches Token ergibt 401.
"""

import hmac

from fastapi import Depends, Header, HTTPException

from app.config import Settings, get_settings
from app.i18n import L


def require_cockpit_token(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.cockpit_token:
        raise HTTPException(
            404, L("Zugang des Cockpits nicht eingerichtet", "cockpit access not set up")
        )
    art, _, token = (authorization or "").partition(" ")
    if art.lower() != "bearer" or not hmac.compare_digest(
        token.strip().encode(), settings.cockpit_token.encode()
    ):
        raise HTTPException(
            401, L("Token des Cockpits fehlt oder falsch", "cockpit token missing or wrong"),
            headers={"WWW-Authenticate": "Bearer"},
        )  # fmt: skip
