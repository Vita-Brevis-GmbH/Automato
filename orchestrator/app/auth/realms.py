"""Kunden-Realms (1.3.0, ADR 0050): welcher Keycloak-Realm zu welchem Kunden gehoert.

Aus dem Kunden-Portal (`portal_customer.realm`) gelesen und kurz gemerkt, damit nicht jede
Anfrage die Datenbank fragt; eine Aenderung im Portal leert den Speicher sofort (in den anderen
Prozessen nach spaetestens `TTL` Sekunden). Ist die Liste nicht lesbar, gibt es keine
Kunden-Realms - deren Anmeldungen scheitern dann (fail-closed), der Realm von Automato bleibt.
"""

import logging
import threading
import time

from sqlalchemy.exc import SQLAlchemyError

from app.store import portal

log = logging.getLogger(__name__)

TTL = 30.0
_lock = threading.Lock()
_cache: dict = {"at": float("-inf"), "value": {}}


def scopes(factory, settings) -> dict[str, portal.RealmScope]:
    now = time.monotonic()
    with _lock:
        if now - _cache["at"] < TTL:
            return _cache["value"]
    parsed = portal.realm_of(settings.oidc_issuer)
    try:
        with factory() as session:
            value = portal.realm_scopes(session, parsed[1] if parsed else None)
    except SQLAlchemyError as exc:
        log.warning("Kunden-Realms nicht lesbar: %s", exc)
        value = {}
    with _lock:
        _cache.update(at=now, value=value)
    return value


def invalidate() -> None:
    with _lock:
        _cache["at"] = float("-inf")
