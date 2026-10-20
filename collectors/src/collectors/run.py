"""Standalone-Collector-Runner — externer Agent (z. B. auf Windows), ADR 0008/0009.

Fuehrt **eine** Erfassung ohne Celery/Redis aus: gather → normalize → submit (HTTPS) an
die interne Orchestrator-API. Gedacht fuer Umgebungen, die nur der externe Collector
erreicht (Kundennetz); die zentrale Plattform braucht keinen Zugriff aufs Zielsystem –
nur der Agent spricht ausgehend HTTPS zum Orchestrator.

Konfiguration ueber `AUTOMATO_*`-Env (collectors.config):
  AUTOMATO_ORCHESTRATOR_URL     Basis-URL des Orchestrators (intern erreichbar)
  AUTOMATO_AGENT_TOKEN          Agent-Token (Pull-Modell): Laeufe abholen + Ergebnis abliefern
  AUTOMATO_FORTIGATE_URL/_TOKEN  nur noch Rueckfall - die Zugangsdaten der Geraete kommen
                                vom Orchestrator (im Web-UI beim Quellsystem erfasst, ADR 0009)

Aufruf:
  automato-collector --poll        naechsten Lauf eines zugewiesenen Geraets holen + ausfuehren
  automato-collector --check       alle zugewiesenen Geraete read-only pruefen
  automato-collector --platform fortigate --run-id <RUN_ID>   (zentral/alt, internes Token)

Exit 0 = Ergebnis completed bzw. alle Checks OK, 1 = failed, 2 = Check-Fehler.
"""

import argparse
import sys
from collections.abc import Callable, Sequence

from collectors.cisco_aci.tasks import check as _aci_check
from collectors.cisco_aci.tasks import collect_once as _aci_collect
from collectors.common import agent as agent_api
from collectors.common.dto import CollectorResult, Target
from collectors.config import get_settings
from collectors.fortigate.tasks import check as _fgt_check
from collectors.fortigate.tasks import collect_once as _fgt_collect

RUNNERS: dict[str, Callable[..., CollectorResult]] = {
    "fortigate": _fgt_collect,
    "cisco_aci": _aci_collect,
}
CHECKERS: dict[str, Callable[..., tuple[bool, str]]] = {
    "fortigate": _fgt_check,
    "cisco_aci": _aci_check,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="automato-collector",
        description="Externer Standalone-Collector (eine Erfassung, read-only).",
    )
    parser.add_argument(
        "--platform", choices=sorted(RUNNERS), help="Plattform (nur fuer --run-id Pflicht)"
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
        help="Erreichbarkeit/Auth der zugewiesenen Geraete pruefen (read-only, kein Ingest)",
    )
    args = parser.parse_args(argv)

    if args.check:
        return _check(args.platform)

    target: Target | None = None
    via_agent = False
    if args.poll:
        run = agent_api.next_run(args.platform)  # --platform optional als Filter
        if not run:
            print("keine offenen Laeufe")
            return 0
        platform, run_id = run["platform"], run["run_id"]
        target = Target(**run["target"]) if run.get("target") else None
        via_agent = True
    else:
        if not (args.platform and args.run_id):
            parser.error("--platform und --run-id erforderlich (oder --poll)")
        platform, run_id = args.platform, args.run_id

    result = RUNNERS[platform](run_id, target, via_agent=via_agent)
    label = f" ({target.name})" if target and target.name else ""
    print(f"{platform} run {run_id}{label}: {result.status}")
    if result.message:
        print(result.message, file=sys.stderr)
    return 0 if result.status == "completed" else 1


def _check(platform: str | None) -> int:
    """Agent: alle zugewiesenen Geraete pruefen; ohne Agent-Token: lokale Konfiguration."""
    if get_settings().agent_token:
        devices = [t for t in agent_api.targets() if not platform or t.platform == platform]
        if devices:
            failed = 0
            for t in devices:
                ok, msg = CHECKERS[t.platform](t)
                failed += 0 if ok else 1
                print(f"{t.name} ({t.platform}) check: {'OK' if ok else 'FEHLER'} - {msg}")
            return 0 if failed == 0 else 2
        print("diesem Agenten sind keine Geraete zugewiesen (Web-UI: Mandanten & Standorte ->")
        print("Quellsystem -> Agent); pruefe lokale Konfiguration")
    plat = platform or "fortigate"
    ok, msg = CHECKERS[plat](None)
    print(f"{plat} check: {'OK' if ok else 'FEHLER'} - {msg}")
    return 0 if ok else 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
