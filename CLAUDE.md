# Projektkontext für Claude Code

Arbeitsregeln für dieses Projekt – immer zuerst lesen. Der ausführliche Stand je Bereich steht
in `docs/STAND.md`, die Versionen in `CHANGELOG.md`, Planung und Befunde in `ROADMAP.md`.

## Was ist das

Automatisierungsplattform für heterogene Infrastruktur (FortiGate, Cisco ACI, VMware, Switches/
Router, NSX ALB, Infoblox, NetBox). UC1 „Firewall-Regel-Dokumentation (read-only)“ ist umgesetzt
und wird erweitert. Details: `README.md`, `ARCHITECTURE.md`, `docs/adr/`.

- **Mehrere Kunden mit verschiedenen Werkzeugen** (NetBox oder Infoblox, ServiceNow oder nicht):
  **Adapter-Pattern** an jeder Kunden-Schnittstelle (ADR 0002), Tools austauschbar ohne neue
  Erfassung. Stack: Python (FastAPI/SQLAlchemy/Alembic, uv-Workspace `orchestrator`,
  `collectors`, `sot-gateway`), React/Vite/TypeScript (`web-ui/frontend`).
- **Konfiguration statt Code** für kundenabhängige Fachentscheide (seit 0.104.0): z. B. die
  Kategorien der Applikations-Doku im Namenskonzept (`store/appdoc_catalog.py`); Vorgaben im
  Code, die Plattform läuft ohne Konfiguration.

## Verbindliche Vorgaben (immer beachten)

- **Nur nach NetBox schreiben, sonst strikt nur lesen.** FortiGate, APIC und Infoblox DDI
  werden ausschliesslich gelesen (Nur-Lese-Schutz im Code, `sot_gateway.readonly`). Der
  einzige Schreibweg ist der NetBox-Sync (Vorschau → Übernehmen, ADR 0010). Neue
  Schreibpfade nur mit eigener ADR und Freigabe.
- **Testsysteme sind produktiv:** nur Read-only-Konten, gelesene Daten wie Produktionsdaten
  behandeln; Negativtests gegen Geräte (z. B. R16 `--check --strict`) nur opt-in.
- **Keine Secrets** in Chat, Code, Tests, Doku oder Commits; `.env`, `pki/`-Ordner und
  `docker-compose.override.yml` bleiben gitignored.
- **Der Name des Referenzkunden wird nirgends hinterlegt** (auch nicht über Gerätenamen, VRFs,
  Tenants); Beispiele anonymisiert (`example.ch`, „Kunde A“).
- **Kein CSV-Import.** Wörterbücher (Namensprofile) sind in-App gepflegte Laufzeitdaten; die
  Plattform muss ohne befüllte Wörterbücher voll funktionieren (Vorgaben im Code). **Einzige
  Ausnahme** (seit 0.75.0): die Stammdaten der Applikationen per CSV mit Vorschau; Mitglieder und
  Wörterbücher nie per CSV.
- **Open Source bevorzugt**; kommerzielle Tools nur, wo der Kunde sie schon hat.
- Kommunikation mit dem Anwender auf Deutsch; Commits nur auf den vereinbarten Branch, PRs nur
  auf Wunsch; keine Modell-Kennungen in Commits/Code.

## Ablauf je Änderung (Entscheid des Anwenders 2026-10-28: weniger Entwicklungszeit)

- **Abnahme:** Der Anwender testet jede freigegebene Version sofort. Eine Version gilt als
  abgenommen, sobald seine nächste Rückmeldung keinen Einwand dazu enthält – in `ROADMAP.md`
  („Abgenommen“) nachführen. Abgenommen bis **0.113.2** (2026-10-29).
- **Klärung vor dem Bauen:** ändert sich Bedienung oder Bedeutung und ist der Wunsch mehrdeutig,
  zuerst 3–5 Zeilen Vorschlag mit einer Rückfrage, dann bauen.
- **Sammeln:** Anpassungen einer Rückmeldungsrunde in **einer** Version (Patch = Korrektur,
  Minor = Funktion), nicht je Punkt eine Version.
- **Tests gezielt:** lokal die betroffenen Testdateien und UI-Specs; die volle Suite läuft in der
  CI. Vor dem Push: Lint, Format, `npm run lint`/`build`, betroffene Tests.
- **Doku je Version:** `CHANGELOG.md` und eine Zeile in `ROADMAP.md`; ADR nur bei einem Entscheid,
  `docs/BETRIEB.md` nur bei geänderter Bedienung, `docs/TESTS.md` bei neuen Testbereichen,
  `docs/STAND.md` nur bei neuen Bausteinen. `CLAUDE.md` nur bei geänderten Regeln.

## Arbeitsweise (Technik)

- Tests: `uv run --package <orchestrator|collectors|sot-gateway> pytest <paket>/tests` (parallel
  mit `-n auto`); Lint `uv run ruff check .` + `uv run ruff format --check .`; Frontend in
  `web-ui/frontend`: `npm run lint` (tsc) + `npm run build`, UI-Tests `npm run e2e` bzw. schneller
  `npm run e2e:shards` (Playwright; jeder UI-Test läuft für sich, aufeinander aufbauende nur in
  `test.describe.serial`). Übersicht und CI-Jobs: `docs/TESTS.md`. Frontend **nicht** mit
  prettier umformatieren.
- **Neue Texte immer zweisprachig** `L("deutsch", "english")` – Oberfläche `src/i18n.ts`, Server
  `app/i18n.py` (nur zur Laufzeit, nie auf Modulebene; Gespeichertes bleibt deutsch,
  `@german_only`; Werte/Schlüssel nie übersetzen), ADR 0028; Begriffe nach `docs/GLOSSAR.md`.
- Gegen Postgres: `AUTOMATO_TEST_DATABASE_URL=postgresql+psycopg://…` (Schema einmal je
  Datenbank, Tabellen je Test geleert; mit `-n auto` je Worker eine eigene Datenbank).
  Jede Schemaänderung = Alembic-Migration (up/down gegen Postgres prüfen).
- **Version:** `python3 scripts/version.py set X.Y.Z` (danach `uv sync --all-packages`), CI prüft
  mit `check`.
- **Module** (ADR 0036, `orchestrator/app/modules.py`): jede neue Datei in `app/api`/`app/store`
  einem Modul zuordnen (sonst scheitert `tests/test_modules.py`); nur der Schreibpfad nutzt
  `netbox_writer`/`netbox_sync`; neue Router mit `modules.guard(<Modul>)` einbinden.
- **Prüfungen und Ansichten je Modul** (ADR 0039): eine neue Prüfung der Findings ist eine Datei
  `app/checks/<modul>/<prüfung>.py` mit `CHECK = Check(...)` (nur Befunde ihrer Typen) –
  `reconcile.py` nicht ändern; eine neue Ansicht ein Eintrag in
  `web-ui/frontend/src/modules/<modul>/index.tsx` (und in `views` des Moduls). Neues kommt in
  den Ordner seines Moduls, bestehende Dateien ziehen um, wenn sie ohnehin angepasst werden.
- **Stand der Doku** (ADR 0037): was Doku oder Verbindungen verändert, muss den Fingerabdruck
  ändern (Audit-Eintrag, Lauf oder `store.touch(app)`); prüfen mit `AUTOMATO_TEST_DOC_SNAPSHOTS=1`.
- Entscheide als ADR in `docs/adr/`, Risiken in `docs/RISKS.md`, Bedienung in `docs/BETRIEB.md`.

## Aktueller Stand

0.114.0 – Einzelheiten je Bereich in `docs/STAND.md`. Offene Punkte und nächste Arbeiten in
`ROADMAP.md` („Roadmap nach Versionen“): Version 1 bis zur Produktivschaltung DC = **1.0.0** (R14),
Version 2 = neuer Name und Cockpit (eigenes Projekt), Version 3 = Automatisierung.
