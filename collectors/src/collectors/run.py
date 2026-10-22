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
  automato-collector --poll --watch 20 --for 14
                                wach bleiben: alle 20 s nach neuen Laeufen fragen, nach 14 min
                                beenden (die Aufgabenplanung startet neu, inkl. Selbst-Update)
  automato-collector --check       alle zugewiesenen Geraete read-only pruefen
  automato-collector --check --strict  zusaetzlich: lehnt die FortiGate ein ungueltiges Token
                                ab? (Risiko R16; zaehlt dort als ein Fehlversuch)
  automato-collector --platform fortigate --run-id <RUN_ID>   (zentral/alt, internes Token)

Exit 0 = Ergebnis completed bzw. alle Checks OK, 1 = failed, 2 = Check-Fehler.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Sequence

import httpx

# Selbst-Update zuerst und ohne weitere Abhaengigkeiten laden: so kann sich auch eine
# unvollstaendige Installation (z. B. fehlendes sot_gateway) selbst reparieren (ADR 0011).
from collectors.common import agentlog, http, selfupdate
from collectors.config import get_settings

try:
    from collectors.cisco_aci.tasks import check as _aci_check
    from collectors.cisco_aci.tasks import collect_once as _aci_collect
    from collectors.common import agent as agent_api
    from collectors.common.dto import CollectorResult, Target
    from collectors.common.upload import confirmed, submit_result
    from collectors.fortigate.tasks import check as _fgt_check
    from collectors.fortigate.tasks import collect_once as _fgt_collect
    from collectors.fortigate.tasks import strict_check as _fgt_strict
    from collectors.ipam import tasks as _ipam
    from collectors.vmware import tasks as _vmware

    RUNNERS: dict[str, Callable[..., CollectorResult]] = {
        "fortigate": _fgt_collect,
        "cisco_aci": _aci_collect,
        "infoblox": _ipam.collect_infoblox,
        "netbox": _ipam.collect_netbox,
        "vmware": _vmware.collect_once,
    }
    CHECKERS: dict[str, Callable[..., tuple[bool, str]]] = {
        "fortigate": _fgt_check,
        "cisco_aci": _aci_check,
        "infoblox": _ipam.check_infoblox,
        "netbox": _ipam.check_netbox,
        "vmware": _vmware.check,
    }
    # --check --strict: Negativtest je Plattform (nur wo sinnvoll und vertretbar)
    STRICT_CHECKERS: dict[str, Callable[..., tuple[bool, str]]] = {"fortigate": _fgt_strict}
    _BROKEN: ImportError | None = None
except ImportError as _exc:  # unvollstaendige Installation
    RUNNERS, CHECKERS, STRICT_CHECKERS = {}, {}, {}
    _BROKEN = _exc


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
        "--watch",
        type=int,
        default=0,
        metavar="SEKUNDEN",
        help="mit --poll: wach bleiben und alle N Sekunden nach neuen Laeufen fragen",
    )
    parser.add_argument(
        "--for",
        dest="duration",
        type=float,
        default=14,
        metavar="MINUTEN",
        help="mit --watch: nach so vielen Minuten beenden (Standard 14)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Erreichbarkeit/Auth der zugewiesenen Geraete pruefen (read-only, kein Ingest)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "mit --check: pruefen, ob die FortiGate ein ungueltiges Token ablehnt (R16); "
            "zaehlt dort als fehlgeschlagene Anmeldung"
        ),
    )
    parser.add_argument(
        "--no-update",
        action="store_true",
        help="Selbst-Update (ADR 0011) fuer diesen Aufruf auslassen",
    )
    parser.add_argument("--version", action="store_true", help="Agent-Version anzeigen")
    raw = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(raw)
    if args.version:
        print(f"automato-collector {selfupdate.current_label()}")
        return 0
    if argv is None:  # echter Aufruf (nicht aus Tests): Ausgaben auch in die Log-Datei
        agentlog.setup(selfupdate.install_dir())
    if (args.poll or args.check) and not args.no_update:
        # neue Version fuehrt den Aufruf selbst aus; bei kaputter Installation neu installieren
        updated = selfupdate.maybe_update(raw, force=_BROKEN is not None)
        if updated is not None:
            return updated
    if _BROKEN is not None:
        print(
            f"Agent-Installation unvollstaendig ({_BROKEN}) - Agent-Paket im Web-UI laden und"
            " install.ps1 erneut ausfuehren",
            file=sys.stderr,
        )
        return 1
    try:
        return _run(parser, args)
    except httpx.HTTPError as exc:
        # Verbindungs-/HTTP-Fehler zum Orchestrator: klare Meldung statt Traceback
        print(f"Orchestrator-Aufruf fehlgeschlagen: {_describe(exc)}", file=sys.stderr)
        return 1


def _describe(exc: httpx.HTTPError) -> str:
    text = http.describe_error(exc)
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500:
        text += " (Details: docker compose logs orchestrator)"
    return text


MAX_RUNS_PER_POLL = 25  # Schutz gegen Endlosschleifen; der naechste Aufruf holt den Rest


def _updater(args: argparse.Namespace) -> Callable[[float], int | None]:
    """Selbst-Update im Wach-Modus: die neue Version fuehrt die restliche Wachzeit aus."""

    def update(minutes_left: float) -> int | None:
        rest = ["--poll", "--watch", str(args.watch), "--for", f"{minutes_left:.1f}"]
        rest += ["--platform", args.platform] if args.platform else []
        return selfupdate.maybe_update(rest)

    return update


def _run(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    if args.check:
        return _check(args.platform, strict=args.strict)
    if args.strict:
        parser.error("--strict nur zusammen mit --check")
    if args.watch and not args.poll:
        parser.error("--watch nur zusammen mit --poll")

    if not args.poll:
        if not (args.platform and args.run_id):
            parser.error("--platform und --run-id erforderlich (oder --poll)")
        return 0 if _execute(args.platform, args.run_id, None, via_agent=False) else 1

    if args.watch:
        update = None if args.no_update else _updater(args)
        return _watch(args.platform, args.watch, args.duration, update=update)
    ok, done = _poll_once(args.platform)
    if done == 0:
        print("keine offenen Laeufe")
    return 0 if ok else 1


MIN_WATCH_SECONDS = 5
UPDATE_EVERY_SECONDS = 300  # im Wach-Modus so oft auf eine neue Agent-Version pruefen


def _poll_once(platform: str | None) -> tuple[bool, int]:
    """Pull-Modell: alle offenen Laeufe der zugewiesenen Geraete nacheinander abarbeiten."""
    ok, done = True, 0
    while done < MAX_RUNS_PER_POLL:
        run = agent_api.next_run(platform)  # --platform optional als Filter
        if not run:
            break
        target = Target(**run["target"]) if run.get("target") else None
        ok = _execute(run["platform"], run["run_id"], target, via_agent=True) and ok
        done += 1
    return ok, done


def _watch(
    platform: str | None,
    every: int,
    minutes: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    update: Callable[[float], int | None] | None = None,
) -> int:
    """Wach bleiben: alle `every` Sekunden nach Laeufen fragen, nach `minutes` beenden.

    Ein "Jetzt einlesen" im Web-UI wird so nach Sekunden statt erst beim naechsten Start der
    geplanten Aufgabe abgeholt. Kurz nicht erreichbarer Orchestrator beendet die Schleife
    nicht; die Aufgabenplanung startet den Agenten danach neu (inkl. Selbst-Update)."""
    every = max(MIN_WATCH_SECONDS, every)
    deadline = clock() + minutes * 60
    ok, total, errors = True, 0, 0
    checked = clock()
    print(f"Agent wartet auf Laeufe (alle {every} s, {minutes:g} min)")
    while True:
        # neue Version uebernimmt die restliche Wachzeit (Dev-Befund 0.11.4: bisher pruefte
        # der Agent nur beim Start und lief im Wach-Modus mit der alten Version); haelt der
        # Orchestrator Laeufe fuer das Update zurueck, sofort (0.16.0)
        due = clock() - checked >= UPDATE_EVERY_SECONDS or agent_api.update_requested()
        if update is not None and due:
            checked = clock()
            left = (deadline - checked) / 60
            if left > every / 60:
                updated = update(left)
                if updated is not None:
                    return updated
        try:
            run_ok, done = _poll_once(platform)
            ok = run_ok and ok
            total += done
            errors = 0
        except httpx.HTTPError as exc:
            errors += 1
            if errors == 1:  # nicht alle paar Sekunden dieselbe Meldung
                print(f"Orchestrator-Aufruf fehlgeschlagen: {_describe(exc)}", file=sys.stderr)
        if clock() + every > deadline:
            break
        sleep(every)
    print(f"{total} Lauf/Laeufe ausgefuehrt" if total else "keine offenen Laeufe")
    return 0 if ok and errors == 0 else 1


def _unsupported(platform: str) -> str:
    return (
        f"Plattform '{platform}' wird von diesem Agenten nicht unterstuetzt - Agent-Paket im"
        " Web-UI (Collector-Agenten) neu herunterladen und install.ps1 erneut ausfuehren"
    )


def _execute(platform: str, run_id: str, target: Target | None, via_agent: bool) -> bool:
    runner = RUNNERS.get(platform)
    if runner is None:  # aelterer Agent als der Orchestrator: Lauf sauber als failed melden
        result = CollectorResult(status="failed", message=_unsupported(platform))
        result = confirmed(result, submit_result(run_id, result, via_agent=via_agent))
    else:
        try:
            result = runner(run_id, target, via_agent=via_agent)
        except httpx.HTTPError as exc:
            # Ergebnis nicht abgeliefert (z. B. zu gross, Zeitueberschreitung): den Lauf mit
            # kurzer Meldung beenden statt ihn offen zu lassen - ein offener Lauf blockierte
            # "Alle einlesen" und den Zeitplan (Dev-Befund 0.11.3)
            result = CollectorResult(
                status="failed", message=f"Ergebnis-Upload fehlgeschlagen: {_describe(exc)}"
            )
            try:
                result = confirmed(result, submit_result(run_id, result, via_agent=via_agent))
            except httpx.HTTPError:
                raise exc from None  # Orchestrator ganz weg: der erste Fehler sagt mehr
    label = f" ({target.name})" if target and target.name else ""
    print(f"{platform} run {run_id}{label}: {result.status}")
    if result.message:
        print(result.message, file=sys.stderr)
    return result.status == "completed"


def _strict(platform: str, target: Target | None, label: str) -> bool:
    """Negativtest nach erfolgreichem Check (nur `--strict`); True = bestanden/entfaellt."""
    checker = STRICT_CHECKERS.get(platform)
    if checker is None:
        return True
    ok, msg = checker(target)
    print(f"{label} strict: {'OK' if ok else 'FEHLER'} - {msg}")
    return ok


def _check(platform: str | None, strict: bool = False) -> int:
    """Agent: alle zugewiesenen Geraete pruefen; ohne Agent-Token: lokale Konfiguration."""
    print(f"Agent-Version: {selfupdate.current_label()}")
    if get_settings().agent_token:
        devices = [t for t in agent_api.targets() if not platform or t.platform == platform]
        if devices:
            failed = 0
            for t in devices:
                checker = CHECKERS.get(t.platform)
                ok, msg = checker(t) if checker else (False, _unsupported(t.platform))
                label = f"{t.name} ({t.platform})"
                print(f"{label} check: {'OK' if ok else 'FEHLER'} - {msg}")
                if ok and strict:
                    ok = _strict(t.platform, t, label)
                failed += 0 if ok else 1
            return 0 if failed == 0 else 2
        print("diesem Agenten sind keine Geraete zugewiesen (Web-UI: Mandanten & Standorte ->")
        print("Quellsystem -> Agent); pruefe lokale Konfiguration")
    plat = platform or "fortigate"
    ok, msg = CHECKERS[plat](None)
    print(f"{plat} check: {'OK' if ok else 'FEHLER'} - {msg}")
    if ok and strict:
        ok = _strict(plat, None, plat)
    return 0 if ok else 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
