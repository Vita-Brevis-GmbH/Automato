# Tests und CI

Was wo geprüft wird – lokal und in der CI (`.github/workflows/ci.yml`). Alle Tests laufen
gegen Testdaten (anonymisiert, `example.ch`, „Kunde A“), nie gegen echte Systeme.

| Ebene | Was | Lokal | CI-Job |
|---|---|---|---|
| Lint | ruff (Python), tsc (Frontend), PowerShell-Syntax der Agent-Skripte | `uv run ruff check .`, `uv run ruff format --check .`, `npm run lint`, `pwsh scripts/check_powershell.ps1` | `python`, `frontend` |
| Unit/API | Orchestrator (FastAPI-TestClient, SQLite), Collectors (Normalisierung, Clients mit Fakes), SoT-Gateway (Nur-Lese-Schutz, NetBox-Schreiber) | `uv run --package <paket> pytest <paket>/tests` | `python` (Abdeckung ≥ 90 % je Paket) |
| Postgres | Migrationen hoch/ganz runter/hoch, **`alembic check`** (Modell = Migrationen), alle Orchestrator-Tests gegen Postgres 16 | `AUTOMATO_TEST_DATABASE_URL=postgresql+psycopg://…` | `postgres` |
| NetBox-Sync | Fake-NetBox mit Eindeutigkeit wie NetBox (VLAN-Name je Gruppe, Site-Name) | in `orchestrator/tests/test_netbox_sync.py` | `python`, `postgres` |
| UI (E2E) | Playwright: jede Ansicht ohne Seiten-/API-Fehler, Kernabläufe (Suche, NAT-Links, Findings, Dienste, Standorte, Alle einlesen) gegen Orchestrator + Vite mit Beispieldaten (`web-ui/frontend/e2e/`) | `cd web-ui/frontend && npm run e2e` | `e2e` (Bericht als Artefakt bei Fehler) |
| Images | Compose-Konfiguration aller Profile, Agent-Paket, Images Orchestrator/Collector/Web-UI | `docker compose --env-file .env.example … config --quiet` | `docker` |
| Secrets | gitleaks über die ganze Historie | – | `gitleaks` |

Die Jobs laufen bei jedem Push nur für geänderte Komponenten; **nächtlich** (02:17 UTC) und
über *Run workflow* (workflow_dispatch) laufen alle.

**Neue Funktion → neuer Test:** Backend-Logik als Orchestrator-/Collector-Test (Testdaten in
den Testmodulen, wiederverwendet von `e2e/seed.py`), sichtbare Abläufe zusätzlich in
`web-ui/frontend/e2e/flows.spec.ts`. Schemaänderung = Alembic-Migration (sonst schlägt
`alembic check` fehl).
