"""FortiGate-Collector-Task.

Liest die Regeln per FortiOS-REST-API (read-only). Zugangsdaten in dieser Reihenfolge:
1. vom Orchestrator fuer das Geraet des Laufs (`Target`, ADR 0009),
2. lokal `AUTOMATO_FORTIGATE_URL`/`_TOKEN` (Rueckfall, z. B. alte agent.env),
3. nur zentral/Lab: Beispieldaten. Ein Agent im Kundennetz liest **nie** Beispieldaten -
   ohne Zugangsdaten schlaegt der Lauf mit klarer Meldung fehl.
Ergebnis geht per HTTP an die interne Orchestrator-API (kein DB-Zugriff).
"""

import json
from importlib import resources

from collectors.celery_app import app
from collectors.common import agent as agent_api
from collectors.common import diagnose
from collectors.common.dto import CollectorResult, Target
from collectors.common.upload import confirmed, submit_result
from collectors.config import get_settings
from collectors.fortigate import normalize as norm
from collectors.fortigate.client import FortiGateClient


def _sample(name: str) -> list | dict:
    return json.loads(resources.files("collectors.fortigate").joinpath(name).read_text())


def _credentials(target: Target | None) -> tuple[str, str, bool] | None:
    """(URL, Token, verify_tls) - vom Geraet (Orchestrator) vor lokaler Konfiguration."""
    if target is not None and target.base_url and target.token:
        return target.base_url, target.token, target.verify_tls
    settings = get_settings()
    if settings.fortigate_url and settings.fortigate_token:
        return settings.fortigate_url, settings.fortigate_token, settings.fortigate_verify_tls
    return None


def _no_credentials(target: Target | None) -> str:
    name = f"„{target.name}“" if target is not None and target.name else "das Geraet"
    return f"keine Zugangsdaten fuer {name} - im Web-UI beim Quellsystem hinterlegen"


def gather(target: Target | None = None) -> dict[str, list]:
    """FortiGate-Rohdaten (IPv4+IPv6, NAT) - echtes Geraet oder (nur zentral) Beispieldaten."""
    creds = _credentials(target)
    if creds is not None:
        url, token, verify = creds
        return FortiGateClient(url, token, verify_tls=verify).gather()
    if get_settings().agent_token:
        # Agent im Kundennetz: nie Beispieldaten in einen echten Mandanten schreiben
        raise RuntimeError(_no_credentials(target))
    return {
        "policies": _sample("sample_policies.json"),
        "addresses": _sample("sample_addresses.json"),
        "addresses6": _sample("sample_addresses6.json"),
        "groups": _sample("sample_addrgrps.json"),
        "groups6": _sample("sample_addrgrps6.json"),
        "services": _sample("sample_services.json"),
        "service_groups": _sample("sample_service_groups.json"),
        "vips": _sample("sample_vips.json"),
        "ippools": _sample("sample_ippools.json"),
        "interfaces": _sample("sample_interfaces.json"),
        "zones": _sample("sample_zones.json"),
        "system": _sample("sample_system.json"),
    }


def check(target: Target | None = None) -> tuple[bool, str]:
    """Erreichbarkeit/Auth des Zielsystems pruefen (read-only, kein Ingest)."""
    creds = _credentials(target)
    if creds is None:
        return False, _no_credentials(target) if target else "keine FortiGate-Creds"
    url, token, verify = creds
    try:
        FortiGateClient(url, token, verify_tls=verify).ping()
    except Exception as exc:  # noqa: BLE001 - Diagnose fuer den Operator
        return False, diagnose.device_error(exc, "fortigate", target, local=True)
    return True, f"erreichbar: {url}"


def collect_once(
    run_id: str, target: Target | None = None, via_agent: bool = False
) -> CollectorResult:
    """Eine Erfassung ohne Celery: gather -> normalize -> submit (HTTPS). Fuer den
    Celery-Worker *und* den externen Standalone-Collector (collectors.run)."""
    try:
        d = gather(target)
        rules = norm.normalize(
            d["policies"],
            d["addresses"],
            d["groups"],
            d["services"],
            d["vips"],
            d["addresses6"],
            d["groups6"],
            d.get("service_groups", []),
        )
        nat_rules = norm.normalize_nat(d["vips"]) + norm.normalize_snat(d["ippools"])
        objects = norm.build_device_objects(
            d["addresses"],
            d["groups"],
            d["addresses6"],
            d["groups6"],
            d["vips"],
            d["ippools"],
            d["services"],
            d.get("service_groups", []),
        )
        inventory = norm.normalize_inventory(
            d.get("interfaces"),
            d.get("zones"),
            d.get("system"),
            target.name if target is not None and target.name else "fortigate",
        )
        result = CollectorResult(
            status="completed",
            rules=rules,
            nat_rules=nat_rules,
            objects=objects,
            inventory=inventory,
        )
    except Exception as exc:  # noqa: BLE001 - Fehler gehen als failed-Result zurueck
        result = CollectorResult(status="failed", message=diagnose.device_error(exc, "fortigate"))
    return confirmed(result, submit_result(run_id, result, via_agent=via_agent))


@app.task(name="collectors.fortigate.collect")
def collect(run_id: str) -> None:
    # Zentraler Worker: Zugangsdaten des Geraets vom Orchestrator (internes Token)
    collect_once(run_id, agent_api.run_target(run_id))
