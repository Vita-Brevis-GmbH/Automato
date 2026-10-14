"""Agent-Pull-API-Client (ADR 0008): naechsten externen Lauf holen / Heartbeat.

Nur fuer den externen Standalone-Agenten. Authentisiert sich mit `AUTOMATO_AGENT_TOKEN`
gegen `/internal/agent/*`; ausgehend HTTPS, kein DB-Zugriff.
"""

import httpx

from collectors.config import get_settings


def _headers() -> dict[str, str]:
    return {"X-Agent-Token": get_settings().agent_token}


def next_run(platform: str | None = None) -> dict | None:
    """Holt den naechsten offenen externen Lauf (oder None, wenn keiner ansteht)."""
    settings = get_settings()
    params = {"platform": platform} if platform else None
    response = httpx.get(
        f"{settings.orchestrator_url}/internal/agent/next-run",
        params=params,
        headers=_headers(),
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json().get("run")


def heartbeat() -> None:
    settings = get_settings()
    httpx.post(
        f"{settings.orchestrator_url}/internal/agent/heartbeat",
        headers=_headers(),
        timeout=15.0,
    ).raise_for_status()
