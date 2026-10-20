"""Selbst-Update des installierten Agenten (ADR 0011).

Vor jedem `--poll`/`--check` fragt der Agent den Orchestrator nach der aktuellen Paket-Version
(`/internal/agent/update`, Agent-Token, gleicher TLS-/mTLS-Kanal wie fuer die Laeufe). Weicht
sie von der installierten ab und hat der Admin das Update fuer diesen Agenten erlaubt, laedt
er die Dateien, prueft jede gegen ihre SHA-256 aus dem Manifest, installiert die Wheels in sein
venv, ersetzt `run-agent.ps1` und fuehrt den aktuellen Aufruf mit der neuen Version aus.
Scheitert etwas, laeuft der Agent mit der bisherigen Version weiter.

Aktiv nur fuer einen installierten Agenten (venv mit `run-agent.ps1` daneben, wie von
`install.ps1` angelegt) - nicht im Repo/Dev - und abschaltbar auf der Station mit
`AUTOMATO_AGENT_AUTO_UPDATE=false`.
"""

import hashlib
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from collectors.common import http
from collectors.config import get_settings

log = logging.getLogger(__name__)

VERSION_FILE = "agent-version.txt"
WRAPPER = "run-agent.ps1"
PIP_TIMEOUT = 900  # Sekunden je pip-Aufruf


def install_dir() -> Path | None:
    """Installationsordner (`<dir>\\venv` + `<dir>\\run-agent.ps1`), sonst None."""
    prefix = Path(sys.prefix)
    if prefix == Path(sys.base_prefix):  # kein venv
        return None
    base = prefix.parent
    return base if (base / WRAPPER).is_file() else None


def installed_version() -> str:
    """Installierte Agent-Version `<Release>+<Build>` (leer = unbekannt)."""
    base = install_dir()
    if base is None:
        return ""
    try:
        return (base / VERSION_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def describe(version: str) -> str:
    """`0.2.0+ab12cd34` -> `0.2.0 (Build ab12cd34)`; aeltere Kennungen unveraendert."""
    release, sep, build = version.partition("+")
    return f"{release} (Build {build})" if sep else (version or "unbekannt")


def current_label() -> str:
    """Anzeige fuer --version/--check: installierte Version, sonst Paketversion."""
    from collectors import __version__

    return describe(installed_version()) if installed_version() else __version__


def agent_headers() -> dict[str, str]:
    """Agent-Token + gemeldete Version (fuer jeden Aufruf an `/internal`)."""
    headers = {"X-Agent-Token": get_settings().agent_token}
    version = installed_version()
    if version:
        headers["X-Agent-Version"] = version
    return headers


def maybe_update(argv: Sequence[str], force: bool = False) -> int | None:
    """Aktualisieren und den Aufruf mit der neuen Version ausfuehren (Exit-Code), sonst None.

    `force`: auch bei gleicher Version neu installieren (unvollstaendige Installation)."""
    settings = get_settings()
    base = install_dir()
    if not settings.agent_token or not settings.agent_auto_update or base is None:
        return None
    try:
        info = http.get_json(
            f"{settings.orchestrator_url}/internal/agent/update", None, agent_headers()
        ).json()
    except Exception as exc:  # noqa: BLE001 - Update ist optional, der Lauf geht vor
        log.warning("Update-Pruefung nicht moeglich: %s", http.describe_error(exc))
        return None
    version = info.get("version")
    if not info.get("enabled") or not version or (version == installed_version() and not force):
        return None
    if version == installed_version():
        print(f"Agent-Installation unvollstaendig - installiere {describe(version)} neu")
    else:
        print(f"Aktualisiere Agent: {describe(installed_version())} -> {describe(version)}")
    try:
        install(info, base)
    except Exception as exc:  # noqa: BLE001
        print(
            f"Selbst-Update fehlgeschlagen ({http.describe_error(exc)}) - "
            "laufe mit der bisherigen Version weiter",
            file=sys.stderr,
        )
        return None
    print(f"Agent ist jetzt {describe(version)} - fuehre den Aufruf mit der neuen Version aus")
    cmd = [sys.executable, "-m", "collectors.run", *argv, "--no-update"]
    return subprocess.run(cmd, check=False).returncode  # noqa: S603 - eigener Interpreter


def install(info: dict, base: Path) -> None:
    """Dateien laden, Pruefsummen pruefen, Wheels installieren, Wrapper + Version ablegen."""
    settings = get_settings()
    staging = Path(tempfile.mkdtemp(prefix="automato-agent-update-"))
    try:
        for f in info.get("files", []):
            name = str(f["name"])
            if Path(name).name != name or name.startswith("."):
                raise RuntimeError(f"ungueltiger Dateiname im Manifest: {name!r}")
            data = http.get_json(
                f"{settings.orchestrator_url}/internal/agent/update/files/{name}",
                None,
                agent_headers(),
            ).content
            if hashlib.sha256(data).hexdigest() != f["sha256"]:
                raise RuntimeError(f"Pruefsumme von {name} stimmt nicht - Update verworfen")
            (staging / name).write_bytes(data)
        wheels = sorted(str(p) for p in staging.glob("*.whl"))
        main_wheel = next((w for w in wheels if Path(w).name.startswith("collectors-")), None)
        if main_wheel is None:
            raise RuntimeError("collectors-Wheel fehlt im Manifest")
        pip = [sys.executable, "-m", "pip", "--disable-pip-version-check", "install"]
        _pip([*pip, "--no-deps", "--force-reinstall", *wheels])
        _pip([*pip, "--find-links", str(staging), main_wheel])  # neue Abhaengigkeiten
        # Startpruefung: neue Version muss importierbar sein (sonst Version nicht uebernehmen,
        # der naechste Lauf versucht es erneut)
        _pip([sys.executable, "-c", "import collectors.run, sot_gateway"], "Startpruefung")
        wrapper = staging / WRAPPER
        if wrapper.is_file():
            # Neue Datei statt Ueberschreiben: so faellt auch eine Sperrmarkierung der alten
            # (Mark-of-the-Web, "Sicherheitswarnung" beim Start) weg
            fresh = base / f"{WRAPPER}.new"
            shutil.copyfile(wrapper, fresh)
            os.replace(fresh, base / WRAPPER)
        (base / VERSION_FILE).write_text(str(info["version"]), encoding="utf-8")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _pip(cmd: list[str], what: str = "pip") -> None:
    done = subprocess.run(  # noqa: S603 - fester Befehl, eigener Interpreter
        cmd, capture_output=True, text=True, timeout=PIP_TIMEOUT, check=False
    )
    if done.returncode != 0:
        tail = (done.stderr or done.stdout or "").strip().splitlines()[-3:]
        raise RuntimeError(f"{what} fehlgeschlagen: {' | '.join(tail)}")
