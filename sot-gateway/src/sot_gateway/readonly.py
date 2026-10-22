"""Nur-Lese-Schutz fuer HTTP-Clients (ADR 0010).

Alle Lese-Clients (FortiGate, APIC, Infoblox, NetBox-Leser) haengen diesen Schutz an ihren
httpx-Client: erlaubt sind nur GET/HEAD/OPTIONS sowie ausdruecklich freigegebene Pfade fuer
POST (z. B. APIC-Login/-Logout). Jede andere Methode bricht **vor** dem Senden mit
`ReadOnlyViolation` ab - so kann kein Lesepfad versehentlich etwas auf einem Zielsystem
aendern. Einziger Schreibweg der Plattform ist der NetBox-Sync (`adapters.netbox_writer`).

`get` wiederholt einen Lese-Zugriff bei kurzen Netzproblemen (Verbindungs-/Lese-Timeout,
abgebrochene Verbindung, 502/503/504 eines Proxys) - gefahrlos, weil nur gelesen wird
(Dev-Befund 0.17.0: einzelne Timeouts liessen ganze Laeufe scheitern).
"""

import time
from collections.abc import Iterable

import httpx

RETRY_DELAYS = (5.0, 15.0, 30.0)  # Sekunden vor dem 2., 3. und 4. Versuch
_RETRY_STATUS = frozenset({502, 503, 504})
_TRANSIENT = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)
_sleep = time.sleep  # Tests ersetzen das Warten

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class ReadOnlyViolation(PermissionError):
    """Ein Lese-Client wollte schreiben (POST/PUT/PATCH/DELETE) - blockiert."""


def enforce_read_only(client: httpx.Client, allow_post: Iterable[str] = ()) -> httpx.Client:
    """Request-Hook anhaengen, der Schreib-Methoden blockiert; gibt den Client zurueck."""
    allowed = tuple(allow_post)

    def guard(request: httpx.Request) -> None:
        method = request.method.upper()
        if method in _SAFE_METHODS:
            return
        if method == "POST" and allowed and request.url.path.endswith(allowed):
            return
        raise ReadOnlyViolation(f"nur lesend: {method} {request.url.path} blockiert")

    hooks = client.event_hooks
    hooks["request"] = [*hooks.get("request", []), guard]
    client.event_hooks = hooks
    return client


def get(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
    """GET mit Wiederholung bei kurzen Netzproblemen; der letzte Fehler bzw. die letzte
    Antwort geht an den Aufrufer (raise_for_status macht er selbst)."""
    for delay in (*RETRY_DELAYS, None):
        try:
            response = client.get(url, **kwargs)
        except _TRANSIENT:
            if delay is None:
                raise
        else:
            if response.status_code not in _RETRY_STATUS or delay is None:
                return response
        _sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover
