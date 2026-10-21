# Changelog

Alle Pakete (Orchestrator, Collector-Agent, SoT-Gateway, Web-UI) tragen dieselbe Version aus
der Datei `VERSION` (SemVer). Neue Version setzen: `python3 scripts/version.py set X.Y.Z`
(aktualisiert `pyproject.toml`, `uv.lock`, `package.json`); die CI prüft mit
`python3 scripts/version.py check`, dass alle Stellen übereinstimmen. Patch = Korrekturen,
Minor = neue Funktionen. Die Agent-Version lautet `<Release>+<Build>` – der Build ist ein
Fingerabdruck der ausgelieferten Dateien und unterscheidet auch Stände ohne neue Release-Nummer.

## 0.13.0 – 2026-10-22

Läufe-Ansicht, kompakte Ports, Portgruppen im Detail (keine Migration).

- **Läufe** (neue Ansicht unter Dokumentation, `#laeufe`): alle Collector-Läufe des Mandanten,
  seitenweise mit Gesamtzahl.
  - Filter Status (offen = wartet oder läuft, wartet, läuft, fehlgeschlagen, erfolgreich),
    Quellsystem, Plattform und Auslöser (von Hand, Zeitplan).
  - Je Lauf Ausführung, Regeln, Meldung, angelegt/begonnen/beendet mit Dauer; offene Läufe
    abbrechen (Operator).
  - `GET /jobs` nimmt dafür `status` (auch `open`), `trigger` und `offset` und liefert die
    Gesamtzahl im Header `X-Total-Count`.
- **Übersicht:** „Letzte Läufe“ zeigt immer **alle offenen Läufe** (auch ältere und bei mehr
  als zehn Geräten), dazu die letzten acht, mit Zähler „N offen“ und Link „Alle Läufe (N)“.
- **Ports kompakt:**
  - Eine Zeile je Port. Lange VLAN-Listen als Bereiche (`100-120, 300`), ab vier Bereichen nur
    die Anzahl („tagged 24 VLANs“). EPGs als Anzahl („3 EPGs“), IPs gekürzt.
  - **Klick → Detail** mit allen Angaben: Status, Beschreibung, Parent/LAG und
    Subinterfaces/Mitglieder (anklickbar), alle VLANs, IPs, Zone, Policy-Gruppe, Nachbar,
    alle EPGs.
  - In den VLANs ist die EPG-Liste gekürzt (alle im Tooltip).
- **Portgruppen im Detail** (Klick, `GET /portgroups/{id}`):
  - EPG mit Tenant, AP, BD, VRF und DN, dazu die Art der Zuordnung (Name bzw. VLAN).
  - **Subnetze** mit Quelle: ACI (EPG, BD bzw. L3Out), das Netzinventar mit gleichem VLAN (als
    Hinweis, „VLAN 1107 in …“) und das IPAM-Netz der VM-IPs. Dazu „Regeln im Subnetz“.
  - **VMs** mit Status, NIC (MAC, getrennt), IPs mit DNS-Namen und „nicht im IPAM“ sowie
    Cluster/Host.

## 0.12.0 – 2026-10-22

Paare bestätigen, VIP-Ziele, Ports je Gerät, Betriebszustand (Alembic `0035`).

- **IPv4/IPv6-Paare bestätigen bzw. ablehnen** (Operator):
  - Im Regel-Detail „Als Paar bestätigen“ (für „ähnlich“) bzw. „Kein Paar“; „zurücknehmen“
    macht es rückgängig. Angezeigt wird, wer wann entschieden hat.
  - Gilt sofort und über neue Läufe hinweg. Gespeichert wird an den Policy-IDs der FortiGate.
  - Bestätigte Paare tragen „✓ bestätigt“ (Abweichungen im Tooltip); abgelehnte werden nicht
    mehr vorgeschlagen.
  - Neuer Filter „Paar“ in den Regeln: ähnlich (offen), bestätigt, gleich, mit/ohne Paar.
- **VIP-Ziele als Objekte:**
  - Die interne Ziel-IP einer VIP ohne eigenes Adressobjekt wird ein Objekt „VIP-Ziel“
    (Mapped-IP bzw. Real Server, z. B. „VIP_web → 10.10.40.5“). Die Suche findet es.
  - Adressobjekte hinter einer VIP – per Name (FQDN-VIP `mapped-addr`, Real Server mit
    Adressobjekt) oder mit genau der Ziel-IP – tragen „Ziel von VIP …“ (Link in NAT / VIPs).
  - Beide zählen die Regeln der VIP mit und gelten nicht als unbenutzt.
- **Ports je Gerät:** Das Gerät steht als Kopfzeile (auf-/zuklappbar). Darunter die Ports,
  Subinterfaces unter ihrem Parent, LAG-Mitglieder unter dem LAG. Subinterfaces zeigen „VLAN 10
  getaggt über internal“.
- **Betriebszustand** (Ampel, `GET /system/health`): Datenbank/Migrationen, Zeitplan, zentraler
  Worker (holt er zentrale Läufe ab?), Agenten (meldet sich nicht, aktualisiert nicht),
  Quellsysteme (nie eingelesen, letzter Lauf fehlgeschlagen, Stand älter als 7 Tage),
  NetBox-Sync (fehlgeschlagen) und Sicherung (fehlt bzw. älter als 26 h).
  - Auf der System-Seite vollständig, in der Übersicht als Hinweis, sobald etwas nicht stimmt
    (mit Link in die betroffene Ansicht).

## 0.11.4 – 2026-10-22

Korrekturen aus dem Betrieb (Alembic `0034`).

- **Agent aktualisiert sich auch im Wach-Modus:** Er prüft alle 5 Minuten auf eine neue
  Version, vorher nur beim Start. Die neue Version übernimmt die restliche Wachzeit.
- **Warum ein Agent nicht aktualisiert, steht in der Agentenliste**, z. B. „Signatur fehlt“,
  „Selbst-Update auf der Station abgeschaltet“ oder „Update-Prüfung nicht möglich: …“. Der
  Agent meldet das Ergebnis der letzten Prüfung mit jedem Aufruf (`X-Agent-Update`).
- **Agent-Log:** Jede Ausgabe steht zusätzlich in
  `<Installationsordner>\logs\agent-JJJJ-MM-TT.log` (14 Tage). Die geplante Aufgabe zeigte
  bisher keine Ausgaben, Fehler waren deshalb unsichtbar.
- **FortiGate ohne JSON:** Die Meldung nennt bei HTML zusätzlich den Seitentitel (z. B. die
  Sperrseite eines Proxys dazwischen).
- **NetBox-Sync robust:**
  - Vorschau und Übernahme, die mit einem Neustart des Orchestrators gestorben sind, werden
    beim Start beendet. Bisher blieben sie für immer auf „wird übernommen“ und sperrten jeden
    weiteren Sync.
  - Ohne Lebenszeichen nach 30 min ebenfalls (`AUTOMATO_NETBOX_SYNC_TIMEOUT_MINUTES`); die
    Übernahme meldet sich je Objektart („schreibt: VLAN“).
  - **Abbrechen** für laufende Pläne (`POST /netbox-sync/plans/{id}/cancel`, auditiert). Die
    Übernahme hört vor der nächsten Objektart auf.
- **FortiGate-VLAN-Subinterfaces** werden als getaggt über den Parent eingelesen, nicht mehr
  als untagged (z. B. Subinterfaces auf „internal“). Gilt ab dem nächsten Lauf der FortiGate;
  der NetBox-Sync passt die Ports dann an.

## 0.11.3 – 2026-10-21

Korrektur: „Alle einlesen“ übersprang ACI und DDI, obwohl nichts lief (Alembic `0033`).

- **Ursache:** Ein Lauf, den ein Agent abgeholt, aber nie abgeschlossen hatte (Agent beendet
  bzw. neu gestartet, Ergebnis-Upload verloren), stand für immer auf „läuft“. Er blockierte
  „Alle einlesen“ und den Zeitplan. In der Übersicht war er nicht zu sehen (nur die letzten
  acht Läufe); „Jetzt einlesen“ prüft nicht auf offene Läufe und ging deshalb.
- **Hängende Läufe werden beendet:**
  - laufend ohne Ergebnis nach 120 min (`AUTOMATO_RUN_TIMEOUT_MINUTES`, ab dem Abholen);
  - nicht abgeholt nach 24 h (`AUTOMATO_RUN_QUEUE_TIMEOUT_HOURS`).
  - Ein später eintreffendes Ergebnis wird trotzdem eingespielt.
- **Abbrechen von Hand** (Operator): „Abbrechen“ bei offenen Läufen in der Übersicht,
  „Abbrechen & neu starten“ im Dialog „Alle einlesen“ (`POST /jobs/{id}/cancel`, auditiert).
- **Genauer Grund beim Überspringen**, z. B. „Lauf läuft seit 21.10. 09:12 (Agent
  „agent-01“)“ oder „Lauf wartet seit … auf den zentralen Worker“.
- **Agent:** Scheitert der Ergebnis-Upload (z. B. HTTP 413, Zeitüberschreitung), meldet er den
  Lauf kurz als fehlgeschlagen („Ergebnis-Upload fehlgeschlagen: …“), statt ihn offen zu
  lassen.
- Läufe tragen den Beginn (`started_at`: abgeholt bzw. vom zentralen Worker gestartet).

## 0.11.2 – 2026-10-21

Korrekturen aus dem Betrieb gegen die echten Systeme.

- **NetBox/Infoblox „Teil-Upload unvollständig (6000 von 6624)“:** Der Orchestrator hat erst
  gespeichert, nachdem die Antwort beim Agenten war. FastAPI führt den Commit einer
  Dependency sonst erst nach dem Senden aus. Der Agent schickte so das Ergebnis, bevor der
  letzte Teil-Upload in der Datenbank stand.
  - Jetzt wird bei allen Anfragen vor der Antwort gespeichert (`Depends(get_session,
    scope="function")`).
  - Ein fehlgeschlagenes Speichern kommt damit auch als Fehler beim Aufrufer an statt als
    Erfolg.
  - Ein Test prüft das für jede Route.
- **FortiGate „Expecting value: line 1 column 1 (char 0)“:** Eine FortiGate antwortete auf
  eine Abfrage mit HTTP 200, aber ohne JSON.
  - Optionale Tabellen (`firewall/policy6`, `firewall/vip6`, `firewall/ldb-monitor`,
    Systemstatus) gelten dann als nicht vorhanden; der Lauf geht weiter.
  - Bei Pflicht-Tabellen nennt die Meldung jetzt Pfad, Status und Inhalt, z. B. „FortiGate
    antwortet auf firewall/policy nicht mit JSON (HTTP 200, text/html, 18 Bytes)“.

## 0.11.1 – 2026-10-21

Korrektur aus dem ersten Lauf gegen ein echtes vCenter.

- **Fehlende vCenter-Berechtigung verständlich gemeldet:** Statt des mehrzeiligen
  `vim.fault.NoPermission` steht im Lauf z. B. „Keine Berechtigung im vCenter: dem Konto fehlt
  System.View auf vim.Folder:group-d1 (oberste Ebene des Inventars)“. Der Hinweis nennt die
  Lösung: Rolle *Read-only* auf dem vCenter-Objekt oder als globale Berechtigung, jeweils mit
  „An untergeordnete Objekte weitergeben“.
- **`--check` prüft auch das Leserecht:** Er liest wie der Lauf ab dem Wurzelordner und meldet
  die Zahl der sichtbaren VMs. Bisher prüfte er nur die Anmeldung und war deshalb grün, obwohl
  der Lauf scheiterte.
- Fix: „Anmeldung abgelehnt“ beim vCenter wurde nicht erkannt (Klassenname in pyVmomi
  `vim.fault.InvalidLogin`).

## 0.11.0 – 2026-10-21

VMware vCenter lesen, VMs in Regeln, Objekten und Doku, VMs nach NetBox Virtualization
(ADR 0017, Alembic `0032`).

- **Neue Plattform VMware** (Quellsystem mit Benutzer/Passwort, über den Agenten):
  - Gelesen werden VMs (Status, Gast-OS, Hostname, vCPU, RAM, Disk, Notiz, Vorlage), ihre
    NICs mit MAC, Portgruppe und IPs (VMware Tools), Hosts, Cluster, Datacenter und
    Portgruppen (verteilt mit VLAN/Trunk/PVLAN, Standard je Host).
  - **Nur lesen, auch im Code:** Der Collector lässt nur lesende vSphere-Methoden zu
    (Anmelden, Service-Content, Container-View, Eigenschaften abrufen). Jeder andere Aufruf
    bricht vor dem Senden ab. Das vCenter-Konto braucht die Rolle **Read-only**.
- **Portgruppe → ACI-EPG:**
  - Name nach ACI-VMM-Schema `Tenant|AP|EPG` (z. B. `TE_x|AP_007|EG_dmz02`), auch ohne
    Präfixe (`EG_db01` = `EPG_db01`).
  - Sonst das VLAN der Portgruppe, wenn es genau einer EPG gehört.
  - Die Zuordnung wird bei jedem Abgleich neu berechnet.
- **VMs in Automato:**
  - Objekte → **VMs** (Netzwerk mit Portgruppe → EPG, IPs mit DNS-Namen und „nicht im
    IPAM“, Cluster/Host, Ressourcen, „Regeln der VM“) und **Portgruppen**.
  - IPs in Regeln und Objekten zeigen die VM dahinter; die Regelsuche findet VM-Namen.
  - EPGs nennen ihre VMs.
  - Doku je System: der Server nennt seine VM und bringt die Contracts der EPG ihrer
    Portgruppe mit, auch ohne gelernten Endpunkt. Die Doku je EPG nennt die VMs.
  - Neue Befunde: **VM-IP fehlt im IPAM**, **Portgruppe nach ACI-Schema ohne EPG**.
- **NetBox-Sync, neuer Abschnitt „Virtualisierung“:**
  - Cluster-Typ „VMware vSphere“, Cluster-Gruppe je Datacenter, Cluster, VMs (Status, vCPU,
    RAM, Disk, Kommentar mit Gast-OS, Host und EPG), VM-Interfaces (MAC, Portgruppe, VLAN,
    EPG) und die IPs am VM-Interface. Vorlagen werden nicht übernommen.
  - Von Hand gepflegte IPs (NetBox als IPAM) bleiben unverändert; fehlt nur die Zuordnung
    zur VM, ist das ein Konflikt.
  - Ohne den Abschnitt bleiben VMs in NetBox unangetastet.
  - Der Sync-Benutzer braucht die Rechte für `virtualization.*`: `sync-user.sh` erneut
    ausführen.

## 0.10.0 – 2026-10-21

FortiGate Virtual Server, vollständige Doku je System bzw. Applikation, Regeln je Subnetz
(Alembic `0031`).

- **Virtual Server** (FortiGate-VIP vom Typ *server-load-balance*):
  - Erfasst werden externe IP und Port sowie alle **Real Server** mit IP bzw. Adressobjekt,
    Port, Status (aktiv/Standby/aus), Gewicht, Health-Check und maximalen Verbindungen.
  - Dazu Server-Typ, Lastverteilung, Persistenz, Health-Checks (`firewall/ldb-monitor`:
    Typ, Port, Intervall, URL) und das Zertifikat. Vom Zertifikat wird nur der Name gelesen.
  - NAT-Sicht: Typ „Virtual Server“, Real Server mit Firewall-Objekt und DNS-Namen.
  - Suche über Real-Server-IP, Server, Methode und Health-Check.
  - Regeln mit dem Virtual Server treffen auch über jeden Real Server (IP-Suche, Doku je
    System).
- **IPv6-VIPs** (`firewall/vip6`) werden gelesen, auch als Virtual Server. `dstaddr6`-Verweise
  lösen gegen sie auf.
- **Doku je System und je Applikation erfasst alle Regeln.** Sie erscheinen gleichwertig;
  die neue Spalte **„Bezug“** sagt, wie die Regel greift.
  - Firewall-Regeln direkt sowie über Netze, Bereiche, Gruppen und any, für Server (Host-IP)
    und für EPGs (Subnetze und Endpunkte der EPG).
  - ACI-Contracts direkt, über **vzAny** der VRF und über **ESGs**, die die EPG einschliessen.
  - Ein Server bringt die Contracts seiner EPG mit (gelernter Endpunkt).
- **Regeln je Subnetz:**
  - Objekte → Subnetze → „Regeln im Subnetz“ zeigt alle Regeln, deren Quelle oder Ziel sich
    mit dem Subnetz überschneidet. any lässt sich ein- und ausblenden.
  - Ein Netz (CIDR) im Suchfeld der Regeln trifft genauso.

## 0.9.0 – 2026-10-21

IPv4/IPv6 getrennt, Regel-Zahl über Gruppen, grosse Regelwerke schnell (ADR 0016, Alembic `0030`).

- **IPv4- und IPv6-Objekte gleichen Namens** sind zwei Objekte, wie auf der FortiGate
  (`address`/`addrgrp` bzw. `address6`/`addrgrp6`):
  - Werte, Gruppen, Regel-Zahl und Befunde je Familie; Befunde zu IPv6-Objekten heissen
    „Name (IPv6)“.
  - Policies lösen `srcaddr`/`dstaddr` gegen IPv4, `srcaddr6`/`dstaddr6` gegen IPv6 auf.
  - `firewall/policy6` (FortiOS 6.x) wird mitgelesen, wo es die Tabelle gibt.
- **Familie je Regel** (IPv4, IPv6, IPv4+IPv6) und Filter „Familie“.
- **IPv4/IPv6-Paare** werden nur markiert, beide bleiben eigene Regeln:
  - „↔ IPv6 #…“ (grün = deckt dasselbe ab, gelb = ähnlich, mit Abweichungen im Tooltip).
  - Klick öffnet das Gegenstück; beim Überfahren wird es hervorgehoben.
- **Regel-Zahl bei Objekten** zählt die Nutzung direkt und über (verschachtelte) Gruppen.
  - Sie stimmt mit dem Objekt-Detail überein.
  - Der Link öffnet genau dieses Objekt (`#regeln?object_id=…`).
- **Grosse Regelwerke** (gemessen: 3000 Regeln, 9500 Objekte, 39 000 Endpunkte):
  - Regelliste 0,9 → 0,07 s, Suche 3,5 → 0,04 s, IP-Suche 4,1 → 0,1 s.
  - Objekt-Detail 8,7 → 0,05 s, Doku je System 2,2 → 0,04 s.
  - Einlesen 216 → 25 s.
  - Möglich macht das ein abgeleiteter Regelindex (Suchtext, Objekt-Nutzung, IP-Bereiche),
    beim Einlesen berechnet. Dazu ein Zwischenspeicher je Lauf und seitenweise Abfragen in
    der Datenbank.
- **Nach dem Update:** Den Index baut die erste Anfrage einmalig auf. Die getrennten
  IPv6-Objekte und Familien kommen mit dem nächsten Lauf eines Agenten ab 0.9.0
  („Alle einlesen“).

## 0.8.1 – 2026-10-20

Tests und CI ausgebaut (`docs/TESTS.md`), zwei Korrekturen (Alembic `0029`).

- **UI-Tests im Browser (Playwright, CI-Job `e2e`):**
  - Jede Ansicht lädt ohne Seiten- und API-Fehler.
  - Kernabläufe: Suche über Gruppenmitglieder und IPAM-Namen, SNAT/VIP ↔ NAT ↔ Regeln,
    Findings mit Link, „Regel nicht wirksam“, Dienste mit Detail, Standorte, „Alle einlesen“.
  - Beispieldaten aus den Orchestrator-Tests (`web-ui/frontend/e2e/`); lokal `npm run e2e`.
- **`alembic check`** im Postgres-Job: Modell und Migrationen müssen übereinstimmen.
- **Mindestabdeckung 90 %** je Python-Paket (Stand 94/95/94 %).
- **PowerShell-Syntaxprüfung** der Agent-Skripte (`scripts/check_powershell.ps1`).
- **Images bauen** (Orchestrator, Collector, Web-UI) und Compose-Konfiguration aller Profile
  prüfen (Job `docker`).
- **Nächtlicher Lauf** aller Jobs und Start von Hand (*Run workflow*).
- **Fix:** `agent.created_at` war in der Datenbank nullable, im Modell nicht (gefunden von
  `alembic check`).
- **Fix NAT:** Ist die interne IP in mehreren Firewall-Objekten definiert, zeigt die NAT-Sicht
  alle als Server (bevorzugt die derselben Firewall), nicht nur das alphabetisch erste.

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
