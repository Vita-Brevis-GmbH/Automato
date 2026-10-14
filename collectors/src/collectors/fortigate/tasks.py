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


def gather() -> dict[str, list]:
    """FortiGate-Rohdaten (IPv4+IPv6, NAT) - Lab oder Beispieldaten."""
    settings = get_settings()
    if settings.fortigate_url and settings.fortigate_token:
        client = FortiGateClient(
            settings.fortigate_url,
            settings.fortigate_token,
            verify_tls=settings.fortigate_verify_tls,
        )
        return client.gather()
    return {
        "policies": _sample("sample_policies.json"),
        "addresses": _sample("sample_addresses.json"),
        "addresses6": _sample("sample_addresses6.json"),
        "groups": _sample("sample_addrgrps.json"),
        "groups6": _sample("sample_addrgrps6.json"),
        "services": _sample("sample_services.json"),
        "vips": _sample("sample_vips.json"),
        "ippools": _sample("sample_ippools.json"),
    }


def collect_once(run_id: str) -> CollectorResult:
    """Eine Erfassung ohne Celery: gather -> normalize -> submit (HTTPS). Fuer den
    Celery-Worker *und* den externen Standalone-Collector (collectors.run)."""
    try:
        d = gather()
        rules = norm.normalize(
            d["policies"],
            d["addresses"],
            d["groups"],
            d["services"],
            d["vips"],
            d["addresses6"],
            d["groups6"],
        )
        nat_rules = norm.normalize_nat(d["vips"]) + norm.normalize_snat(d["ippools"])
        objects = norm.build_object_catalog(d["addresses"], d["addresses6"])
        result = CollectorResult(
            status="completed", rules=rules, nat_rules=nat_rules, objects=objects
        )
    except Exception as exc:  # noqa: BLE001 - Fehler gehen als failed-Result zurueck
        result = CollectorResult(status="failed", message=str(exc))
    submit_result(run_id, result)
    return result


@app.task(name="collectors.fortigate.collect")
def collect(run_id: str) -> None:
    collect_once(run_id)
