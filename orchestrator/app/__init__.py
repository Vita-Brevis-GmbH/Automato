from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orchestrator")  # Plattform-Version (Datei VERSION, scripts/version.py)
except PackageNotFoundError:  # pragma: no cover - nur ohne Installation (z. B. Quellbaum)
    __version__ = "0.0.0+unbekannt"
