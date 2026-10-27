"""NSX-ALB-(Avi-)Collector-Task (ADR 0027, read-only).

Liest Virtual Services, VS VIPs, Pools, Pool Groups, VRF Contexts und Application Profiles je
Avi-Tenant und bildet sie als publizierte Dienste ab (NAT wie der Virtual Server der FortiGate).
Zugangsdaten vom Orchestrator fuer das Geraet des Laufs (`Target`: Adresse, Benutzer/Passwort,
Tenants in `read_scope`; ADR 0009). Ohne Zugangsdaten liest nur der zentrale Worker (Lab) die
Beispieldaten - ein Agent im Kundennetz nie.
"""

import json
from importlib import resources

from collectors.avi.client import AviClient
from collectors.avi.normalize import normalize
from collectors.celery_app import app
from collectors.common import agent as agent_api
from collectors.common import diagnose
from collectors.common.dto import CollectorResult, Target
from collectors.common.upload import confirmed, submit_result
from collectors.config import get_settings


def _sample() -> dict:
    return json.loads(resources.files("collectors.avi").joinpath("sample_avi.json").read_text())


def _client(target: Target | None) -> AviClient | None:
    if target is not None and target.base_url and target.username and target.password:
        return AviClient(
            target.base_url, target.username, target.password, verify_tls=target.verify_tls
        )
    return None


def _no_credentials(target: Target | None) -> str:
    name = f"„{target.name}“" if target is not None and target.name else "den Avi-Controller"
    return f"keine Zugangsdaten fuer {name} - im Web-UI beim Quellsystem hinterlegen"


def gather(target: Target | None = None) -> dict:
    """Avi-Objekte je Tenant - echter Controller oder (nur zentral) Beispieldaten."""
    client = _client(target)
    if client is not None:
        with client:
            return client.gather(target.read_scope if target else "")
    if get_settings().agent_token:
        raise RuntimeError(_no_credentials(target))
    return _sample()


def check(target: Target | None = None) -> tuple[bool, str]:
    """Anmeldung und Leserecht pruefen (read-only, kein Ingest)."""
    client = _client(target)
    if client is None:
        return False, _no_credentials(target) if target else "keine Avi-Creds"
    try:
        with client:
            count = client.ping()
    except Exception as exc:  # noqa: BLE001 - Diagnose fuer den Operator
        return False, diagnose.device_error(exc, "avi", target, local=True)
    return True, f"erreichbar: Avi {client.version}, {count} Virtual Services sichtbar"


def collect_once(
    run_id: str, target: Target | None = None, via_agent: bool = False
) -> CollectorResult:
    """Eine Erfassung ohne Celery (Worker *und* externer Standalone-Collector)."""
    try:
        data = gather(target)
        missing = data.get("missing") or []
        result = CollectorResult(
            status="completed",
            nat_rules=normalize(data),
            message=f"nicht lesbar: {'; '.join(missing[:20])}" if missing else None,
        )
    except Exception as exc:  # noqa: BLE001 - Fehler sichtbar an die interne API melden
        result = CollectorResult(status="failed", message=diagnose.device_error(exc, "avi"))
    return confirmed(result, submit_result(run_id, result, via_agent=via_agent))


@app.task(name="collectors.avi.collect")
def collect(run_id: str) -> None:
    collect_once(run_id, agent_api.run_target(run_id))
