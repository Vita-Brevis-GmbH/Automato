# ADR 0011 — Selbst-Update der Collector-Agenten

## Status

Angenommen (2026-10-20).

## Kontext

Der Collector-Agent läuft auf Windows-Stationen in Kundennetzen (ADR 0008/0009). Jede neue
Plattform oder Korrektur verlangte bisher, das Agent-Paket im Web-UI zu laden und auf jeder
Station `install.ps1` von Hand auszuführen. Ein veralteter Agent brach zuletzt mit
`KeyError: 'infoblox'` ab, als der Orchestrator eine neue Plattform kannte.

## Entscheidung

- **Kanal:** Der Agent aktualisiert sich über seinen bestehenden Agent-Kanal zum Orchestrator
  (`/internal/agent/*`, Agent-Token, gleicher TLS-/mTLS-Weg wie für die Läufe). Kein
  zusätzlicher Dienst, keine eingehende Verbindung zur Station.
- **Was:** collectors- und sot-gateway-Wheel sowie `run-agent.ps1` aus dem Agent-Paket des
  Orchestrator-Images. `GET /internal/agent/update` liefert die **Version** (Fingerabdruck
  über Namen und SHA-256 der Dateien – unabhängig von der Paketnummer) und die Dateien mit
  SHA-256; `GET /internal/agent/update/files/{name}` liefert nur Dateien aus diesem Manifest.
- **Wann:** vor jedem `--poll`/`--check`. Weicht die Version von der installierten
  (`agent-version.txt`) ab, lädt der Agent die Dateien, prüft jede Prüfsumme, installiert die
  Wheels in sein venv (`pip --no-deps --force-reinstall`, danach Auflösung neuer
  Abhängigkeiten), ersetzt `run-agent.ps1`, schreibt die Version und führt den Aufruf mit der
  neuen Version aus (`python -m collectors.run … --no-update`). Bei einem Fehler bleibt die
  bisherige Version aktiv.
- **Start über `python -m collectors.run`** statt `automato-collector.exe`, damit die `.exe`
  unter Windows während des Updates nicht gesperrt ist.
- **Steuerung:** je Agent im Web-UI (`auto_update`, Standard an; Admin, auditiert
  `agent.updated`); auf der Station abschaltbar mit `AUTOMATO_AGENT_AUTO_UPDATE=false`. Der
  Agent meldet seine Version bei jedem Aufruf (`X-Agent-Version`); das UI zeigt „aktuell“ bzw.
  „Update beim nächsten Lauf“. Jeder Download des Wheels wird auditiert
  (`agent.update_downloaded`).
- Aktiv nur für einen installierten Agenten (venv mit `run-agent.ps1` daneben), nicht im
  Repo/Dev und nicht im zentralen Worker (der kommt mit dem Container-Image).

### Versionierung (Ergänzung 0.2.0)

- Eine Plattform-Version für alle Pakete in der Datei `VERSION` (SemVer), gepflegt mit
  `scripts/version.py` (setzt `pyproject.toml`, `uv.lock`, `package.json`; CI prüft
  Gleichstand). Änderungen stehen in `CHANGELOG.md`.
- Agent-Version = `<Release>+<Build>` (z. B. `0.2.0+bb8829b8`): Release aus dem Wheel-Namen,
  Build = Fingerabdruck der Dateien. Angezeigt als „0.2.0 (Build bb8829b8)“ – im UI, in den
  Update-Meldungen und mit `automato-collector --version`.
- Der Agent lädt das Selbst-Update vor den Plattform-Modulen. Fehlt ein Teil der Installation
  (z. B. `sot_gateway`), installiert er die aktuelle Version auch bei gleicher Kennung neu und
  führt den Aufruf danach aus; erst nach erfolgreicher Startprüfung gilt die Version als
  installiert.

## Konsequenzen

- Nach einem Orchestrator-Update ziehen alle Agenten mit Auto-Update beim nächsten Lauf nach;
  manuell ist nur noch die **erste** Installation dieser Version nötig (ältere Agenten kennen
  das Update noch nicht).
- **Vertrauensanker ist der Orchestrator** – wie schon für die Geräte-Zugangsdaten (ADR 0009).
  Die Prüfsummen schützen gegen fehlerhafte Übertragung, nicht gegen einen kompromittierten
  Orchestrator. Ausbaustufe: signierte Pakete (Signatur mit einem Schlüssel ausserhalb des
  Orchestrators, Prüfung im Agenten). Bis dahin: Orchestrator absichern (E1), mTLS für
  `/internal` nutzen, wo verfügbar, und Auto-Update je Agent abschalten, wo eine Freigabe
  vorab nötig ist.
- Neue Abhängigkeiten eines Updates holt pip wie bei der Erstinstallation aus dem Paketindex;
  ist die Station ohne Internet, müssen Abhängigkeiten unverändert bleiben (sonst bleibt der
  Agent auf der alten Version und meldet das).
