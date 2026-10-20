# Collector-Agent für Windows (ADR 0008/0009)

Rollout des externen Collectors als geplante Aufgabe auf einer Windows-Station im
Kundennetz. Der Agent holt Läufe per Pull und spricht nur **ausgehend HTTPS** zum
Orchestrator (Details/Netz/Sicherheit: `collectors/EXTERNAL.md`).

**Geräte werden nicht am Agenten erfasst** (ADR 0009): FortiGate, Cisco APIC und
Referenzquellen wie die **Infoblox DDI** (ADR 0010) legt man im Web-UI unter **Mandanten &
Standorte → Quellsysteme** an (Adresse, read-only Zugangsdaten – FortiGate: API-Token, APIC:
Benutzer/Passwort + ACI-Tenants, Infoblox: WAPI-Benutzer/Passwort –, TLS-Prüfung)
und weist sie dort einem Agenten zu. Der Orchestrator speichert die Zugangsdaten
verschlüsselt und gibt sie pro Lauf **nur dem zugewiesenen Agenten**. Die `agent.env`
enthält deshalb nur Orchestrator-Adresse + Agent-Token (+ optional TLS). Ein Gerät lässt
sich jederzeit einem anderen Agenten zuweisen, ohne an der Station etwas zu ändern.

**Updates kommen automatisch** (ADR 0011): Der Agent aktualisiert sich vor jedem Lauf selbst
über seinen Agent-Kanal, wenn der Orchestrator ein neues Paket hat und „Auto-Update“ für ihn
aktiv ist (Web-UI → Collector-Agenten). Abschalten auf der Station:
`AUTOMATO_AGENT_AUTO_UPDATE=false` in `agent.env`. Manuell (`install.ps1`) ist nur die erste
Installation nötig – bzw. einmalig für Agenten, die älter sind als das Selbst-Update.

**Version anzeigen:** `C:\Automato\collector\venv\Scripts\python.exe -m collectors.run --version`
(z. B. `automato-collector 0.2.0 (Build bb8829b8)`); dieselbe Version steht im Web-UI in der
Agentenliste. `install.ps1` meldet „Installiere …“, „Aktualisiere alt -> neu“ bzw. „bereits
installiert – installiere neu“ und prüft am Ende, dass der Agent startet. Heruntergeladene
Dateien entsperrt es selbst (sonst fragt Windows bei jedem Start „Sicherheitswarnung“).

## Überblick

```
Build-Host (Python)        Windows-Station (Kundennetz)
  uv build --package  ──▶  install.ps1 ──▶ venv + Wheel + Scheduled Task
  collectors (*.whl)                        └▶ run-agent.ps1 ⇒ automato-collector --poll
```

## Schnellweg: Paket aus dem Web-UI (empfohlen)

Im Web-UI unter **Collector-Agenten** steht die Einrichtung Schritt für Schritt:
Geräte zuweisen, `agent.env` direkt im Browser erzeugen (Token des neuen Agenten wird
übernommen) und das
**Agent-Paket** (ZIP: collectors- und sot-gateway-Wheel + Skripte, ohne Zugangsdaten)
herunterladen. Das
Orchestrator-Image baut das Paket mit (Dockerfile-Stage `agent-package`). Auf der Station:

```powershell
cd $HOME\Downloads\automato-collector-agent     # ZIP entpackt, agent.env daneben gelegt
Get-ChildItem | Unblock-File
powershell -ExecutionPolicy Bypass -File .\install.ps1 -IntervalMinutes 15
powershell -ExecutionPolicy Bypass -File C:\Automato\collector\run-agent.ps1 -Check
```

`install.ps1` findet das Wheel neben sich und übernimmt eine daneben liegende `agent.env`.

## 1. Wheel bauen (manuell, auf einem Build-Host)

```bash
uv build --package collectors --out-dir dist
uv build --package sot-gateway --out-dir dist   # Infoblox-/NetBox-Leser (ADR 0010)
# -> dist/collectors-0.1.0-py3-none-any.whl, dist/sot_gateway-0.1.0-py3-none-any.whl
```

## 2. Auf der Windows-Station installieren

**Voraussetzung:** Python 3.11+ auf der Station (https://www.python.org/downloads/windows/,
beim Setup **„Add python.exe to PATH"** anhaken). `install.ps1` findet `py`, `python` oder
`python3` automatisch; ohne Python bricht es mit einem Hinweis ab. Ganz ohne Python auf der
Station: die PyInstaller-`.exe` bauen (siehe unten).

Beide Wheels + diesen Ordner (`deploy/collector-agent/`) in denselben Ordner auf die Station
kopieren (`install.ps1` installiert das sot-gateway-Wheel aus dem Ordner mit), dann in einer
**administrativen** PowerShell:

```powershell
.\install.ps1 -Wheel .\collectors-0.1.0-py3-none-any.whl -IntervalMinutes 15   # -Wheel optional
# danach C:\Automato\collector\agent.env mit echten Werten fuellen (Orchestrator, Agent-Token, TLS)
```

`install.ps1` legt ein venv an, installiert das Wheel (bringt `automato-collector` mit),
kopiert `run-agent.ps1` + `agent.env` und registriert die Aufgabe **Automato Collector
Agent** (SYSTEM, alle N Minuten → `automato-collector --poll`).

## 3. Verbindung prüfen

```powershell
powershell -ExecutionPolicy Bypass -File C:\Automato\collector\run-agent.ps1 -Check
```

Prüft read-only **alle diesem Agenten zugewiesenen Geräte** (eine Zeile je Gerät, Exit 0 =
alle OK, 2 = mindestens eines fehlerhaft). Ohne zugewiesene Geräte erscheint ein Hinweis.
Alte `agent.env` mit `AUTOMATO_FORTIGATE_URL/_TOKEN` (bzw. `AUTOMATO_ACI_*`) funktionieren als Rückfall weiter,
die Angaben am Quellsystem haben aber Vorrang.

## Ohne Python auf der Station (optional)

Statt venv+Wheel eine eigenständige `.exe` bauen (auf einer Windows-Build-Maschine):

```powershell
pip install . pyinstaller
pyinstaller --onefile -n automato-collector deploy\collector-agent\agent_entry.py
```

Die `dist\automato-collector.exe` ersetzt dann den venv-Aufruf in `run-agent.ps1`.
Diese Variante hat kein venv und **aktualisiert sich nicht selbst** (ADR 0011) – neue
Versionen manuell bauen und verteilen.

## Dateien

| Datei | Zweck |
|---|---|
| `install.ps1` | venv + Wheel installieren, Scheduled Task registrieren |
| `run-agent.ps1` | `agent.env` laden, `automato-collector --poll` starten |
| `agent.env.example` | Vorlage für die Umgebung (Orchestrator, Agent-Token, TLS) |
| `agent_entry.py` | PyInstaller-Entry für eine Python-freie `.exe` |

**Secrets:** Auf der Station liegt nur noch das Agent-Token (+ optional Client-Zertifikat)
in `agent.env` (nicht committen); rotierbar je Agent. Die Geräte-Tokens liegen verschlüsselt
im Orchestrator (ADR 0009) und kommen nur pro Lauf über die TLS-Verbindung zum Agenten
(FortiGate-Token ebenso wie APIC-Benutzer/Passwort).
