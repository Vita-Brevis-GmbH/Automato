"""Zugang des Cockpits (1.10.0 Inventar, ADR 0053; 1.11.0 Freigaben, ADR 0054; 1.13.0 Kopplung,
ADR 0057).

Nur mit dem Token des Cockpits (Bearer), nie mit der Anmeldung einer Person und nie ohne. Gueltig
ist das **gekoppelte** Token (Einmal-Code, `POST /kopplung`, nur als Hash in der Datenbank,
`store/kopplung.py`) oder - rueckwaertskompatibel - `AUTOMATO_COCKPIT_TOKEN` aus `.env`
(`deploy/aufbau.sh cockpit-token`). Ohne beides gibt es die Endpunkte nicht (404), ein falsches
Token ergibt 401.
"""

import hmac

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session
from app.i18n import L
from app.store import kopplung


def bearer(authorization: str | None) -> str:
    """Token aus `Authorization: Bearer <token>` - leer, wenn es fehlt oder anders lautet."""
    art, _, token = (authorization or "").partition(" ")
    return token.strip() if art.lower() == "bearer" else ""


def require_cockpit_token(
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_session, scope="function"),
    settings: Settings = Depends(get_settings),
) -> None:
    env_token = settings.cockpit_token
    if not env_token and not kopplung.coupled(session, kopplung.COCKPIT):
        raise HTTPException(
            404, L("Zugang des Cockpits nicht eingerichtet", "cockpit access not set up")
        )
    token = bearer(authorization)
    if token and env_token and hmac.compare_digest(token.encode(), env_token.encode()):
        return
    if token and kopplung.token_valid(session, kopplung.COCKPIT, token):
        return
    raise HTTPException(
        401, L("Token des Cockpits fehlt oder falsch", "cockpit token missing or wrong"),
        headers={"WWW-Authenticate": "Bearer"},
    )  # fmt: skip
