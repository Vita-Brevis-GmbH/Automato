"""Ergebnis-Upload an die interne Orchestrator-API (einziger Rueckkanal).

Der zentrale Worker meldet mit dem internen Token; ein Agent (Pull-Modell) mit seinem
eigenen Agent-Token - der Orchestrator nimmt es nur fuer Laeufe an, die genau dieser Agent
abgeholt hat (ADR 0009). Agenten brauchen das interne Token damit nicht mehr.

Robust (Dev-Befund 0.17.0, „ReadTimeout“ beim Ergebnis-Upload): Das Einspielen grosser
Laeufe dauert, der Agent wartet deshalb bis zu 10 Minuten auf die Quittung und wiederholt den
Upload bei Netzproblemen. Das ist gefahrlos: Der Orchestrator nimmt ein Ergebnis fuer einen
schon abgeschlossenen Lauf nicht noch einmal an, sondern quittiert dessen Stand. Teil-Uploads
der Referenzquellen werden nur wiederholt, wenn sie den Orchestrator nicht erreicht haben.
"""

import logging
import time

import httpx

from collectors.common import http
from collectors.common.dto import CollectorResult, ReferenceBatch
from collectors.common.selfupdate import agent_headers
from collectors.config import get_settings

log = logging.getLogger(__name__)

RESULT_TIMEOUT = httpx.Timeout(30.0, read=600.0)  # Einspielen grosser Laeufe (Ingest)
BATCH_TIMEOUT = httpx.Timeout(30.0, read=300.0)
RETRY_DELAYS = (10.0, 30.0, 60.0)  # Sekunden vor dem 2., 3. und 4. Versuch
_RESENDABLE = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout)  # Anfrage kam nicht an
_sleep = time.sleep  # Tests ersetzen das Warten


def _post(url: str, body: dict, headers: dict, timeout: httpx.Timeout, retry_on: tuple):
    for delay in (*RETRY_DELAYS, None):
        try:
            return http.post_json(url, json=body, headers=headers, timeout=timeout)
        except retry_on as exc:
            if delay is None:
                raise
            log.warning("%s: %s - neuer Versuch in %.0f s", url, http.describe_error(exc), delay)
            _sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def _headers(via_agent: bool) -> dict[str, str]:
    settings = get_settings()
    if via_agent:
        return agent_headers()
    return {"X-Internal-Token": settings.internal_api_token}


def submit_references(run_id: str, batch: ReferenceBatch, via_agent: bool = False) -> None:
    """Teil-Upload der Referenzeintraege eines Laufs (Infoblox/NetBox, ADR 0010)."""
    settings = get_settings()
    _post(
        f"{settings.orchestrator_url}/internal/collector-runs/{run_id}/references",
        batch.model_dump(),
        _headers(via_agent),
        BATCH_TIMEOUT,
        _NOT_SENT,  # angekommen und gespeichert: nochmals senden gaebe Doppelte
    )


def submit_result(run_id: str, result: CollectorResult, via_agent: bool = False) -> dict | None:
    """Ergebnis abliefern; gibt die Quittung des Orchestrators zurueck (`status`, `message`)."""
    settings = get_settings()
    response = _post(
        f"{settings.orchestrator_url}/internal/collector-runs/{run_id}/result",
        result.model_dump(),
        _headers(via_agent),
        RESULT_TIMEOUT,
        _RESENDABLE,  # der Orchestrator quittiert einen schon abgeschlossenen Lauf nur
    )
    try:
        return response.json()
    except ValueError:
        return None  # aeltere Orchestratoren antworten mit 204 ohne Inhalt


def confirmed(result: CollectorResult, ack: dict | None) -> CollectorResult:
    """Ergebnis mit dem Urteil des Orchestrators - z. B. failed, wenn das Einspielen in den
    Store scheitert, obwohl das Lesen geklappt hat, bzw. completed, wenn ein abgebrochener
    Upload doch noch eingespielt wurde. Fehlschlaege landen auch im Log."""
    final = ack.get("status") if isinstance(ack, dict) else None
    if final in ("completed", "failed") and final != result.status:
        result = result.model_copy(update={"status": final, "message": ack.get("message")})
    if result.status == "failed":
        run_id = ack.get("run_id") if isinstance(ack, dict) else None
        log.warning("Lauf %s fehlgeschlagen: %s", run_id or "?", result.message)
    return result
