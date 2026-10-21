# Tests und CI

Was wo geprüft wird – lokal und in der CI (`.github/workflows/ci.yml`). Alle Tests laufen
gegen Testdaten (anonymisiert, `example.ch`, „Kunde A“), nie gegen echte Systeme.

| Ebene | Was | Lokal | CI-Job |
|---|---|---|---|
| Lint | ruff (Python), tsc (Frontend), PowerShell-Syntax der Agent-Skripte | `uv run ruff check .`, `uv run ruff format --check .`, `npm run lint`, `pwsh scripts/check_powershell.ps1` | `python`, `frontend` |
| Unit/API | Orchestrator (FastAPI-TestClient, SQLite), Collectors (Normalisierung, Clients mit Fakes), SoT-Gateway (Nur-Lese-Schutz, NetBox-Schreiber) | `uv run --package <paket> pytest <paket>/tests` | `python` (Abdeckung ≥ 90 % je Paket) |
| Postgres | Migrationen hoch/ganz runter/hoch, **`alembic check`** (Modell = Migrationen), alle Orchestrator-Tests gegen Postgres 16 | `AUTOMATO_TEST_DATABASE_URL=postgresql+psycopg://…` | `postgres` |
| NetBox-Sync | Fake-NetBox mit Eindeutigkeit wie NetBox (VLAN-Name je Gruppe, Site-Name), auch Abschnitt Virtualisierung (Cluster, VMs, VM-Interfaces, IPs) | in `orchestrator/tests/test_netbox_sync.py`, `test_vmware.py` | `python`, `postgres` |
| VMware | Nur-Lese-Schutz (WSDL-Name, z. B. `Destroy_Task` blockiert), Abruf über nachgebaute pyVmomi-Verbindung, Normalisierung; Orchestrator: Portgruppe → EPG, Befunde, VMs in Regeln/Doku | `collectors/tests/test_vmware.py`, `orchestrator/tests/test_vmware.py` | `python`, `postgres` |
| UI (E2E) | Playwright: jede Ansicht ohne Seiten-/API-Fehler, Kernabläufe (Suche, NAT-Links, Findings, Dienste, Standorte, IPv4/IPv6-Paare und gleichnamige Objekte, Virtual Server, Regeln je Subnetz, VMware-VMs mit EPG, Alle einlesen inkl. Abbrechen & neu starten, Ports je Gerät, Paar bestätigen, VIP-Ziel, Betriebszustand) gegen Orchestrator + Vite mit Beispieldaten (`web-ui/frontend/e2e/`) | `cd web-ui/frontend && npm run e2e` | `e2e` (Bericht als Artefakt bei Fehler) |
| Leistung | Grosses erzeugtes Regelwerk (Standard 3000 Regeln, 8000 Hosts): Einlesen und Abfragen messen – nur lokal, leere Datenbank | `AUTOMATO_DATABASE_URL=… uv run --package orchestrator python scripts/bench_large_ruleset.py` | – |
| Images | Compose-Konfiguration aller Profile, Agent-Paket, Images Orchestrator/Collector/Web-UI | `docker compose --env-file .env.example … config --quiet` | `docker` |
| Secrets | gitleaks über die ganze Historie | – | `gitleaks` |

Die Jobs laufen bei jedem Push nur für geänderte Komponenten; **nächtlich** (02:17 UTC) und
über *Run workflow* (workflow_dispatch) laufen alle.

**Neue Funktion → neuer Test:** Backend-Logik als Orchestrator-/Collector-Test (Testdaten in
den Testmodulen, wiederverwendet von `e2e/seed.py`), sichtbare Abläufe zusätzlich in
`web-ui/frontend/e2e/flows.spec.ts`. Schemaänderung = Alembic-Migration (sonst schlägt
`alembic check` fehl).
