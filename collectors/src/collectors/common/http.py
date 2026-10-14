"""Gemeinsamer HTTP-Zugang zum Orchestrator mit optionalem mTLS (ADR 0008).

Alle ausgehenden Aufrufe des Agenten (Ergebnis-Upload, Agent-Pull) laufen hierueber, damit
mTLS-Client-Zertifikate und CA-Pinning an **einer** Stelle konfiguriert sind:
- `AUTOMATO_ORCHESTRATOR_CA_BUNDLE` pinnt die Server-CA (sonst System-Trust/`verify_tls`).
- `AUTOMATO_CLIENT_CERT` + `AUTOMATO_CLIENT_KEY` aktivieren das Client-Zertifikat (mTLS).
"""

from typing import Any

import httpx

from collectors.config import Settings, get_settings


def client_kwargs(settings: Settings) -> dict[str, Any]:
    """httpx.Client-Argumente aus der Konfiguration (verify/cert) - pure, testbar."""
    verify: str | bool = settings.orchestrator_ca_bundle or settings.orchestrator_verify_tls
    cert: tuple[str, str] | str | None = None
    if settings.client_cert and settings.client_key:
        cert = (settings.client_cert, settings.client_key)
    elif settings.client_cert:
        cert = settings.client_cert  # kombiniertes PEM (Cert+Key)
    return {"verify": verify, "cert": cert, "timeout": 30.0}


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
