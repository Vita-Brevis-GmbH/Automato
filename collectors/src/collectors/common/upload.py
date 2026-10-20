"""Ergebnis-Upload an die interne Orchestrator-API (einziger Rueckkanal).

Der zentrale Worker meldet mit dem internen Token; ein Agent (Pull-Modell) mit seinem
eigenen Agent-Token - der Orchestrator nimmt es nur fuer Laeufe an, die genau dieser Agent
abgeholt hat (ADR 0009). Agenten brauchen das interne Token damit nicht mehr.
"""

from collectors.common import http
from collectors.common.dto import CollectorResult
from collectors.config import get_settings


def submit_result(run_id: str, result: CollectorResult, via_agent: bool = False) -> dict | None:
    """Ergebnis abliefern; gibt die Quittung des Orchestrators zurueck (`status`, `message`)."""
    settings = get_settings()
    if via_agent:
        headers = {"X-Agent-Token": settings.agent_token}
    else:
        headers = {"X-Internal-Token": settings.internal_api_token}
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
    Store scheitert, obwohl das Lesen geklappt hat."""
    if isinstance(ack, dict) and ack.get("status") == "failed" and result.status == "completed":
        return result.model_copy(update={"status": "failed", "message": ack.get("message")})
    return result
