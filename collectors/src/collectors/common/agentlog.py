"""Log-Datei des installierten Agenten (Dev-Befund 0.11.4).

Die geplante Aufgabe verwirft stdout/stderr - Meldungen wie "Update nicht installiert" oder ein
FortiGate-Fehler waren deshalb nirgends zu sehen. Jetzt geht jede Ausgabe zusaetzlich mit
Zeitstempel nach `<Installationsordner>\\logs\\agent-JJJJ-MM-TT.log` (UTF-8); Dateien aelter
als `KEEP_DAYS` werden beim Start geloescht. Zugangsdaten gibt der Agent nie aus.
"""

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import TextIO

from collectors.common.i18n import L

KEEP_DAYS = 14


class _Tee:
    """Schreibt auf den bisherigen Strom und mit Zeitstempel je Zeile in die Log-Datei."""

    def __init__(self, stream: TextIO, log: TextIO, tag: str) -> None:
        self._stream = stream
        self._log = log
        self._tag = tag
        self._line_start = True

    def write(self, text: str) -> int:
        try:
            self._stream.write(text)
        except (OSError, ValueError):  # Konsole weg (geplante Aufgabe): nur noch Datei
            pass
        for part in text.splitlines(keepends=True):
            if self._line_start:
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._log.write(f"{stamp} {self._tag} ")
            self._log.write(part)
            self._line_start = part.endswith("\n")
        self._log.flush()
        return len(text)

    def flush(self) -> None:
        for s in (self._stream, self._log):
            try:
                s.flush()
            except (OSError, ValueError):
                pass

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


def log_dir(base: Path) -> Path:
    return base / "logs"


def setup(base: Path | None, now: float | None = None) -> Path | None:
    """Ausgaben zusaetzlich in die Tages-Logdatei schreiben; Rueckgabe: Pfad (oder None)."""
    if base is None:
        return None
    folder = log_dir(base)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        _prune(folder, now or time.time())
        path = folder / f"agent-{datetime.now():%Y-%m-%d}.log"
        log = path.open("a", encoding="utf-8")  # noqa: SIM115 - lebt bis Prozessende
    except OSError:
        return None  # kein Schreibrecht: ohne Datei weiterlaufen
    sys.stdout = _Tee(sys.stdout, log, "INFO ")
    sys.stderr = _Tee(sys.stderr, log, L("FEHLER", "ERROR"))  # AUTOMATO_LANG (0.90.0)
    return path


def _prune(folder: Path, now: float) -> None:
    for old in folder.glob("agent-*.log"):
        try:
            if now - old.stat().st_mtime > KEEP_DAYS * 86400:
                old.unlink()
        except OSError:
            pass
