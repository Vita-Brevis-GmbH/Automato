# Collector-Agent für Windows (ADR 0008)

Rollout des externen Collectors als geplante Aufgabe auf einer Windows-Station im
Kundennetz. Der Agent holt Läufe per Pull und spricht nur **ausgehend HTTPS** zum
Orchestrator (Details/Netz/Sicherheit: `collectors/EXTERNAL.md`).

## Überblick

```
Build-Host (Python)        Windows-Station (Kundennetz)
  uv build --package  ──▶  install.ps1 ──▶ venv + Wheel + Scheduled Task
  collectors (*.whl)                        └▶ run-agent.ps1 ⇒ automato-collector --poll
```

## 1. Wheel bauen (einmal, auf dem Build-Host)

```bash
uv build --package collectors --out-dir dist
# -> dist/collectors-0.1.0-py3-none-any.whl
```

## 2. Auf der Windows-Station installieren

Wheel + diesen Ordner (`deploy/collector-agent/`) auf die Station kopieren, dann in einer
**administrativen** PowerShell:

```powershell
.\install.ps1 -Wheel .\collectors-0.1.0-py3-none-any.whl -IntervalMinutes 15
# danach C:\Automato\collector\agent.env mit echten Werten fuellen (Token, Ziel-Creds, TLS)
```

`install.ps1` legt ein venv an, installiert das Wheel (bringt `automato-collector` mit),
kopiert `run-agent.ps1` + `agent.env` und registriert die Aufgabe **Automato Collector
Agent** (SYSTEM, alle N Minuten → `automato-collector --poll`).

## 3. Verbindung prüfen

```powershell
& 'C:\Automato\collector\venv\Scripts\automato-collector.exe' --check --platform fortigate
```

## Ohne Python auf der Station (optional)

Statt venv+Wheel eine eigenständige `.exe` bauen (auf einer Windows-Build-Maschine):

```powershell
pip install . pyinstaller
pyinstaller --onefile -n automato-collector deploy\collector-agent\agent_entry.py
```

Die `dist\automato-collector.exe` ersetzt dann den venv-Aufruf in `run-agent.ps1`.

## Dateien

| Datei | Zweck |
|---|---|
| `install.ps1` | venv + Wheel installieren, Scheduled Task registrieren |
| `run-agent.ps1` | `agent.env` laden, `automato-collector --poll` starten |
| `agent.env.example` | Vorlage für die Umgebung (Token, Ziel-Creds, TLS) |
| `agent_entry.py` | PyInstaller-Entry für eine Python-freie `.exe` |

**Secrets** gehören in `agent.env` (nicht committen) bzw. über den Secrets-Provider
(ADR 0007, `env:`/`file:`/`openbao:`). Agent-Token/Client-Zertifikate je Agent rotierbar.
