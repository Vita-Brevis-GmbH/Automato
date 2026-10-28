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

import uvicorn  # noqa: E402
from app.api import jobs  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402
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
uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8000)
