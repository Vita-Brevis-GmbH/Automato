"""Collector-Task fuer Cisco-Switches (ADR 0025, read-only).

Ein Quellsystem ist eine Gruppe von Switches mit gemeinsamem Read-only-Konto: die Adressen
stehen im Lesebereich (`read_scope`, eine je Zeile bzw. kommagetrennt, `host[:port]`). Je
Switch `show version` (erkennt IOS/IOS-XE bzw. NX-OS), danach die festen Befehle der Plattform.
Ein Switch, der nicht antwortet, fehlt im Ergebnis und steht in der Meldung; antwortet keiner,
schlaegt der Lauf fehl. Ohne Zugangsdaten liest nur der zentrale Worker (Lab) die Beispieldaten.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from importlib import resources

from collectors.celery_app import app
from collectors.cisco_switch.client import COMMANDS, SwitchClient, fingerprint
from collectors.cisco_switch.normalize import cli_error, detect_platform, normalize
from collectors.common import agent as agent_api
from collectors.common import diagnose
from collectors.common.dto import CollectorResult, Target
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
    name = f"„{target.name}“" if target is not None and target.name else "die Switches"
    return f"keine Zugangsdaten fuer {name} - im Web-UI beim Quellsystem hinterlegen"


def read_switch(entry: str, target: Target) -> dict:
    """Ein Switch: Ausgaben aller Befehle; Befehle mit Fehlermeldung bleiben leer (`notes`)."""
    client = SwitchClient(entry, target.username or "", target.password or "",
                          known_key=target.known_hosts.get(entry))  # fmt: skip
    outputs: dict[str, str] = {}
    notes: list[str] = []
    with client:
        version = client.run("show version")
        platform = detect_platform(version)
        outputs["show version"] = version
        for cmd in COMMANDS[platform][1:]:
            try:
                text = client.run(cmd)
            except Exception as exc:  # noqa: BLE001 - ein Befehl darf fehlen
                notes.append(f"{cmd}: {type(exc).__name__}")
                continue
            error = cli_error(text)
            if error and "not enabled" not in error.lower():
                notes.append(f"{cmd}: {error}")
            outputs[cmd] = text
    return {"host": entry, "platform": platform, "outputs": outputs, "notes": notes,
            "host_key": client.host_key}  # fmt: skip


def gather(target: Target | None = None) -> dict:
    """Alle Switches des Quellsystems; `errors` je Adresse, die nicht gelesen wurde."""
    if target is None or not (target.username and target.password):
        if get_settings().agent_token:
            raise RuntimeError(_no_credentials(target))
        return _sample()
    entries = hosts(target.read_scope)
    if not entries:
        raise RuntimeError("keine Switch-Adressen im Quellsystem (Lesebereich) hinterlegt")
    switches, errors = [], []

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
        return False, _no_credentials(target) if target else "keine Switch-Creds"
    entries = hosts(target.read_scope)
    if not entries:
        return False, "keine Switch-Adressen im Quellsystem (Lesebereich) hinterlegt"
    ok, lines = True, []
    for entry in entries:
        client = SwitchClient(entry, target.username, target.password,
                              known_key=target.known_hosts.get(entry))  # fmt: skip
        try:
            with client:
                version = client.run("show version")
        except Exception as exc:  # noqa: BLE001 - Diagnose fuer den Operator
            ok = False
            lines.append(f"{entry}: {diagnose.device_error(exc, 'cisco_switch', target, True)}")
            continue
        kind = "NX-OS" if detect_platform(version) == "nxos" else "IOS/IOS-XE"
        lines.append(f"{entry}: erreichbar ({kind}, Host-Schluessel "
                     f"{fingerprint(client.host_key or '')})")  # fmt: skip
    return ok, "; ".join(lines)


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
        notes = [f"{sw['host']}: {n}" for sw in switches for n in sw.get("notes") or []]
        message = [f"Switches: {len(switches)} gelesen"]
        if errors:
            message.append(f"nicht gelesen: {'; '.join(errors)}")
        if notes:
            message.append(f"Hinweise: {'; '.join(notes[:20])}")
        result = CollectorResult(
            status="completed",
            inventory=normalize(raw),
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
