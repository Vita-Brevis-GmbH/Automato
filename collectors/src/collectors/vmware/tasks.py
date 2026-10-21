"""VMware-vCenter-Collector-Task (ADR 0017, read-only).

Liest VMs, Hosts, Cluster und Portgruppen per pyVmomi. Zugangsdaten vom Orchestrator fuer das
Geraet des Laufs (`Target`: Adresse, Benutzer/Passwort; ADR 0009). Ohne Zugangsdaten liest
nur der zentrale Worker (Lab) die Beispieldaten - ein Agent im Kundennetz nie.
"""

import json
from importlib import resources

from collectors.celery_app import app
from collectors.common import agent as agent_api
from collectors.common import diagnose
from collectors.common.dto import CollectorResult, Target
from collectors.common.upload import confirmed, submit_result
from collectors.config import get_settings
from collectors.vmware.client import VCenterClient
from collectors.vmware.normalize import normalize


def _sample() -> dict:
    return json.loads(
        resources.files("collectors.vmware").joinpath("sample_vcenter.json").read_text()
    )


def _client(target: Target | None) -> VCenterClient | None:
    if target is not None and target.base_url and target.username and target.password:
        return VCenterClient(
            target.base_url, target.username, target.password, verify_tls=target.verify_tls
        )
    return None


def _no_credentials(target: Target | None) -> str:
    name = f"„{target.name}“" if target is not None and target.name else "das vCenter"
    return f"keine Zugangsdaten fuer {name} - im Web-UI beim Quellsystem hinterlegen"


def gather(target: Target | None = None) -> dict:
    """vCenter-Objekte als einfache Dicts - echtes vCenter oder (nur zentral) Beispieldaten."""
    client = _client(target)
    if client is not None:
        with client:
            return client.gather()
    if get_settings().agent_token:
        raise RuntimeError(_no_credentials(target))
    return _sample()


def check(target: Target | None = None) -> tuple[bool, str]:
    """Anmeldung am vCenter pruefen (read-only, kein Ingest)."""
    client = _client(target)
    if client is None:
        return False, _no_credentials(target) if target else "keine vCenter-Creds"
    try:
        with client:
            now = client.ping()
    except Exception as exc:  # noqa: BLE001 - Diagnose fuer den Operator
        return False, diagnose.device_error(exc, "vmware", target, local=True)
    return True, f"erreichbar: {client.host} (Zeit {now})"


def collect_once(
    run_id: str, target: Target | None = None, via_agent: bool = False
) -> CollectorResult:
    """Eine Erfassung ohne Celery (Worker *und* externer Standalone-Collector)."""
    try:
        result = CollectorResult(status="completed", virtual=normalize(gather(target)))
    except Exception as exc:  # noqa: BLE001 - Fehler sichtbar an die interne API melden
        result = CollectorResult(status="failed", message=diagnose.device_error(exc, "vmware"))
    return confirmed(result, submit_result(run_id, result, via_agent=via_agent))


@app.task(name="collectors.vmware.collect")
def collect(run_id: str) -> None:
    collect_once(run_id, agent_api.run_target(run_id))
