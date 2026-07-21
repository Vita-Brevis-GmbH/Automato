"""FortiGate-Collector-Task.

GRUNDAUFBAU: Liest Beispieldaten (sample_policies.json) statt einer echten
FortiGate - der Kreislauf Orchestrator -> Queue -> Collector -> Ergebnis-Upload
ist damit komplett testbar. Der echte REST-Abruf (FortiOS API, read-only
Token) kommt im Durchstich (M7), sobald das Lab steht.
"""

import json
from importlib import resources

from collectors.celery_app import app
from collectors.common.dto import CollectorResult
from collectors.common.upload import submit_result
from collectors.fortigate.normalize import normalize_policy


def fetch_policies() -> list[dict]:
    # TODO(M7): echte FortiOS-REST-Abfrage (GET /api/v2/cmdb/firewall/policy)
    sample = resources.files("collectors.fortigate").joinpath("sample_policies.json")
    return json.loads(sample.read_text())


@app.task(name="collectors.fortigate.collect")
def collect(run_id: str) -> None:
    try:
        rules = [normalize_policy(p) for p in fetch_policies()]
        result = CollectorResult(status="completed", rules=rules)
    except Exception as exc:  # noqa: BLE001 - Fehler gehen als failed-Result zurueck
        result = CollectorResult(status="failed", message=str(exc))
    submit_result(run_id, result)
