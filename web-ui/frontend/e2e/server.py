"""E2E-Server fuer die UI-Tests (Playwright, CI-Job `e2e`).

Startet den Orchestrator gegen eine frische SQLite-Datei, ersetzt den Celery-Dispatch durch
ein No-op und spielt Beispieldaten aus den Orchestrator-Tests ein (`seed.py`). Rein lokal: es
werden keine echten Systeme angesprochen, alle Daten sind anonymisierte Testdaten.

    uv run --package orchestrator python web-ui/frontend/e2e/server.py [PORT]
"""

import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = Path(os.environ.get("E2E_DB") or Path(tempfile.gettempdir()) / "automato-e2e.db")
DB.unlink(missing_ok=True)
os.environ["AUTOMATO_DATABASE_URL"] = f"sqlite:///{DB}"
os.environ.setdefault("AUTOMATO_CREDENTIALS_KEY", "e2e")  # nur Testdaten
# Stand der Doku (0.108.0): nach einer Aenderung neu rechnen, bevor die Doku kommt - die UI-Tests
# pruefen den alten Stand mit „wird aktualisiert“ eigens (`page.route`, apps.spec.ts)
os.environ.setdefault("AUTOMATO_DOC_STALE_SECONDS", "120")
# 0.133.2: alle Applikationen vorrechnen nicht in den UI-Tests - nach jeder Aenderung rechnete der
# Hintergrund sonst alle Beispiel-Applikationen (Uebersicht laufend neu); geprueft in Python-Tests
os.environ.setdefault("AUTOMATO_DOC_WARM_ALL", "false")
# 1.11.0 (ADR 0054): Freigaben aus dem Cockpit (PUT /freigaben) in den UI-Tests - nur ein
# Test-Token des lokalen UI-Servers, kein Geheimnis. Seit 1.14.0 (ADR 0058) nicht mehr aus der
# Umgebung, sondern als Kopplung (umschaltbar): mit verbundenem Cockpit ist das Kunden-Portal nur
# lesbar, die Tests der Oberflaeche ohne Cockpit entkoppeln dafuer (`PUT`/`DELETE /e2e/cockpit`,
# e2e/cockpit.ts)
E2E_COCKPIT_TOKEN = "e2e-cockpit-token"

import hashlib  # noqa: E402
from datetime import UTC, datetime  # noqa: E402

import uvicorn  # noqa: E402
from app.api import jobs  # noqa: E402
from app.db import get_engine, get_sessionmaker  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402
from app.store import kopplung  # noqa: E402
from sqlalchemy import event  # noqa: E402

sys.path.insert(0, str(HERE))
import seed  # noqa: E402

# SQLite mit Schreibern im Hintergrund (Befunde, Stand der Doku): WAL und Wartezeit statt „locked“
event.listen(get_engine(), "connect",
             lambda conn, _: (conn.execute("PRAGMA journal_mode=WAL"),
                              conn.execute("PRAGMA busy_timeout=15000")))  # fmt: skip
Base.metadata.create_all(get_engine())
jobs.celery_app.dispatch_collect = lambda platform, run_id: None  # Laeufe bleiben "wartet"
app = create_app()
seed.run(app)


def _cockpit(gekoppelt: bool) -> dict:
    """Nur im UI-Testserver: mit dem Test-Token koppeln bzw. entkoppeln (1.14.0)."""
    with get_sessionmaker()() as session:
        row = kopplung._ensure(session, kopplung.COCKPIT)
        token = hashlib.sha256(E2E_COCKPIT_TOKEN.encode()).hexdigest()
        row.token_hash = token if gekoppelt else None
        row.peer_name = "Cockpit E2E" if gekoppelt else None
        row.coupled_at = datetime.now(UTC) if gekoppelt else None
        session.commit()
    return {"gekoppelt": gekoppelt}


app.add_api_route("/e2e/cockpit", lambda: _cockpit(True), methods=["PUT"])
app.add_api_route("/e2e/cockpit", lambda: _cockpit(False), methods=["DELETE"])
_cockpit(True)
uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8000)
