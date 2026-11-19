"""Kunden fuer den NetBox-Host (1.11.0, ADR 0054): `GET /netbox-host`.

Nur mit dem Token des NetBox-Hosts (`AUTOMATO_NETBOX_HOST_TOKEN`, Bearer, `deploy/aufbau.sh
netbox-host-token`) - ein eigenes Token, nicht das des Cockpits. Ohne eingerichtetes Token gibt es
den Endpunkt nicht (404), falsch = 401.

Seit 1.13.0 (ADR 0056) liefert der Agent des NetBox-Hosts damit auch den Zugang fuer Automato:
`PUT /netbox-host/zugang` `{kurzname, lesen, sync}` - nur fuer Kunden mit NetBox, nur fuer ein
Quellsystem NetBox, nie fuer andere Quellen. Der Inhalt wird von Hand geprueft (nicht mit einem
Pydantic-Modell), damit keine Fehlermeldung die Tokens zurueckgibt.

Seit 1.14.0 (ADR 0059) meldet der Agent nach jedem Abgleich seinen Ist-Stand:
`PUT /netbox-host/stand` `{vertrag, zeit, instanzen, ohne_freigabe, geloescht,
loeschen_fehlgeschlagen}` - nur Kurznamen und Zeit (`store/ist_stand.py`); Automato schreibt
damit die Loeschauftraege fort (`store/abbau.py`) und nennt in der Antwort die noch offenen.
"""

import hmac
import re
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import credentials
from app.config import Settings, get_settings
from app.db import get_session
from app.i18n import L
from app.store import abbau, ist_stand, netbox_host
from app.store.portal import SHORT_NAME

router = APIRouter(prefix="/netbox-host", tags=["netbox-host"])

# NetBox-Token: v1 sind 40 Hex-Zeichen (netbox-host.sh), Platz fuer kuenftige Formen
_TOKEN = re.compile(r"^[A-Za-z0-9._-]{32,256}$")


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


def _pruefen(data: object) -> tuple[str, str, str]:
    """Anfrage pruefen - Fehler nennen nur das Feld, nie den Wert."""
    if not isinstance(data, dict):
        raise HTTPException(422, L("Objekt erwartet", "Object expected"))
    kurz, lesen, sync = data.get("kurzname"), data.get("lesen"), data.get("sync")
    if not isinstance(kurz, str) or not SHORT_NAME.match(kurz):
        raise HTTPException(422, L("kurzname ungültig", "kurzname invalid"))
    if not isinstance(lesen, str) or not _TOKEN.match(lesen):
        raise HTTPException(422, L("lesen: Token ungültig", "lesen: token invalid"))
    if not isinstance(sync, str) or not _TOKEN.match(sync):
        raise HTTPException(422, L("sync: Token ungültig", "sync: token invalid"))
    if hmac.compare_digest(lesen, sync):
        raise HTTPException(422, L("lesen und sync gleich", "lesen and sync are the same"))
    return kurz, lesen, sync


@router.put("/zugang", dependencies=[Depends(require_netbox_host_token)])
def put_access(
    data: Annotated[Any, Body()] = None,
    session: Session = Depends(get_session, scope="function"),
    settings: Settings = Depends(get_settings),
):
    """Zugang fuer Automato aus dem NetBox-Host (1.13.0, ADR 0056): `200` uebernommen,
    unveraendert bzw. vorhanden (bleibt, nie ueberschrieben), `409` nicht uebernommen (kein
    Mandant, andere Adresse, keine Domain), `404` kein Kunde mit NetBox. Antwort `{status,
    detail}` - ohne Mandant, ohne Tokens."""
    kurz, lesen, sync = _pruefen(data)
    pc = netbox_host.kunde(session, kurz)
    if pc is None:
        raise HTTPException(404, L("Kein Kunde mit NetBox", "No customer with NetBox"))
    try:
        ergebnis = netbox_host.uebernehmen(session, pc, lesen, sync, settings.front_domain)
    except credentials.CredentialsUnavailable as exc:
        session.rollback()
        raise HTTPException(503, L(
            "Zugangsdaten können nicht verschlüsselt werden (Schlüssel fehlt)",
            "Credentials cannot be encrypted (key missing)",
        )) from exc  # fmt: skip
    except IntegrityError as exc:  # gleichzeitig in der Oberflaeche angelegt
        session.rollback()
        raise HTTPException(409, L(
            "Quellsysteme gleichzeitig geändert – der nächste Abgleich versucht es wieder",
            "Source systems changed at the same time – the next sync tries again",
        )) from exc  # fmt: skip
    return JSONResponse(status_code=ergebnis.http, content=ergebnis.antwort())


@router.put("/stand", dependencies=[Depends(require_netbox_host_token)])
def put_stand(
    data: Annotated[Any, Body()] = None,
    session: Session = Depends(get_session, scope="function"),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Ist-Stand des NetBox-Hosts nach einem Abgleich (ADR 0059): ersetzt die vorige Meldung,
    schreibt den Teil NetBox der Loeschauftraege fort. `422` nennt nur das Feld."""
    try:
        meldung = ist_stand.netbox_host_pruefen(data)
    except ist_stand.MeldungFehler as exc:
        raise HTTPException(422, str(exc)) from exc
    ist_stand.netbox_host_melden(session, meldung)
    abbau.fortschreiben(session, settings, teile=("netbox",), akteur=netbox_host.AKTEUR)
    return {"vertrag": ist_stand.VERTRAG, "status": "gespeichert",
            "loeschen": abbau.netbox_loeschen(session)}  # fmt: skip
