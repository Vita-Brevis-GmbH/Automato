"""Ergebnis-Upload an die interne Orchestrator-API (einziger Rueckkanal)."""

import httpx

from collectors.common.dto import CollectorResult
from collectors.config import get_settings


def submit_result(run_id: str, result: CollectorResult) -> None:
    settings = get_settings()
    response = httpx.post(
        f"{settings.orchestrator_url}/internal/collector-runs/{run_id}/result",
        json=result.model_dump(),
        headers={"X-Internal-Token": settings.internal_api_token},
        timeout=30.0,
    )
    response.raise_for_status()
