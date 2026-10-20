"""Agent-Pull-API-Client (ADR 0008): naechsten externen Lauf holen / Heartbeat.

Nur fuer den externen Standalone-Agenten. Authentisiert sich mit `AUTOMATO_AGENT_TOKEN`
gegen `/internal/agent/*`; ausgehend HTTPS, kein DB-Zugriff.
"""

import logging

from collectors.common import http
from collectors.common.dto import Target
from collectors.common.selfupdate import agent_headers
from collectors.config import get_settings

log = logging.getLogger(__name__)


def _headers() -> dict[str, str]:
    return agent_headers()  # Agent-Token + Version (ADR 0011)


def next_run(platform: str | None = None) -> dict | None:
    """Holt den naechsten offenen externen Lauf (oder None, wenn keiner ansteht)."""
    settings = get_settings()
    params = {"platform": platform} if platform else None
    response = http.get_json(
        f"{settings.orchestrator_url}/internal/agent/next-run", params, _headers()
    )
    return response.json().get("run")


def heartbeat() -> None:
    settings = get_settings()
    http.post(f"{settings.orchestrator_url}/internal/agent/heartbeat", _headers())


def targets() -> list[Target]:
    """Diesem Agenten zugewiesene Geraete inkl. Zugangsdaten (fuer --check)."""
    settings = get_settings()
    response = http.get_json(
        f"{settings.orchestrator_url}/internal/agent/targets", None, _headers()
    )
    return [Target(**t) for t in response.json()]


def run_target(run_id: str) -> Target | None:
    """Ziel eines zentralen Laufs (zentraler Worker, internes Token); Fehler -> None."""
    settings = get_settings()
    try:
        response = http.get_json(
            f"{settings.orchestrator_url}/internal/collector-runs/{run_id}/target",
            None,
            {"X-Internal-Token": settings.internal_api_token},
        )
    except Exception as exc:  # noqa: BLE001 - dann gilt die lokale Konfiguration
        log.warning("Ziel fuer Lauf %s nicht abrufbar: %s", run_id, exc)
        return None
    data = response.json().get("target")
    return Target(**data) if data else None
