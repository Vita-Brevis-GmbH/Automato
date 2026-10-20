# Changelog

Alle Pakete (Orchestrator, Collector-Agent, SoT-Gateway, Web-UI) tragen dieselbe Version aus
der Datei `VERSION` (SemVer). Neue Version setzen: `python3 scripts/version.py set X.Y.Z`
(aktualisiert `pyproject.toml`, `uv.lock`, `package.json`); die CI prüft mit
`python3 scripts/version.py check`, dass alle Stellen übereinstimmen. Patch = Korrekturen,
Minor = neue Funktionen. Die Agent-Version lautet `<Release>+<Build>` – der Build ist ein
Fingerabdruck der ausgelieferten Dateien und unterscheidet auch Stände ohne neue Release-Nummer.

## 0.4.0 – 2026-10-20

Wo liegen Consumer und Provider eines ACI-Contracts? (Alembic `0023`)

- **Lage je Consumer/Provider:** Das Detail eines ACI-Contracts zeigt pro Seite eine Karte mit
  ACI-Tenant, Applikationsprofil, Bridge Domain, VRF bzw. L3Out, den Subnetzen (EPG, BD,
  extern) samt passendem IPAM-Netz der DDI/NetBox (Beschreibung, VLAN, Standort), den gelernten
  Endpunkten mit DNS-Namen aus der Referenzquelle und dem verknüpften Server. In der Liste
  stehen Consumer/Provider als `AP/EPG` bzw. `L3Out/EPG`, mit ACI-Tenant davor, wenn er vom
  Contract abweicht.
- **ACI-Parser tenant-bewusst:** Identität per DN statt Name – gleichnamige Contracts, EPGs,
  BDs und Filter in mehreren ACI-Tenants werden nicht mehr zusammengelegt. Namensverweise wie
  im APIC: eigener Tenant, sonst `common`. Neu als Consumer/Provider: **externe EPGs**
  (L3Out, Subnetze), **vzAny** und exportierte Contracts über **Contract-Interfaces**;
  Filter nicht-reflexiver Subjects (`vzInTerm`/`vzOutTerm`).
- **Store:** EPG mit Art, ACI-Tenant, DN, VRF, L3Out und Quellsystem, Subnetze mit Herkunft.
  Bestehende EPGs (nur Name) ersetzt der nächste ACI-Lauf; veraltete EPGs eines APICs ohne
  Regelbezug fallen weg, ebenso beim Löschen des Quellsystems.
- **Applikations-Doku:** gleichnamige EPGs heissen „EG_x · Tenant · AP“; Gegenstelle in einem
  anderen ACI-Tenant → Tabelle 9. Behoben: die Doku berücksichtigte nur die ersten 500 Regeln.

## 0.3.0 – 2026-10-20

ACI-Contracts in der Regel-Ansicht.

- **Regeln & Contracts:** Die Ansicht (vorher „Firewall-Regeln“) zeigt FortiGate-Policies und
  ACI-Contracts. Neuer Filter **Art** (Firewall-Regeln / ACI-Contracts). ACI-Zeilen tragen das
  Kennzeichen „ACI“ mit Contract, Subject, ACI-Tenant und Scope; Quelle/Ziel heissen dort
  **Consumer**/**Provider**. Der Detail-Drawer zeigt Contract, Subject, Tenant, Scope, DN und
  die Consumer-/Provider-EPGs.
- **Keine Kappung mehr bei 500:** `/rules` lieferte höchstens 500 Regeln, sortiert nach
  Quellsystem – bei einer FortiGate mit vielen Policies fielen die ACI-Contracts heraus, und
  die Suche fand sie nicht. Jetzt filtert der Orchestrator nach Art, System und Suche über
  alle Regeln, liefert seitenweise (`limit`/`offset`, Gesamtzahl im Header `X-Total-Count`),
  das UI lädt mit „weitere … laden“ nach.
- **Übersicht:** Kennzahlen kommen aus dem neuen `GET /rules/summary` (alle Regeln statt der
  ersten 500), aufgeteilt in Firewall und ACI.

## 0.2.3 – 2026-10-20

- **ACI-Einspielen:** Lauf scheiterte mit `duplicate key … rule_service_pkey`, wenn mehrere
  Filter-Einträge eines Contracts denselben Service ergeben (z. B. `https` und `443`, Einträge
  nur mit anderem Quell-Port). Jeder Service zählt jetzt einmal je Regel – gilt für alle
  Plattformen.

## 0.2.2 – 2026-10-20

Diagnose bei abgelehnter Anmeldung am Gerät.

- **Collector:** Lehnt ein Gerät die Anmeldung ab (HTTP 401/403), nennen `--check` und die
  Lauf-Meldung jetzt den Fehlertext des Geräts (APIC z. B. „FAILED local authentication“) und
  einen Hinweis je Plattform (APIC: Remote-Benutzer als `apic#<Login-Domäne>\<Benutzer>`;
  Infoblox: API-Recht der Admin-Gruppe; FortiGate: Trusted Hosts). Nur lokal bei `--check`
  zusätzlich der verwendete Benutzer und Hinweise zum Passwort (Leerzeichen am Rand,
  Nicht-ASCII-Zeichen) – das Passwort selbst wird nie ausgegeben.
- **Web-UI:** Zugangsdaten-Felder der Quellsysteme werden nicht mehr vom Browser oder
  Passwort-Manager automatisch befüllt (sonst konnte ein gespeichertes fremdes Login beim
  Bearbeiten unbemerkt als neue Gerätezugangsdaten gespeichert werden).

## 0.2.1 – 2026-10-20

Korrekturen am Agent-Paket.

- **Agent-Paket:** Das sot-gateway-Wheel aus dem Orchestrator-Image war leer (der Docker-Bau
  kopierte nur `pyproject.toml`) – pip meldete „Successfully installed sot-gateway“, der Agent
  startete danach mit `No module named 'sot_gateway'`. Der Bau kopiert jetzt den Quellcode und
  bricht ab, wenn ein Wheel sein Modul nicht enthält (`scripts/check_agent_package.py`); die CI
  baut die Docker-Stage `agent-package` mit.
- **Selbst-Update:** `run-agent.ps1` wird als neue Datei abgelegt statt überschrieben – die
  Sperrmarkierung der alten Datei (Sicherheitswarnung beim Start) fällt dabei weg.
- **install.ps1:** zeigt den gefundenen Python-Starter korrekt an (statt `System.Object[]`).

Bereits installierte 0.2.0-Agenten reparieren sich beim nächsten Lauf selbst.

## 0.2.0 – 2026-10-20

Erste versionierte Ausgabe.

- **Phase A (ADR 0010):** Firewall-Objekte mit Namen und Regel-Verweisen, Netzinventar aus
  FortiGate und APIC, Infoblox DDI und NetBox als Referenzquellen (nur lesend), Abgleich
  Firewall/ACI ↔ IPAM und DDI ↔ NetBox, Ansichten „Objekte & Inventar“ und „Abgleich“,
  NetBox-Sync mit Vorschau und Übernahme per Klick (einziger Schreibweg).
- **Agent-Selbstupdate (ADR 0011):** Agenten aktualisieren sich über ihren Agent-Kanal, prüfen
  SHA-256, reparieren eine unvollständige Installation selbst; Schalter je Agent im UI.
- **Versionierung:** `VERSION` + `scripts/version.py`, Version in `/health`, UI-Fusszeile,
  Agentenliste und `automato-collector --version`.
- **Agent-Installation:** `install.ps1` entsperrt die Dateien (keine Sicherheitswarnung mehr),
  hält die geplante Aufgabe an, installiert collectors- und sot-gateway-Wheel sichtbar und
  prüft am Ende, dass der Agent startet.
- **Robustheit:** NetBox ohne Lese-Token wird beim Einlesen abgelehnt; Lauf-Fehler stehen mit
  Ursache im Log und in „Letzte Läufe“; unbekannte Plattform → Lauf `failed` statt Absturz.

## 0.1.0

Stand bis einschliesslich UC1 Model 2, ACI (Model 1), Keycloak/RBAC, externe Agenten mit
zentralen Geräte-Zugangsdaten (ADR 0001–0009). Keine einzelnen Release-Nummern.
