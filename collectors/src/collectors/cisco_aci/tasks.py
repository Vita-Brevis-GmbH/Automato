"""Cisco-ACI-Collector-Task (Model 1, read-only).

Liest einen APIC-MO-Export (Tenant-Subtree) und bildet Contracts auf Regeln ab. Ohne
Lab liest der Collector die Beispieldatei; der echte Abruf (APIC REST: Login +
`/api/mo/uni/tn-<TE>.json?query-target=subtree`) folgt mit Lab-Creds (analog FortiGate).
"""

import json
from importlib import resources

from collectors.celery_app import app
from collectors.cisco_aci.mo import parse_epgs, parse_mo_export
from collectors.cisco_aci.normalize import normalize_contract
from collectors.common.dto import CollectorResult, NormalizedEpg
from collectors.common.upload import submit_result


def fetch_contracts() -> list[dict]:
    """Legacy-Beispiel (bereits vereinfachte Contract-Dicts) - Rueckwaertskompatibilitaet."""
    sample = resources.files("collectors.cisco_aci").joinpath("sample_contracts.json")
    return json.loads(sample.read_text())


def fetch_mo_export() -> dict:
    """APIC-MO-Export (Beispiel bis Lab-Creds); spaeter echter APIC-REST-Abruf."""
    sample = resources.files("collectors.cisco_aci").joinpath("sample_apic_export.json")
    return json.loads(sample.read_text())


def collect_once(run_id: str) -> CollectorResult:
    """Eine Erfassung ohne Celery (Worker *und* externer Standalone-Collector)."""
    try:
        export = fetch_mo_export()
        rules = [rule for c in parse_mo_export(export) for rule in normalize_contract(c)]
        epgs = [NormalizedEpg(**e) for e in parse_epgs(export)]
        result = CollectorResult(status="completed", rules=rules, epgs=epgs)
    except Exception as exc:  # noqa: BLE001 - Fehler sichtbar an die interne API melden
        result = CollectorResult(status="failed", message=str(exc))
    submit_result(run_id, result)
    return result


@app.task(name="collectors.cisco_aci.collect")
def collect(run_id: str) -> None:
    collect_once(run_id)
