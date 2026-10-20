"""Nur-Lese-Schutz fuer HTTP-Clients (ADR 0010).

Alle Lese-Clients (FortiGate, APIC, Infoblox, NetBox-Leser) haengen diesen Schutz an ihren
httpx-Client: erlaubt sind nur GET/HEAD/OPTIONS sowie ausdruecklich freigegebene Pfade fuer
POST (z. B. APIC-Login/-Logout). Jede andere Methode bricht **vor** dem Senden mit
`ReadOnlyViolation` ab - so kann kein Lesepfad versehentlich etwas auf einem Zielsystem
aendern. Einziger Schreibweg der Plattform ist der NetBox-Sync (`adapters.netbox_writer`).
"""

from collections.abc import Iterable

import httpx

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
