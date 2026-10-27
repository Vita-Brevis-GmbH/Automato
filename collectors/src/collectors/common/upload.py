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
from collectors.common.i18n import L, language_header
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
            log.warning(L("%s: %s - neuer Versuch in %.0f s", "%s: %s - retrying in %.0f s"),
                        url, http.describe_error(exc), delay)  # fmt: skip
            _sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def _headers(via_agent: bool) -> dict[str, str]:
    """Token und Sprache der Konsole: der Orchestrator speichert deutsch und gibt die Meldung
    in der Quittung zusaetzlich uebersetzt zurueck (`shown`, ADR 0028 Schritt 4)."""
    settings = get_settings()
    if via_agent:
        return agent_headers()
    return {"X-Internal-Token": settings.internal_api_token, **language_header()}


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
    Upload doch noch eingespielt wurde. Fehlschlaege landen auch im Log.

    Die Meldung bleibt deutsch (`message`); passt die Quittung zu ihr, kommt die Uebersetzung
    des Orchestrators in der Sprache der Konsole nach `shown` (0.88.0)."""
    ack = ack if isinstance(ack, dict) else {}
    final = ack.get("status")
    if final in ("completed", "failed") and final != result.status:
        result = result.model_copy(update={"status": final, "message": ack.get("message")})
    if ack.get("shown") and ack.get("message") == result.message:
        result = result.model_copy(update={"shown": ack["shown"]})
    if result.status == "failed":
        log.warning(L("Lauf %s fehlgeschlagen: %s", "Run %s failed: %s"),
                    ack.get("run_id") or "?", result.shown or result.message)  # fmt: skip
    return result
