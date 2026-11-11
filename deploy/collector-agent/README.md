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
enthält deshalb nur Orchestrator-Adresse + Agent-Token (+ Zertifikatszeilen). Ein Gerät lässt
sich jederzeit einem anderen Agenten zuweisen, ohne an der Station etwas zu ändern.

**Zugang aus dem Web-UI** (1.6.0, ADR 0052): `agent.env`, Agent-Token und – mit Agent-Gateway –
Client-Zertifikat, Schlüssel und CA kommen als ein ZIP (**Collector-Agenten → Zugang →
Zugang herunterladen**). Von Hand ausfüllen muss man nichts; jeder Download erneuert Token und
Zertifikat.

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

## Schnellweg: Paket und Zugang aus dem Web-UI (empfohlen)

Im Web-UI unter **Collector-Agenten** steht die Einrichtung Schritt für Schritt: Agent
anlegen, Geräte zuweisen, das **Agent-Paket** (ZIP: collectors- und sot-gateway-Wheel + Skripte,
ohne Zugangsdaten) und den **Zugang** des Agenten (ZIP: `agent.env`, ggf. `agent.crt`,
`agent.key`, `agents-ca.crt` bzw. `automato-ca.crt`) herunterladen. Das Orchestrator-Image baut
das Paket mit (Dockerfile-Stage `agent-package`). Auf der Station:

```powershell
cd $HOME\Downloads\automato-collector-agent     # Paket entpackt, Inhalt des Zugangs daneben gelegt
Get-ChildItem | Unblock-File
powershell -ExecutionPolicy Bypass -File .\install.ps1 -IntervalMinutes 15
powershell -ExecutionPolicy Bypass -File C:\Automato\collector\run-agent.ps1 -Check
```

`install.ps1` findet das Wheel neben sich und übernimmt `agent.env` und die Zertifikatsdateien
nach `C:\Automato\collector` (mit anderem `-InstallDir` passt es die Pfade in der `agent.env`
an). **Bestehender Agent:** nur den Inhalt des Zugangs nach `C:\Automato\collector` kopieren
(`agent.env` wird ersetzt) und `run-agent.ps1 -Check` ausführen – keine Neuinstallation nötig.
`agent.env` und `agent.key` nur auf dieser Station ablegen; den Ordner nur SYSTEM und
Administratoren lesen lassen.

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
# ohne Zugang daneben: danach den Zugang aus dem Web-UI nach C:\Automato\collector kopieren
```

**TLS intern (0.78.2):** Ist auf dem Server `AUTOMATO_INTERNAL_TLS=on`, spricht der
Orchestrator auf Port 8000 nur noch HTTPS. Ohne Agent-Gateway schreibt der Zugang dann
`https://<host>:8000` in die `agent.env` und legt die interne CA (`automato-ca.crt`, nur das
öffentliche Zertifikat) samt `AUTOMATO_ORCHESTRATOR_CA_BUNDLE` bei. Mit `http://` beendet der
Orchestrator die Verbindung ohne Antwort („RemoteProtocolError: Server disconnected without
sending a response“) – dann einen neuen Zugang herunterladen. Über das Agent-Gateway (Vorgabe
`:8443`, Port nach `AUTOMATO_AGENT_GATEWAY_PORT`) ändert sich nichts.

`install.ps1` legt ein venv an, installiert das Wheel (bringt `automato-collector` mit),
kopiert `run-agent.ps1` + `agent.env` und registriert die Aufgabe **Automato Collector
Agent** (SYSTEM, Start alle N Minuten → `automato-collector --poll --watch 20 --for N-1`).
Der Agent bleibt dazwischen wach und fragt alle 20 s (`-WatchSeconds`) nach Läufen – ein
„Jetzt einlesen“ wird so nach Sekunden abgeholt. Jeder Neustart prüft auf ein Selbst-Update;
läuft der Agent noch (langer Lauf), lässt die Aufgabe den nächsten Start aus (`IgnoreNew`).
Bereits installierte Agenten übernehmen den Wach-Modus mit dem nächsten Selbst-Update
(neues `run-agent.ps1`). Von Hand einmal abholen: `run-agent.ps1 -Once`.

## 3. Verbindung prüfen

Im Web-UI (seit 1.7.0): **Mandanten & Standorte → Quellsysteme → Prüfen** beim Gerät. Der Agent
prüft beim nächsten Abruf (im Wach-Modus spätestens nach 20 s) Name, Verbindung und Anmeldung
read-only und meldet das Ergebnis; die Seite zeigt es, sobald es da ist. Meldet er sich nicht
(geplante Aufgabe aus, Agent älter als 1.7.0), sagt die Seite das nach drei Minuten. Auf der
Station selbst, für alle Geräte des Agenten:

```powershell
powershell -ExecutionPolicy Bypass -File C:\Automato\collector\run-agent.ps1 -Check
```

Prüft read-only **alle diesem Agenten zugewiesenen Geräte** (eine Zeile je Gerät, Exit 0 =
alle OK, 2 = mindestens eines fehlerhaft). Ohne zugewiesene Geräte erscheint ein Hinweis
(Exit 2). Geräte-Zugangsdaten in der `agent.env` (`AUTOMATO_FORTIGATE_URL/_TOKEN`,
`AUTOMATO_ACI_*`) liest der Agent seit 1.6.0 nicht mehr – sie gehören ans Quellsystem im Web-UI
und können aus alten `agent.env` gelöscht werden (ein neuer Zugang enthält sie nicht).
Typische Meldungen: `403 agent channel requires the mTLS gateway` (Adresse zeigt auf Port 8000
statt auf das Gateway – neuen Zugang laden), `certificate required` (Zertifikatszeilen fehlen),
`403 client certificate does not belong to this agent` (Zertifikat eines anderen Agenten).

**Sprache (0.90.0):** `AUTOMATO_LANG=en` in der `agent.env` stellt die Konsole und das Log des
Agenten auf Englisch um (Standard Deutsch). Was der Agent an den Orchestrator meldet (Meldung des
Laufs, Grund der Update-Prüfung), bleibt deutsch gespeichert und erscheint im Web-UI in der dort
gewählten Sprache.

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
| `agent.env.example` | Vorlage der `agent.env` – im Normalfall kommt sie fertig aus dem Zugang (Web-UI) |
| `agent_entry.py` | PyInstaller-Entry für eine Python-freie `.exe` |

**Secrets:** Auf der Station liegen nur das Agent-Token in `agent.env` und – mit Gateway – der
Schlüssel `agent.key` (nicht committen); rotierbar je Agent über einen neuen Zugang. Die Geräte-Tokens liegen verschlüsselt
im Orchestrator (ADR 0009) und kommen nur pro Lauf über die TLS-Verbindung zum Agenten
(FortiGate-Token ebenso wie APIC-Benutzer/Passwort).
