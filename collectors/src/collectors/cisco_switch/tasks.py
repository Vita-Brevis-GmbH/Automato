"""Collector-Task fuer Cisco-Switches (ADR 0025, read-only).

Ein Quellsystem ist eine Gruppe von Switches bzw. Routern mit gemeinsamem Read-only-Konto: die
Adressen stehen im Lesebereich (`read_scope`, eine je Zeile bzw. kommagetrennt, `host[:port]`).
Je Geraet `show version` (erkennt IOS/IOS-XE, NX-OS, seit 0.68.0 Junos und die Business-CLI der
Catalyst 1200/1300 - diese ueber die begrenzte Sitzung -, seit 0.94.0 Arista EOS und Aruba CX),
danach die festen Befehle der Plattform (seit 0.94.0 auch die IPv6-Nachbarn).
Ein Switch, der nicht antwortet, fehlt im Ergebnis und steht in der Meldung; antwortet keiner,
schlaegt der Lauf fehl. Ohne Zugangsdaten liest nur der zentrale Worker (Lab) die Beispieldaten.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from importlib import resources

from collectors.celery_app import app
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
from collectors.config import get_settings

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


def read_switch(entry: str, target: Target) -> dict:
    """Ein Switch: Ausgaben aller Befehle; Befehle mit Fehlermeldung bleiben leer (`notes`)."""
    client = SwitchClient(entry, target.username or "", target.password or "",
                          known_key=target.known_hosts.get(entry))  # fmt: skip
    outputs: dict[str, str] = {}
    notes: list[str] = []
    with client:
        version = client.version()  # per Exec, sonst begrenzte Sitzung (Catalyst 1200/1300)
        platform = detect_platform(version)
        mode = client.mode
        client.prepare(platform)
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
    return {"host": entry, "platform": platform, "outputs": outputs, "notes": notes,
            "host_key": client.host_key, "mode": mode}  # fmt: skip


def gather(target: Target | None = None) -> dict:
    """Alle Switches des Quellsystems; `errors` je Adresse, die nicht gelesen wurde."""
    if target is None or not (target.username and target.password):
        if get_settings().agent_token:
            raise RuntimeError(_no_credentials(target))
        return _sample()
    entries = hosts(target.read_scope)
    if not entries:
        raise RuntimeError(_no_hosts())
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
    """Anmeldung und `show version` je Switch (read-only, kein Ingest)."""
    if target is None or not (target.username and target.password):
        return False, _no_credentials(target) if target else L(
            "keine Switch-Creds", "no switch credentials"
        )
    entries = hosts(target.read_scope)
    if not entries:
        return False, _no_hosts()
    ok, lines = True, []
    for entry in entries:
        client = SwitchClient(entry, target.username, target.password,
                              known_key=target.known_hosts.get(entry))  # fmt: skip
        try:
            with client:
                version = client.version()
                mode = client.mode
        except Exception as exc:  # noqa: BLE001 - Diagnose fuer den Operator
            ok = False
            lines.append(f"{entry}: {diagnose.device_error(exc, 'cisco_switch', target, True)}")
            continue
        kind = PLATFORM_LABEL[detect_platform(version)]
        if mode == "shell":
            kind += L(", Sitzung", ", session")
        key = fingerprint(client.host_key or "")
        lines.append(L(f"{entry}: erreichbar ({kind}, Host-Schluessel {key})",
                       f"{entry}: reachable ({kind}, host key {key})"))  # fmt: skip
    return ok, "; ".join(lines)


def _no_hosts() -> str:
    return L(
        "keine Switch-Adressen im Quellsystem (Lesebereich) hinterlegt",
        "no switch addresses stored in the source system (read scope)",
    )


@german_only  # Ergebnis und Meldung speichert der Orchestrator - deutsch (ADR 0028)
def collect_once(
    run_id: str, target: Target | None = None, via_agent: bool = False
) -> CollectorResult:
    """Eine Erfassung ohne Celery (Worker *und* externer Standalone-Collector)."""
    try:
        raw = gather(target)
        switches = raw.get("switches") or []
        errors = raw.get("errors") or []
        if not switches:
            raise RuntimeError("; ".join(errors) or "kein Switch gelesen")
        inventory = normalize(raw)  # ergaenzt Hinweise je Switch (z. B. MAC-Tabelle gekappt)
        notes = [f"{sw['host']}: {n}" for sw in switches for n in sw.get("notes") or []]
        kinds = sorted({PLATFORM_LABEL.get(sw.get("platform"), "?") for sw in switches})
        message = [f"Switches: {len(switches)} gelesen ({', '.join(kinds)})"]
        if errors:
            message.append(f"nicht gelesen: {'; '.join(errors)}")
        if notes:
            message.append(f"Hinweise: {'; '.join(notes[:20])}")
        result = CollectorResult(
            status="completed",
            inventory=inventory,
            host_keys={sw["host"]: sw["host_key"] for sw in switches if sw.get("host_key")},
            message=" · ".join(message),
        )
    except Exception as exc:  # noqa: BLE001 - Fehler sichtbar an die interne API melden
        message = diagnose.device_error(exc, "cisco_switch")
        result = CollectorResult(status="failed", message=message)
    return confirmed(result, submit_result(run_id, result, via_agent=via_agent))


@app.task(name="collectors.cisco_switch.collect")
def collect(run_id: str) -> None:
    collect_once(run_id, agent_api.run_target(run_id))
