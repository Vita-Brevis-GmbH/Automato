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

from collectors.cisco_aci.tasks import check as _aci_check
from collectors.cisco_aci.tasks import collect_once as _aci_collect
from collectors.common import agent as agent_api
from collectors.common.dto import CollectorResult
from collectors.fortigate.tasks import check as _fgt_check
from collectors.fortigate.tasks import collect_once as _fgt_collect

RUNNERS: dict[str, Callable[[str], CollectorResult]] = {
    "fortigate": _fgt_collect,
    "cisco_aci": _aci_collect,
}
CHECKERS: dict[str, Callable[[], tuple[bool, str]]] = {
    "fortigate": _fgt_check,
    "cisco_aci": _aci_check,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="automato-collector",
        description="Externer Standalone-Collector (eine Erfassung, read-only).",
    )
    parser.add_argument(
        "--platform", choices=sorted(RUNNERS), help="Plattform (ohne --poll Pflicht)"
    )
    parser.add_argument("--run-id", help="RUN_ID eines externen Laufs (ohne --poll Pflicht)")
    parser.add_argument(
        "--poll",
        action="store_true",
        help="naechsten externen Lauf aus der Agent-Queue holen (Agent-Token noetig)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="nur Erreichbarkeit/Auth des Zielsystems pruefen (kein Ingest)",
    )
    args = parser.parse_args(argv)

    if args.check:
        if not args.platform:
            parser.error("--platform erforderlich fuer --check")
        ok, msg = CHECKERS[args.platform]()
        print(f"{args.platform} check: {'OK' if ok else 'FEHLER'} - {msg}")
        return 0 if ok else 2

    if args.poll:
        run = agent_api.next_run(args.platform)  # --platform optional als Filter
        if not run:
            print("keine offenen Laeufe")
            return 0
        platform, run_id = run["platform"], run["run_id"]
    else:
        if not (args.platform and args.run_id):
            parser.error("--platform und --run-id erforderlich (oder --poll)")
        platform, run_id = args.platform, args.run_id

    result = RUNNERS[platform](run_id)
    print(f"{platform} run {run_id}: {result.status}")
    if result.message:
        print(result.message, file=sys.stderr)
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
