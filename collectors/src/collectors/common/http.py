"""Gemeinsamer HTTP-Zugang zum Orchestrator mit optionalem mTLS (ADR 0008).

Alle ausgehenden Aufrufe des Agenten (Ergebnis-Upload, Agent-Pull) laufen hierueber, damit
mTLS-Client-Zertifikate und CA-Pinning an **einer** Stelle konfiguriert sind:
- `AUTOMATO_ORCHESTRATOR_CA_BUNDLE` pinnt die Server-CA (sonst System-Trust/`verify_tls`).
- `AUTOMATO_CLIENT_CERT` + `AUTOMATO_CLIENT_KEY` aktivieren das Client-Zertifikat (mTLS).
"""

import ssl
from typing import Any

import httpx

from collectors.config import Settings, get_settings


def describe_error(exc: Exception) -> str:
    """Verstaendliche Fehlermeldung fuer Lauf/Log: bei HTTP-Fehlern mit Ziel-URL (ohne
    Header/Tokens) und Status bzw. Verbindungsursache, sonst Typ + Text."""
    if not isinstance(exc, httpx.HTTPError):
        return str(exc) or type(exc).__name__
    try:
        url = f" {exc.request.url.copy_with(query=None)}"
    except RuntimeError:  # httpx: Fehler ohne zugehoerigen Request
        url = ""
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            body = exc.response.json()
            detail = body.get("detail") if isinstance(body, dict) else None
        except ValueError:
            detail = None
        text = f" - {detail}" if detail else ""
        return f"HTTP {exc.response.status_code}{url}{text}"
    if isinstance(exc, httpx.ConnectError | httpx.TimeoutException):
        return f"nicht erreichbar{url}: {type(exc).__name__} {exc}".rstrip()
    return f"{type(exc).__name__}: {exc}{url}"


def client_kwargs(settings: Settings) -> dict[str, Any]:
    """httpx.Client-Argumente aus der Konfiguration - ein SSL-Kontext, sobald eine CA gepinnt
    oder ein Client-Zertifikat gesetzt ist.

    Bewusst kein `verify=<pfad>` + `cert=`: httpx (ab 0.28) legt das Client-Zertifikat in
    dieser Kombination nicht vor - das Gateway lehnt dann mit "certificate required" ab."""
    if not (settings.orchestrator_ca_bundle or settings.client_cert):
        return {"verify": settings.orchestrator_verify_tls, "timeout": 30.0}
    if settings.orchestrator_ca_bundle:
        ctx = ssl.create_default_context(cafile=settings.orchestrator_ca_bundle)
    else:
        ctx = ssl.create_default_context()
        if not settings.orchestrator_verify_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
    if settings.client_cert:  # Cert + Key oder kombiniertes PEM
        ctx.load_cert_chain(settings.client_cert, settings.client_key or None)
    return {"verify": ctx, "timeout": 30.0}


def _client() -> httpx.Client:
    return httpx.Client(**client_kwargs(get_settings()))


def post_json(path_url: str, json: dict, headers: dict[str, str]) -> httpx.Response:
    with _client() as client:
        response = client.post(path_url, json=json, headers=headers)
    response.raise_for_status()
    return response


def get_json(path_url: str, params: dict | None, headers: dict[str, str]) -> httpx.Response:
    with _client() as client:
        response = client.get(path_url, params=params, headers=headers)
    response.raise_for_status()
    return response


def post(path_url: str, headers: dict[str, str]) -> httpx.Response:
    with _client() as client:
        response = client.post(path_url, headers=headers)
    response.raise_for_status()
    return response
