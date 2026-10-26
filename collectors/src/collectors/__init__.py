"""Read-only Collectors: lesen Regeln aus Zielsystemen, liefern sie normalisiert
an den Orchestrator. Kein Schreibzugriff auf Zielsysteme, kein DB-Zugriff (ADR 0003)."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("collectors")  # Plattform-Version (Datei VERSION, scripts/version.py)
except PackageNotFoundError:  # pragma: no cover - nur ohne Installation (z. B. Quellbaum)
    __version__ = "0.0.0+unbekannt"
