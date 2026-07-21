"""Cisco-ACI-Collector-Task.

GRUNDAUFBAU: Beispieldaten statt echtem APIC (analog FortiGate-Collector).
Der echte Abruf (APIC REST, Contracts/Filter/EPGs) kommt im Durchstich (M7).
"""

import json
from importlib import resources

from collectors.celery_app import app
from collectors.cisco_aci.normalize import normalize_contract
from collectors.common.dto import CollectorResult
from collectors.common.upload import submit_result


def fetch_contracts() -> list[dict]:
    # TODO(M7): echte APIC-Abfrage (Login + /api/node/class/vzBrCP.json u. a.)
    sample = resources.files("collectors.cisco_aci").joinpath("sample_contracts.json")
    return json.loads(sample.read_text())


@app.task(name="collectors.cisco_aci.collect")
def collect(run_id: str) -> None:
    try:
        rules = [rule for contract in fetch_contracts() for rule in normalize_contract(contract)]
        result = CollectorResult(status="completed", rules=rules)
    except Exception as exc:  # noqa: BLE001
        result = CollectorResult(status="failed", message=str(exc))
    submit_result(run_id, result)
