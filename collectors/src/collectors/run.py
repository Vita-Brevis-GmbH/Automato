"""Standalone-Collector-Runner — externer Agent (z. B. auf Windows), ADR 0008.

Fuehrt **eine** Erfassung ohne Celery/Redis aus: gather → normalize → submit (HTTPS) an
die interne Orchestrator-API. Gedacht fuer Umgebungen, die nur der externe Collector
erreicht (Kundennetz); die zentrale Plattform braucht keinen Zugriff aufs Zielsystem –
nur der Agent spricht ausgehend HTTPS zum Orchestrator.

Konfiguration ueber `AUTOMATO_*`-Env (collectors.config):
  AUTOMATO_ORCHESTRATOR_URL     Basis-URL des Orchestrators (intern erreichbar)
  AUTOMATO_INTERNAL_API_TOKEN   Shared Secret fuer /internal (nie im Repo)
  AUTOMATO_FORTIGATE_URL/_TOKEN  bzw. APIC-Creds (Zielsystem, read-only)

Aufruf:
  automato-collector --platform fortigate --run-id <RUN_ID>
  python -m collectors.run --platform cisco_aci --run-id <RUN_ID>

Der RUN_ID stammt aus einem zuvor (execution=external) angelegten Lauf am Orchestrator.
Exit 0 = Ergebnis completed, 1 = failed (Details stehen am Lauf/Audit-Log).
"""

import argparse
import sys
from collections.abc import Callable, Sequence

from collectors.cisco_aci.tasks import collect_once as _aci_collect
from collectors.common.dto import CollectorResult
from collectors.fortigate.tasks import collect_once as _fgt_collect

RUNNERS: dict[str, Callable[[str], CollectorResult]] = {
    "fortigate": _fgt_collect,
    "cisco_aci": _aci_collect,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="automato-collector",
        description="Externer Standalone-Collector (eine Erfassung, read-only).",
    )
    parser.add_argument("--platform", required=True, choices=sorted(RUNNERS))
    parser.add_argument("--run-id", required=True, help="RUN_ID eines externen Laufs")
    args = parser.parse_args(argv)

    result = RUNNERS[args.platform](args.run_id)
    print(f"{args.platform} run {args.run_id}: {result.status}")
    if result.message:
        print(result.message, file=sys.stderr)
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
