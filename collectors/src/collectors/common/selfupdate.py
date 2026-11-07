"""Selbst-Update des installierten Agenten (ADR 0011).

Vor jedem `--poll`/`--check` fragt der Agent den Orchestrator nach der aktuellen Paket-Version
(`/internal/agent/update`, Agent-Token, gleicher TLS-/mTLS-Kanal wie fuer die Laeufe). Weicht
sie von der installierten ab und hat der Admin das Update fuer diesen Agenten erlaubt, laedt
er die Dateien, prueft jede gegen ihre SHA-256 aus dem Manifest, installiert die Wheels in sein
venv, ersetzt `run-agent.ps1` und fuehrt den aktuellen Aufruf mit der neuen Version aus.
Scheitert etwas, laeuft der Agent mit der bisherigen Version weiter.

Mit `AUTOMATO_AGENT_SIGNING_PUBKEY` installiert der Agent nur Updates, deren Ed25519-Signatur
(offline erstellt, `scripts/agent_signing.py`) zu Version und Pruefsummen passt - ein
kompromittierter Orchestrator kann dann keine fremden Pakete verteilen.

Aktiv nur fuer einen installierten Agenten (venv mit `run-agent.ps1` daneben, wie von
`install.ps1` angelegt) - nicht im Repo/Dev - und abschaltbar auf der Station mit
`AUTOMATO_AGENT_AUTO_UPDATE=false`.

Robust gegen halbe Installationen (Dev-Befund 0.16.0: nach dem Update fehlte eine Datei, der
Agent haette danach nicht mehr gestartet):
- nur ein Update gleichzeitig je Installation (`update.lock`; geplante Aufgabe und ein Start
  von Hand duerfen nicht zugleich in dasselbe venv installieren),
- nach pip pruefen, ob alle Dateien der Wheels da sind, sonst einmal wiederholen,
- scheitert es trotzdem, die zuletzt guten Pakete (`packages/`) wieder installieren; daraus
  repariert sich auch `run-agent.ps1`, wenn der Agent nicht startet.
"""

import hashlib
import logging
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path

from collectors.common import http
from collectors.common.i18n import Failure, L, both, language_header
from collectors.config import get_settings

log = logging.getLogger(__name__)

VERSION_FILE = "agent-version.txt"
WRAPPER = "run-agent.ps1"
PIP_TIMEOUT = 900  # Sekunden je pip-Aufruf
LOCK_FILE = "update.lock"
LOCK_STALE = 1800  # Sekunden: aelter = abgebrochenes Update, Sperre wird uebernommen
PACKAGES_DIR = "packages"  # zuletzt gute Wheels (Rueckfall, Selbstheilung in run-agent.ps1)

# Ergebnis der letzten Update-Pruefung - geht als Header `X-Agent-Update` an den Orchestrator,
# die Agentenliste zeigt so, warum ein Agent nicht aktualisiert (Dev-Befund 0.11.4)
_note = ""


def note() -> str:
    return _note


def _set_note(text: str) -> None:
    global _note
    _note = text


def _report(make, suffix: tuple[str, str] | None = None) -> None:
    """Grund der Update-Pruefung: deutsch an den Orchestrator (Agentenliste, uebersetzt beim
    Abruf), auf der Konsole in `AUTOMATO_LANG` (ADR 0028 Schritt 4)."""
    note, shown = both(make)
    _set_note(note)
    print(shown + (L(*suffix) if suffix else ""), file=sys.stderr)


_KEEP_OLD = (
    " - laufe mit der bisherigen Version weiter",
    " - continuing with the previous version",
)


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
    return f"{release} (Build {build})" if sep else (version or L("unbekannt", "unknown"))


def current_label() -> str:
    """Anzeige fuer --version/--check: installierte Version, sonst Paketversion."""
    from collectors import __version__

    return describe(installed_version()) if installed_version() else __version__


def agent_headers() -> dict[str, str]:
    """Agent-Token + gemeldete Version (fuer jeden Aufruf an `/internal`) und die Sprache der
    Konsole - der Orchestrator speichert trotzdem deutsch (ADR 0028 Schritt 4)."""
    headers = {"X-Agent-Token": get_settings().agent_token, **language_header()}
    version = installed_version()
    if version:
        headers["X-Agent-Version"] = version
    if install_dir() is not None:  # nur der installierte Agent aktualisiert sich selbst
        text = _note or "ok"
        headers["X-Agent-Update"] = text.encode("ascii", "replace").decode()[:300]
    return headers


def maybe_update(argv: Sequence[str], force: bool = False) -> int | None:
    """Aktualisieren und den Aufruf mit der neuen Version ausfuehren (Exit-Code), sonst None.

    `force`: auch bei gleicher Version neu installieren (unvollstaendige Installation)."""
    settings = get_settings()
    base = install_dir()
    if not settings.agent_token or base is None:
        return None
    if not settings.agent_auto_update:  # nur an den Orchestrator: deutsch
        _set_note("Selbst-Update auf der Station abgeschaltet (AUTOMATO_AGENT_AUTO_UPDATE=false)")
        return None
    try:
        info = http.get_json(
            f"{settings.orchestrator_url}/internal/agent/update", None, agent_headers()
        ).json()
    except Exception as exc:  # noqa: BLE001 - Update ist optional, der Lauf geht vor
        _report(lambda e=exc: L("Update-Pruefung nicht moeglich: ", "Update check not possible: ")
                + http.describe_error(e))  # fmt: skip
        return None
    version = info.get("version")
    if not info.get("enabled") or not version or (version == installed_version() and not force):
        _set_note("")
        return None
    if settings.agent_signing_pubkey and not signature_ok(settings.agent_signing_pubkey, info):
        _report(lambda: L(
            f"Update {describe(version)} nicht installiert: Signatur fehlt oder passt nicht "
            "zum hinterlegten Schluessel (Paket signieren, deploy/agent-signing)",
            f"Update {describe(version)} not installed: signature missing or does not match "
            "the stored key (sign the package, deploy/agent-signing)",
        ), _KEEP_OLD)  # fmt: skip
        return None
    if version == installed_version():
        print(L(f"Agent-Installation unvollstaendig - installiere {describe(version)} neu",
                f"Agent installation incomplete - reinstalling {describe(version)}"))  # fmt: skip
    else:
        old, new = describe(installed_version()), describe(version)
        print(L(f"Aktualisiere Agent: {old} -> {new}", f"Updating agent: {old} -> {new}"))
    try:
        install(info, base)
    except Exception as exc:  # noqa: BLE001 - Text erst bei der Ausgabe (`Failure`)
        _report(lambda e=exc: L("Selbst-Update fehlgeschlagen: ", "Self-update failed: ")
                + http.describe_error(e), _KEEP_OLD)  # fmt: skip
        return None
    _set_note("")
    new = describe(version)
    print(L(f"Agent ist jetzt {new} - fuehre den Aufruf mit der neuen Version aus",
            f"Agent is now {new} - running the call with the new version"))  # fmt: skip
    cmd = [sys.executable, "-m", "collectors.run", *argv, "--no-update"]
    return subprocess.run(cmd, check=False).returncode  # noqa: S603 - eigener Interpreter


SIGNING_CONTEXT = "automato-agent-update/v1"  # wie app.store.agent_package im Orchestrator


def signed_message(info: dict) -> bytes:
    files = sorted((str(f["name"]), str(f["sha256"])) for f in info.get("files", []))
    lines = [SIGNING_CONTEXT, str(info["version"]), *(f"{n}:{h}" for n, h in files)]
    return ("\n".join(lines) + "\n").encode()


def signature_ok(pubkey_b64: str, info: dict) -> bool:
    """Ed25519-Signatur des Manifests pruefen (Version + Dateien mit SHA-256)."""
    import base64
    import binascii

    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    signature = info.get("signature")
    if not signature:
        return False
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(pubkey_b64, validate=True))
        key.verify(base64.b64decode(signature, validate=True), signed_message(info))
    except (InvalidSignature, ValueError, binascii.Error):
        return False
    return True


def _lock(base: Path) -> Path:
    """Nur ein Update gleichzeitig je Installation; eine alte Sperre (Abbruch) wird uebernommen."""
    lock = base / LOCK_FILE
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except FileNotFoundError:
                continue  # gerade freigegeben
            if age < LOCK_STALE:
                raise Failure(lambda: L(
                    "ein anderer Agent-Prozess installiert gerade ein Update (update.lock)",
                    "another agent process is installing an update right now (update.lock)",
                )) from None  # fmt: skip
            lock.unlink(missing_ok=True)
            continue
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return lock
    raise Failure(lambda: L("Update-Sperre (update.lock) nicht moeglich",
                            "update lock (update.lock) not possible"))  # fmt: skip


def _site_packages() -> Path:
    return Path(sysconfig.get_paths()["purelib"])


def _missing_files(wheels: list[str]) -> list[str]:
    """Dateien der Wheels, die nach pip im venv fehlen (ohne dist-info)."""
    site = _site_packages()
    missing = []
    for wheel in wheels:
        try:
            names = zipfile.ZipFile(wheel).namelist()
        except zipfile.BadZipFile:
            continue
        for name in names:
            top = name.split("/", 1)[0]
            if name.endswith("/") or top.endswith((".dist-info", ".data")):
                continue
            if not (site / name).is_file():
                missing.append(name)
    return missing


def _restore(base: Path) -> Callable[[], str]:
    """Zuletzt gute Pakete wieder installieren (Rueckfall); Ergebnis fuer die Meldung - als
    Text in der Sprache der Ausgabe (einmal installiert, zweimal ausgegeben)."""
    wheels = sorted(str(p) for p in (base / PACKAGES_DIR).glob("*.whl"))
    if not wheels:
        return lambda: L(
            "keine zuletzt guten Pakete fuer den Rueckfall - install.ps1 erneut ausfuehren",
            "no last good packages for the fallback - run install.ps1 again",
        )
    try:
        _pip([sys.executable, "-m", "pip", "--disable-pip-version-check", "install",
              "--no-deps", "--force-reinstall", *wheels], "Rueckfall")  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - Meldung reicht, Ursache steht davor
        return lambda e=exc: L(f"Rueckfall fehlgeschlagen: {e}", f"fallback failed: {e}")
    return lambda: L("zuletzt gute Pakete wieder installiert", "last good packages reinstalled")


def _keep(base: Path, wheels: list[str]) -> None:
    """Neue Wheels als zuletzt gute Pakete ablegen (ersetzt die alten)."""
    fresh = base / f"{PACKAGES_DIR}.new"
    shutil.rmtree(fresh, ignore_errors=True)
    fresh.mkdir()
    for w in wheels:
        shutil.copyfile(w, fresh / Path(w).name)
    shutil.rmtree(base / PACKAGES_DIR, ignore_errors=True)
    os.replace(fresh, base / PACKAGES_DIR)


def install(info: dict, base: Path) -> None:
    """Dateien laden, Pruefsummen pruefen, Wheels installieren, Wrapper + Version ablegen."""
    settings = get_settings()
    lock = _lock(base)
    staging = Path(tempfile.mkdtemp(prefix="automato-agent-update-"))
    try:
        for f in info.get("files", []):
            name = str(f["name"])
            if Path(name).name != name or name.startswith("."):
                raise Failure(
                    lambda name=name: L(
                        f"ungueltiger Dateiname im Manifest: {name!r}",
                        f"invalid file name in the manifest: {name!r}",
                    )
                )
            data = http.get_json(
                f"{settings.orchestrator_url}/internal/agent/update/files/{name}",
                None,
                agent_headers(),
            ).content
            if hashlib.sha256(data).hexdigest() != f["sha256"]:
                raise Failure(lambda name=name: L(
                    f"Pruefsumme von {name} stimmt nicht - Update verworfen",
                    f"checksum of {name} does not match - update discarded",
                ))  # fmt: skip
            (staging / name).write_bytes(data)
        wheels = sorted(str(p) for p in staging.glob("*.whl"))
        main_wheel = next((w for w in wheels if Path(w).name.startswith("collectors-")), None)
        if main_wheel is None:
            raise Failure(lambda: L("collectors-Wheel fehlt im Manifest",
                                    "collectors wheel missing in the manifest"))  # fmt: skip
        pip = [sys.executable, "-m", "pip", "--disable-pip-version-check", "install"]
        try:
            _pip([*pip, "--no-deps", "--force-reinstall", *wheels])
            _pip([*pip, "--find-links", str(staging), main_wheel])  # neue Abhaengigkeiten
            missing = _missing_files(wheels)
            if missing:  # z. B. zweiter Prozess oder Virenschutz: einmal wiederholen
                print(
                    L(
                        f"nach der Installation fehlt {missing[0]} - installiere erneut",
                        f"{missing[0]} missing after the installation - reinstalling",
                    )
                )
                _pip([*pip, "--no-deps", "--force-reinstall", *wheels])
                missing = _missing_files(wheels)
            if missing:
                count, first = len(missing), missing[0]
                raise Failure(lambda: L(
                    f"nach der Installation fehlen {count} Datei(en), z. B. {first} - "
                    "Virenschutz (Schutzverlauf) bzw. einen zweiten Agent-Prozess pruefen",
                    f"after the installation {count} file(s) are missing, e.g. {first} - check "
                    "the antivirus (protection history) or a second agent process",
                ))  # fmt: skip
            # Startpruefung: neue Version muss importierbar sein (sonst Version nicht
            # uebernehmen, der naechste Lauf versucht es erneut)
            _pip([sys.executable, "-c", "import collectors.run, sot_gateway"], "Startpruefung")
        except Exception as exc:
            restored = _restore(base)
            raise Failure(lambda e=exc: f"{e} ({restored()})") from exc
        _keep(base, wheels)
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
        lock.unlink(missing_ok=True)


_WHAT_EN = {"Rueckfall": "fallback", "Startpruefung": "start check"}


def _pip(cmd: list[str], what: str = "pip") -> None:
    done = subprocess.run(  # noqa: S603 - fester Befehl, eigener Interpreter
        cmd, capture_output=True, text=True, timeout=PIP_TIMEOUT, check=False
    )
    if done.returncode != 0:
        tail = " | ".join((done.stderr or done.stdout or "").strip().splitlines()[-3:])
        raise Failure(lambda: L(f"{what} fehlgeschlagen: {tail}",
                                f"{_WHAT_EN.get(what, what)} failed: {tail}"))  # fmt: skip
