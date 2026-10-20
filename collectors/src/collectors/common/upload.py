"""Ergebnis-Upload an die interne Orchestrator-API (einziger Rueckkanal).

Der zentrale Worker meldet mit dem internen Token; ein Agent (Pull-Modell) mit seinem
eigenen Agent-Token - der Orchestrator nimmt es nur fuer Laeufe an, die genau dieser Agent
abgeholt hat (ADR 0009). Agenten brauchen das interne Token damit nicht mehr.
"""

import logging

from collectors.common import http
from collectors.common.dto import CollectorResult, ReferenceBatch
from collectors.common.selfupdate import agent_headers
from collectors.config import get_settings

log = logging.getLogger(__name__)


def _headers(via_agent: bool) -> dict[str, str]:
    settings = get_settings()
    if via_agent:
        return agent_headers()
    return {"X-Internal-Token": settings.internal_api_token}


def submit_references(run_id: str, batch: ReferenceBatch, via_agent: bool = False) -> None:
    """Teil-Upload der Referenzeintraege eines Laufs (Infoblox/NetBox, ADR 0010)."""
    settings = get_settings()
    http.post_json(
        f"{settings.orchestrator_url}/internal/collector-runs/{run_id}/references",
        json=batch.model_dump(),
        headers=_headers(via_agent),
    )


def submit_result(run_id: str, result: CollectorResult, via_agent: bool = False) -> dict | None:
    """Ergebnis abliefern; gibt die Quittung des Orchestrators zurueck (`status`, `message`)."""
    settings = get_settings()
    headers = _headers(via_agent)
    response = http.post_json(
        f"{settings.orchestrator_url}/internal/collector-runs/{run_id}/result",
        json=result.model_dump(),
        headers=headers,
    )
    try:
        return response.json()
    except ValueError:
        return None  # aeltere Orchestratoren antworten mit 204 ohne Inhalt


def confirmed(result: CollectorResult, ack: dict | None) -> CollectorResult:
    """Ergebnis mit dem Urteil des Orchestrators - z. B. failed, wenn das Einspielen in den
    Store scheitert, obwohl das Lesen geklappt hat. Fehlschlaege landen auch im Log."""
    if isinstance(ack, dict) and ack.get("status") == "failed" and result.status == "completed":
        result = result.model_copy(update={"status": "failed", "message": ack.get("message")})
    if result.status == "failed":
        run_id = ack.get("run_id") if isinstance(ack, dict) else None
        log.warning("Lauf %s fehlgeschlagen: %s", run_id or "?", result.message)
    return result
