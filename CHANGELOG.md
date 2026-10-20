# Changelog

Alle Pakete (Orchestrator, Collector-Agent, SoT-Gateway, Web-UI) tragen dieselbe Version aus
der Datei `VERSION` (SemVer). Neue Version setzen: `python3 scripts/version.py set X.Y.Z`
(aktualisiert `pyproject.toml`, `uv.lock`, `package.json`); die CI prüft mit
`python3 scripts/version.py check`, dass alle Stellen übereinstimmen. Patch = Korrekturen,
Minor = neue Funktionen. Die Agent-Version lautet `<Release>+<Build>` – der Build ist ein
Fingerabdruck der ausgelieferten Dateien und unterscheidet auch Stände ohne neue Release-Nummer.

## 0.8.0 – 2026-10-20

Dev-Befunde: NetBox-Sync, Regeln ohne Quelle/Ziel, Dienste (Alembic `0028`).

- **Fix NetBox-Sync** („duplicate key … ipam_vlan_unique_group_name“): Mehrere VLANs einer
  ACI-EPG trugen denselben Namen. NetBox verlangt eindeutige Namen je VLAN-Gruppe (Sites
  global). Doppelte Namen bekommen jetzt die VID bzw. den Code angehängt, z. B.
  „EG_web (110)“. Fällt die Dublette weg, gilt beim nächsten Sync wieder der schlichte Name.
- **Regeln ohne Quelle/Ziel:**
  - FortiGate: Internet Services (ISDB) als Ziel bzw. Quelle, Benutzer/Gruppen/FSSO und
    ZTNA-Tags als Quelle werden gelesen. Negierte Felder sind als „nicht (negiert)“
    gekennzeichnet.
  - ACI: Contract Master (geerbte Contracts) und Intra-EPG-Contracts füllen Consumer und
    Provider.
  - Was danach noch ohne Quelle/Ziel bleibt, ist gekennzeichnet („kein Consumer · nicht
    wirksam“) und ein Befund **„Regel nicht wirksam“** (ACI: Info).
- **Dienste wie Adressobjekte:**
  - FortiGate-Services mit Protokoll, Ports inkl. Quellports, ICMP-Typ/-Code, IP-Protokoll,
    Kategorie, Timern und Ziel-Beschränkung.
  - **ACI-Filter** als Service-Objekte `<Tenant>/FT_…` mit ihren Einträgen; Contracts
    verweisen darauf (Dienste-Spalte, Suche, Nutzung).
  - **Prüfung nach Namensschema:** Präfix `FT_` (erweiterbar über das Wörterbuch
    `service_prefix`) und Port passend zum Namen – laut Wörterbuch `service` bzw. Schema
    `FT_<proto><port>` (neuer Befund **„Service ≠ Name“**). FortiOS-Standardservices sind
    ausgenommen und zählen nicht als unbenutzt.
- **Objekt-Detail wie bei NAT** (`GET /objects/{id}`): Attribute, Werte, Mitglieder, Befunde und
  die Regeln, die das Objekt nutzen (auch über Gruppen), mit „In Regeln anzeigen“; Klick auf
  eine Regel öffnet sie (`#regeln?rule=<id>`). Gilt für Services wie für Adressobjekte.

**Hinweis:** Die neuen Felder liest erst der aktualisierte Collector bzw. Agent (Selbst-Update).
Danach FortiGate und APIC neu einlesen.

## 0.7.0 – 2026-10-20

Virtuelle und physische Standorte (ADR 0015, Alembic `0027`).

- **Zwei Arten Standort:** *virtuell* = dreistellige Nummer der Namenskonvention (`000` z. B.
  Stretched DC, `001` …), *physisch* = Ort-Code des Rechenzentrums (z. B. `chzrh1`). Ein
  virtueller Standort kann an mehreren physischen laufen (Zuordnung n:m, „Zuordnen“).
  - Bestand bleibt: Ziffern-Codes werden virtuell, andere (z. B. `ZRH`) physisch; alle
    Verortungen bleiben erhalten.
  - Standort-Auswahl (Quellsystem, Doku je System, Applikation) nach Art gruppiert, Anzeige
    mit Zuordnung („000 · Stretched DC (chzrh1, chbsl1)“).
- **Standort aus dem Namen (Vorschlag):** Netz-Objekte (`N_<tenant>-000-…`, `T_`, `IPP_`),
  BD-/Subnetz-Namen, Inventar-Geräte und Quellsysteme (`fwc11-000-<tenant>`,
  `evh01-chzrh1-<tenant>`) zeigen den erkannten Standort; „?“ = Code nicht erfasst. VMs tragen
  keinen Standort im Namen und werden nicht ausgewertet.
- **NetBox-Sync:** Standorte als NetBox-Sites in den Standort-Gruppen *Virtuelle Standorte* und
  *Physische Standorte*, Kommentar mit der Zuordnung. Gruppe/Kommentar nur an eigenen Sites
  (Tag `automato`); von Hand gepflegte Sites werden referenziert, ohne Konflikt. Gerät ohne
  Standort am Quellsystem: der einzige physische Standort.

**Hinweis NetBox:** Das Schreib-Token braucht zusätzlich das Recht auf *DCIM › Site Group*
(Permission `automato-sync-write` ergänzen oder `source-of-truth/netbox/sync-user.sh` erneut
ausführen – erzeugt einen neuen Token, ausser `SYNC_TOKEN` ist gesetzt).

## 0.6.0 – 2026-10-20

Dev-Befunde aus dem Test gegen die echten Systeme: Suche, NAT, Findings, Namensabgleich,
„Alle einlesen“ und ein reaktiver Agent. Keine Schemaänderung.

- **Regelsuche findet alles:** Regel (Name, ID, Kommentar, Zonen, Aktion, Schutzprofile),
  Objekte **inkl. verschachtelter Gruppenmitglieder**, Services, Werte, DNS-Namen aus dem
  IPAM, EPG/AP/Tenant/BD/VRF und NAT. Eine IP trifft auch, wenn sie in einem Netz/Bereich der
  Regel liegt („any“ ausgenommen); mehrere Wörter müssen alle vorkommen. Neue exakte Filter
  `object` (Regeln mit dem Objekt, auch über Gruppen) und `nat`; Zonen-/Aktionsfilter
  serverseitig über alle Regeln.
- **NAT in den Regeln:** Kennzeichen **SNAT** (Pool bzw. Interface-IP) und **VIP** je Regel,
  im Detail ein NAT-Abschnitt mit IPs, Ports und DNS-Namen; Klick führt in die NAT-Sicht.
- **NAT / VIPs detailliert:** DNS-Namen beider IPs aus DDI/NetBox, Server hinter der internen
  IP, Interface, Kommentar, Regelzahl; Suche (Name, IP auch im Pool-Bereich, Port, DNS-Name,
  Server); Detail mit Einstellungen, IPAM-Sicht und den Firewall-Regeln, „In Regeln anzeigen“.
- **Verlinkung:** Objekte in Regel-Detail, Objekt-Liste und Findings führen zu allen Regeln mit
  dem Objekt; NAT-Einträge zu ihren Regeln und zurück (`#regeln?object=…`, `#regeln?nat=…`).
- **Findings:** Übersicht nach Kategorien (IPAM-Abgleich, Namenskonvention, Bereinigung, ACI)
  mit Zählern je Typ; je Befund ein Satz, **was genau nicht stimmt**, und ein Hinweis, was zu tun
  wäre. „Nicht im IPAM“ nennt Firewall-Objekte, Regeln und – wenn das IPAM den Server unter
  seinem Namen mit anderer IP kennt – diese IP; mehrdeutige Namen nennen die Kandidaten.
- **IPAM-Name ohne Präfix:** Im IPAM steht der Server ohne Präfix (`web01.example.ch`), auf
  Firewall/ACI mit Präfix (`H_web01`). Der Abgleich sucht ohne Präfix und über den Kurznamen
  (nur wenn eindeutig); Regel- und Objektsuche finden über den IPAM-Namen.
- **Alle einlesen:** Übersicht → „Alle einlesen“ bzw. `POST /jobs/collect-all` – ein Lauf je
  aktivem Quellsystem des Mandanten, laufende und nicht lesbare werden mit Grund übersprungen.
- **Agent reaktiver:** `automato-collector --poll --watch 20 --for 14` bleibt zwischen den
  Starts der geplanten Aufgabe wach und holt neue Läufe nach Sekunden statt nach bis zu 15 min.
  `run-agent.ps1` nutzt das standardmässig (`-Once` = einmal holen); `install.ps1` setzt
  `IgnoreNew`, damit nie zwei Agenten parallel laufen. Bestehende Agenten übernehmen den
  Wach-Modus mit dem nächsten Selbst-Update.

## 0.5.0 – 2026-10-20

Applikationen, zeitgesteuerte Läufe, ACI-Lücken und Produktions-Gate (Alembic `0024`–`0026`).

- **Applikationen (ADR 0012):** eigenes Objekt mit Kürzel, Verantwortlichen, Kritikalität,
  Standort und Mitgliedern: Application Profiles (`Tenant/AP`), einzelne EPGs, Server.
  - **Vorschläge** nach der Projektnummer aus `AP_<nr>` bzw. `H_<funktion>-<nr>-…`, mit einem
    Klick übernehmen bzw. ergänzen.
  - **Doku je Applikation** über alle Systeme mit Markdown-Export und Druckansicht (PDF über
    den Browser).
  - Die VIP-Sicht heisst jetzt **Publizierte Dienste** (`/published-services`).
- **EPG-Lage:** Leaf-Ports (statische Pfade, LLDP/CDP-Nachbar) und VLANs der EPG.
- **ACI:** **ESGs** (Selektoren EPG/Tag/IP) als Consumer/Provider, **Taboo-Contracts** als
  Deny-Regeln auf die geschützten EPGs, **Preferred Groups** als eine Regel je VRF. Nach dem
  Update den APIC neu einlesen.
- **Zeitgesteuerte Läufe:** Zeitplan je Quellsystem (Vorlagen oder Cron, Zeitzone
  Europe/Zurich); „Letzte Läufe“ kennzeichnet sie mit „Zeitplan“.
  - Ein Termin wird auch bei mehreren Orchestrator-Prozessen nur einmal ausgelöst.
  - Läuft oder wartet schon ein Lauf, fällt der Termin aus.
- **NAT und Findings seitenweise** mit Gesamtzahl; Zähler in Übersicht und Navigation aus
  Summary-Endpunkten statt aus gekappten Listen.
- **Produktions-Gate (ADR 0013):**
  - **mTLS-Agent-Gateway** (`deploy/agent-gateway`, `AUTOMATO_INTERNAL_MTLS_MODE=require|bind`),
  - **signierte Agent-Updates** (Ed25519, offline signiert),
  - **Sicherung/Wiederherstellung** mit age (`deploy/backup`),
  - **`deploy/update.sh`** mit Rückfall auf Code und Datenbank,
  - **R16-Prüfung** `automato-collector --check --strict`,
  - neue Admin-Ansicht **System**.
- **Fix Agent-mTLS:** Mit gepinnter CA legte der Agent sein Client-Zertifikat nicht vor (httpx
  0.28 ignoriert `cert=` neben `verify=<pfad>`); jetzt ein gemeinsamer SSL-Kontext.
- **CI:** Migrationen hoch/runter und alle Orchestrator-Tests zusätzlich gegen Postgres 16.
- **Doku:** CLAUDE.md gestrafft (Verlauf bis 0.4.2 in `docs/ENTWICKLUNGSVERLAUF.md`),
  ROADMAP-Status, ADR 0012–0014 (0014 = Entwurf gemeinsames Cockpit mit Magister).

**Hinweis Agenten:** Das Update auf 0.5.0 bringt die neue Abhängigkeit `cryptography`. Die
Station braucht dafür einmalig PyPI-Zugang oder ein Wheelhouse. Ohne Zugang bleibt der Agent
auf seiner Version und meldet den pip-Fehler.

## 0.4.2 – 2026-10-20

- **ACI-Zone je Contract-Seite (Dev-Befund):** Die Zone kam vom *ersten* Consumer bzw.
  Provider. Konsumiert eine externe EPG (z. B. VPN hinter `L3O_vpn`) viele Contracts zusammen
  mit Anwendungs-EPGs, trug dadurch fast jeder Contract „L3O_vpn“. Jetzt hat eine Seite nur
  dann einen L3Out als Zone, wenn **alle** ihre Endpunkte externe EPGs desselben L3Outs sind;
  gemischte Seiten haben keine Zone, die externe EPG steht als `L3O_vpn/<EPG>` in der
  Consumer-/Provider-Liste. Nach dem Update den APIC neu einlesen.

## 0.4.1 – 2026-10-20

- **ACI-Zonen korrigiert (Dev-Befund):** Contracts zwischen EPGs zeigten einen L3Out als Quell-/
  Ziel-Zone. Ursache: die Bindung einer Bridge Domain an einen L3Out (`fvRsBDToOut`) wurde als
  Zone gewertet – sie bedeutet aber nur, dass die Subnetze der BD nach aussen bekannt gegeben
  werden. Ohne L3Out fiel die Zone zudem auf den BD- bzw. EPG-Namen zurück. Jetzt hat nur eine
  **externe EPG** eine Zone (ihren L3Out); EPG zu EPG hat keine. Im Detail steht die Zeile nur
  noch, wenn eine Seite extern ist („L3Out“).
- **Applikations-Doku:** Contracts mit externer Gegenstelle landen nach dem L3Out in den
  Tabellen 1–6/9 (z. B. `L3O_inet` → „Zugriff aus dem Internet“) statt in 7/8/9.

Nach dem Update den APIC einmal neu einlesen – die Zonen der bestehenden Contracts werden
dabei ersetzt.

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
zentralen Geräte-Zugangsdaten (ADR 0001–0009). Keine einzelnen Release-Nummern; der
ausführliche Verlauf steht in `docs/ENTWICKLUNGSVERLAUF.md`.
