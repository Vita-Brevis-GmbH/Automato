"""FortiGate-Collector-Task.

Liest die Regeln per FortiOS-REST-API (read-only), sobald `AUTOMATO_FORTIGATE_URL`
und `AUTOMATO_FORTIGATE_TOKEN` gesetzt sind - sonst Beispieldaten (Lab noch nicht da).
In beiden Faellen laeuft dieselbe Normalisierung inkl. Adress-/Service-Aufloesung.
Ergebnis geht per HTTP an die interne Orchestrator-API (kein DB-Zugriff).
"""

import json
from importlib import resources

from collectors.celery_app import app
from collectors.common.dto import CollectorResult
from collectors.common.upload import submit_result
from collectors.config import get_settings
from collectors.fortigate import normalize as norm
from collectors.fortigate.client import FortiGateClient


def _sample(name: str) -> list[dict]:
    return json.loads(resources.files("collectors.fortigate").joinpath(name).read_text())


def gather() -> tuple[list, list, list, list, list]:
    """(policies, addresses, address_groups, services, vips) - Lab oder Beispieldaten."""
    settings = get_settings()
    if settings.fortigate_url and settings.fortigate_token:
        client = FortiGateClient(
            settings.fortigate_url,
            settings.fortigate_token,
            verify_tls=settings.fortigate_verify_tls,
        )
        return client.gather()
    return (
        _sample("sample_policies.json"),
        _sample("sample_addresses.json"),
        _sample("sample_addrgrps.json"),
        _sample("sample_services.json"),
        _sample("sample_vips.json"),
    )


@app.task(name="collectors.fortigate.collect")
def collect(run_id: str) -> None:
    try:
        policies, addresses, groups, services, vips = gather()
        rules = norm.normalize(policies, addresses, groups, services, vips)
        result = CollectorResult(status="completed", rules=rules)
    except Exception as exc:  # noqa: BLE001 - Fehler gehen als failed-Result zurueck
        result = CollectorResult(status="failed", message=str(exc))
    submit_result(run_id, result)
