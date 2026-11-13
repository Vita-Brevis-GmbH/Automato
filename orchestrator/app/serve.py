"""Start des Orchestrators (0.49.0): uvicorn auf Port 8000 - mit TLS intern (ADR 0023) per
HTTPS mit dem Zertifikat aus der internen CA, sonst wie bisher per HTTP.

    python -m app.serve            Orchestrator starten (Dienst orchestrator in docker-compose)
    python -m app.serve --check    Healthcheck: GET /health (https gegen die interne CA), Exit 0/1
"""

import argparse
import json
import logging
import sys
import urllib.request

from sot_gateway import trust

from app import tls
from app.config import get_settings

PORT = 8000
# Unbenutzte Verbindungen offen halten (0.139.2, Befund des Anwenders: „socket hang up“ im Proxy
# der Web-UI, HTTP 500 in der Übersicht): uvicorn schloss sie nach 5 s - genau im Takt, in dem die
# Übersicht fragt; der Proxy nahm ab und zu eine Verbindung, die gerade zuging. Der Proxy schliesst
# seine unbenutzten Verbindungen nach 30 s selbst (web-ui/frontend/vite.config.ts), also vorher.
KEEP_ALIVE_SECONDS = 75


def options(settings) -> dict:
    """uvicorn-Optionen; mit TLS intern Zertifikat und Schluessel des Orchestrators."""
    base = {"timeout_keep_alive": KEEP_ALIVE_SECONDS}
    if not settings.internal_tls:
        return base
    cert, key = tls.server_files(settings)
    return {**base, "ssl_certfile": cert, "ssl_keyfile": key}


def check(settings, url: str | None = None, timeout: float = 5.0) -> bool:
    scheme = "https" if settings.internal_tls else "http"
    url = url or f"{scheme}://localhost:{PORT}/health"
    context = tls.client_context(settings) if url.startswith("https://") else None
    try:
        with urllib.request.urlopen(url, timeout=timeout, context=context) as resp:  # noqa: S310
            return json.load(resp).get("status") == "ok"
    except (OSError, ValueError):
        return False


def logs() -> None:
    """Meldungen des Orchestrators mit Zeit ins Log des Containers (0.133.2, Anwender: „das log
    zeigt nichts“): bis 0.133.1 kamen nur Warnungen und Fehler ohne Zeit an, die Meldungen
    zum Fortschritt (`app.*`, Stufe INFO) nie. Andere Bibliotheken bleiben bei Warnungen."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.WARNING)
    logging.getLogger("app").setLevel(logging.INFO)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.serve")
    parser.add_argument("--check", action="store_true", help="Healthcheck statt Start")
    args = parser.parse_args(argv)
    settings = get_settings()
    if args.check:
        return 0 if check(settings) else 1
    import uvicorn

    logs()
    # zusaetzliche CAs aus deploy/trust (1.10.0, ADR 0053), bevor ein Client entsteht
    trust.zusaetzliche_cas()
    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT, **options(settings))  # noqa: S104
    return 0


if __name__ == "__main__":
    sys.exit(main())
