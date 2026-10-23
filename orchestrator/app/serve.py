"""Start des Orchestrators (0.49.0): uvicorn auf Port 8000 - mit TLS intern (ADR 0023) per
HTTPS mit dem Zertifikat aus der internen CA, sonst wie bisher per HTTP.

    python -m app.serve            Orchestrator starten (Dienst orchestrator in docker-compose)
    python -m app.serve --check    Healthcheck: GET /health (https gegen die interne CA), Exit 0/1
"""

import argparse
import json
import sys
import urllib.request

from app import tls
from app.config import get_settings

PORT = 8000


def options(settings) -> dict:
    """uvicorn-Optionen; mit TLS intern Zertifikat und Schluessel des Orchestrators."""
    if not settings.internal_tls:
        return {}
    cert, key = tls.server_files(settings)
    return {"ssl_certfile": cert, "ssl_keyfile": key}


def check(settings, url: str | None = None, timeout: float = 5.0) -> bool:
    scheme = "https" if settings.internal_tls else "http"
    url = url or f"{scheme}://localhost:{PORT}/health"
    context = tls.client_context(settings) if url.startswith("https://") else None
    try:
        with urllib.request.urlopen(url, timeout=timeout, context=context) as resp:  # noqa: S310
            return json.load(resp).get("status") == "ok"
    except (OSError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.serve")
    parser.add_argument("--check", action="store_true", help="Healthcheck statt Start")
    args = parser.parse_args(argv)
    settings = get_settings()
    if args.check:
        return 0 if check(settings) else 1
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT, **options(settings))  # noqa: S104
    return 0


if __name__ == "__main__":
    sys.exit(main())
