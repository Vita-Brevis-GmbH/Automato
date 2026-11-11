"""Collector-Task fuer Cisco-Switches (ADR 0025, read-only).

Ein Quellsystem ist eine Gruppe von Switches bzw. Routern mit gemeinsamem Read-only-Konto: die
Adressen stehen im Lesebereich (`read_scope`, eine je Zeile bzw. kommagetrennt, `host[:port]`).
Je Geraet `show version` (erkennt IOS/IOS-XE, NX-OS, seit 0.68.0 Junos und die Business-CLI der
Catalyst 1200/1300 - diese ueber die begrenzte Sitzung -, seit 0.94.0 Arista EOS und Aruba CX),
danach die festen Befehle der Plattform (seit 0.94.0 auch die IPv6-Nachbarn) und - ADR 0032 -
das Routing (`routing.COMMANDS`: RIB, BGP; nur L3-Geraete bekommen Kontexte, ein Switch ohne
`ip routing` nur den ersten Befehl).
Ein Switch, der nicht antwortet, fehlt im Ergebnis und steht in der Meldung; antwortet keiner,
schlaegt der Lauf fehl. Ohne Zugangsdaten liest nur der zentrale Worker im Labor die
Beispieldaten (`samples_allowed`, 1.6.0).

Zugang „SNMPv3“ (ADR 0033, `Target.access == "snmp"`): dieselben Geraete und dasselbe Ergebnis,
gelesen mit SNMPv3 authPriv statt SSH (`collectors/snmp/`).
"""

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from importlib import resources

from collectors.celery_app import app
from collectors.cisco_switch import routing
from collectors.cisco_switch.client import COMMANDS, SwitchClient, fingerprint
from collectors.cisco_switch.normalize import (
    PLATFORM_LABEL,
    cli_error,
    detect_platform,
    normalize,
    quiet,
)
from collectors.common import agent as agent_api
from collectors.common import diagnose
from collectors.common.dto import CollectorResult, Target
from collectors.common.i18n import L, carry, german_only
from collectors.common.upload import confirmed, submit_result
from collectors.config import samples_allowed

PARALLEL = 8  # Switches gleichzeitig


def hosts(read_scope: str) -> list[str]:
    """Adressen aus dem Lesebereich (Zeilen, Kommas bzw. Leerzeichen), ohne Doppelte."""
    return list(dict.fromkeys(h for h in re.split(r"[\s,;]+", read_scope or "") if h))


def _sample() -> dict:
    return json.loads(
        resources.files("collectors.cisco_switch").joinpath("sample_switches.json").read_text()
    )


def _no_credentials(target: Target | None) -> str:
    name = (
        L(f"„{target.name}“", f"“{target.name}”")
        if target is not None and target.name
        else L("die Switches", "the switches")
    )
    return L(
        f"keine Zugangsdaten fuer {name} - im Web-UI beim Quellsystem hinterlegen",
        f"no credentials for {name} - store them at the source system in the web UI",
    )


def _snmp(target: Target | None) -> bool:
    """Zugang SNMPv3 statt SSH (ADR 0033)."""
    return target is not None and (target.access or "ssh") == "snmp"


def _has_credentials(target: Target | None) -> bool:
    if _snmp(target):
        from collectors.snmp.tasks import has_credentials

        return has_credentials(target)
    return bool(target is not None and target.username and target.password)


def read_switch(entry: str, target: Target) -> dict:
    """Ein Switch: Ausgaben aller Befehle; Befehle mit Fehlermeldung bleiben leer (`notes`).
    `seconds` = Dauer (1.9.0: der Lauf nennt den laengsten Switch), `mode` = `shell`, wenn der
    Switch ueber die begrenzte Sitzung gelesen wurde - auch nach einem spaeteren Wechsel."""
    start = time.monotonic()
    client = SwitchClient(entry, target.username or "", target.password or "",
                          known_key=target.known_hosts.get(entry))  # fmt: skip
    outputs: dict[str, str] = {}
    notes: list[str] = []
    with client:
        version = client.version()  # per Exec, sonst begrenzte Sitzung (Catalyst 1200/1300)
        platform = detect_platform(version)
        client.prepare(platform)  # merkt die Plattform auch fuer einen spaeteren Wechsel
        outputs["show version"] = version
        for cmd in COMMANDS[platform][1:]:
            try:
                text = client.run(cmd)
            except Exception as exc:  # noqa: BLE001 - ein Befehl darf fehlen
                notes.append(f"{cmd}: {type(exc).__name__}")
                continue
            error = cli_error(text)
            if error and not quiet(platform, cmd, error):
                notes.append(f"{cmd}: {error}")
            outputs[cmd] = text
        # Routing (ADR 0032): Fehler wertet `routing.device` aus (nicht lesbar bzw. Funktion aus)
        routing_errors: dict[str, str] = {}
        for i, cmd in enumerate(routing.COMMANDS.get(platform, ())):
            try:
                outputs[cmd] = client.run(cmd)
            except Exception as exc:  # noqa: BLE001 - Teil „nicht lesbar“, die anderen weiter
                routing_errors[cmd] = type(exc).__name__
                continue
            if i == 0 and routing.not_routing(outputs[cmd]):
                break  # Switch ohne Routing: die weiteren Routing-Befehle sparen
        # erst am Ende: schliesst das Geraet die Verbindung nach dem Exec, wechselt der Client
        # beim zweiten Befehl in die Sitzung (1.9.0)
        mode = client.mode
    cut = sorted(set(getattr(client, "cut", ())) & routing.ALL)
    return {"host": entry, "platform": platform, "outputs": outputs, "notes": notes,
            "host_key": client.host_key, "mode": mode, "routing_errors": routing_errors,
            "routing_cut": cut, "seconds": round(time.monotonic() - start, 1)}  # fmt: skip


def gather(target: Target | None = None) -> dict:
    """Alle Switches des Quellsystems; `errors` je Adresse, die nicht gelesen wurde."""
    if not _has_credentials(target):
        if not samples_allowed():  # Agent bzw. Betrieb (1.6.0): nie Beispieldaten
            raise RuntimeError(_no_credentials(target))
        return _sample()
    entries = hosts(target.read_scope)
    if not entries:
        raise RuntimeError(_no_hosts())
    if _snmp(target):  # SNMPv3 (ADR 0033)
        from collectors.snmp import tasks as snmp

        return snmp.gather(target, entries)
    switches, errors = [], []

    @carry  # Threads: Meldungen in der Sprache des Aufrufers (im Lauf deutsch)
    def one(entry: str):
        try:
            return read_switch(entry, target), None
        except Exception as exc:  # noqa: BLE001 - je Switch melden, die anderen weiter lesen
            return None, f"{entry}: {diagnose.device_error(exc, 'cisco_switch')}"

    with ThreadPoolExecutor(max_workers=min(PARALLEL, len(entries))) as pool:
        for sw, err in pool.map(one, entries):
            if sw is not None:
                switches.append(sw)
            if err:
                errors.append(err)
    return {"switches": switches, "errors": errors}


def check(target: Target | None = None) -> tuple[bool, str]:
    """Anmeldung und `show version` je Switch (read-only, kein Ingest); Zugang SNMPv3: ein GET
    auf `sysName` je Geraet. Seit 1.9.0 bis zu `PARALLEL` Switches gleichzeitig wie im Lauf
    (Anwender: „das prüfen geht wesentlich länger … es bleibt vor allem bei den Switches lange
    hängen“) - Zeilen in der Reihenfolge des Lesebereichs."""
    if not _has_credentials(target):
        return False, _no_credentials(target) if target else L(
            "keine Switch-Creds", "no switch credentials"
        )
    entries = hosts(target.read_scope)
    if not entries:
        return False, _no_hosts()
    if _snmp(target):
        from collectors.snmp import tasks as snmp

        return snmp.check(target, entries)

    @carry  # Threads: Meldungen in der Sprache des Aufrufers (Konsole bzw. deutsch)
    def one(entry: str) -> tuple[bool, str]:
        try:
            client = SwitchClient(entry, target.username, target.password,
                                  known_key=target.known_hosts.get(entry))  # fmt: skip
            with client:
                version = client.version()
                mode = client.mode
        except Exception as exc:  # noqa: BLE001 - Diagnose fuer den Operator
            return False, f"{entry}: {diagnose.device_error(exc, 'cisco_switch', target, True)}"
        kind = PLATFORM_LABEL[detect_platform(version)]
        if mode == "shell":
            kind += L(", Sitzung", ", session")
        key = fingerprint(client.host_key or "")
        return True, L(f"{entry}: erreichbar ({kind}, Host-Schluessel {key})",
                       f"{entry}: reachable ({kind}, host key {key})")  # fmt: skip

    with ThreadPoolExecutor(max_workers=min(PARALLEL, len(entries))) as pool:
        results = list(pool.map(one, entries))  # map: Reihenfolge wie im Lesebereich
    return all(ok for ok, _ in results), "; ".join(line for _, line in results)


def _no_hosts() -> str:
    return L(
        "keine Switch-Adressen im Quellsystem (Lesebereich) hinterlegt",
        "no switch addresses stored in the source system (read scope)",
    )


def _span(seconds: float) -> str:
    """Dauer lesbar: „45 s“ bzw. „2 min 10 s“."""
    minutes, rest = divmod(round(seconds), 60)
    if not minutes:
        return f"{rest} s"
    return f"{minutes} min {rest} s" if rest else f"{minutes} min"


def durations(switches: list[dict], total: float) -> str | None:
    """Dauer des Laufs und der laengste Switch (1.9.0, Anwender: „Der Switchrun geht auch sehr
    lange“) - so sieht der Anwender Ausreisser, „Sitzung“ = ueber die begrenzte Sitzung gelesen.
    Deutsch (Meldung des Laufs, gespeichert); ohne gemessene Switches (Beispieldaten) nichts."""
    timed = [sw for sw in switches if sw.get("seconds") is not None]
    if not timed:
        return None
    slow = max(timed, key=lambda sw: sw["seconds"])
    shell = slow.get("mode") == "shell"
    if len(timed) == 1:
        return f"Dauer {_span(total)}" + (" (Sitzung)" if shell else "")
    name = slow.get("name") or slow["host"]  # Name aus `show version` (normalize), sonst Adresse
    detail = _span(slow["seconds"]) + (", Sitzung" if shell else "")
    return f"Dauer {_span(total)}, längster Switch {name} ({detail})"


@german_only  # Ergebnis und Meldung speichert der Orchestrator - deutsch (ADR 0028)
def collect_once(
    run_id: str, target: Target | None = None, via_agent: bool = False
) -> CollectorResult:
    """Eine Erfassung ohne Celery (Worker *und* externer Standalone-Collector)."""
    try:
        start = time.monotonic()
        raw = gather(target)
        total = time.monotonic() - start
        switches = raw.get("switches") or []
        errors = raw.get("errors") or []
        if not switches:
            raise RuntimeError("; ".join(errors) or "kein Switch gelesen")
        inventory = normalize(raw)  # ergaenzt Hinweise je Switch (z. B. MAC-Tabelle gekappt)
        routed = routing.normalize(raw)  # ADR 0032: Router/L3-Switches (auch leer: ersetzt)
        notes = [f"{sw['host']}: {n}" for sw in switches for n in sw.get("notes") or []]
        kinds = sorted({PLATFORM_LABEL.get(sw.get("platform"), "?") for sw in switches})
        if _snmp(target):  # Hersteller aus sysObjectID (ADR 0033)
            vendors = sorted({sw["vendor"] for sw in switches if sw.get("vendor")})
            kinds = [f"SNMPv3: {', '.join(vendors)}" if vendors else "SNMPv3"]
        message = [f"Switches: {len(switches)} gelesen ({', '.join(kinds)})"]
        if routing.summary(routed):
            message.append(routing.summary(routed))
        took = durations(switches, total)
        if took:
            message.append(took)
        if errors:
            message.append(f"nicht gelesen: {'; '.join(errors)}")
        if notes:
            message.append(f"Hinweise: {'; '.join(notes[:20])}")
        result = CollectorResult(
            status="completed",
            inventory=inventory,
            routing=routed,
            host_keys={sw["host"]: sw["host_key"] for sw in switches if sw.get("host_key")},
            message=" · ".join(message),
        )
    except Exception as exc:  # noqa: BLE001 - Fehler sichtbar an die interne API melden
        message = diagnose.device_error(exc, "cisco_switch")
        result = CollectorResult(status="failed", message=message)
    return confirmed(result, submit_result(run_id, result, via_agent=via_agent))


@app.task(name="collectors.cisco_switch.collect")
def collect(run_id: str) -> None:
    agent_api.central(run_id, collect_once)
