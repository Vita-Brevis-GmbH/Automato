# Changelog

Alle Pakete (Orchestrator, Collector-Agent, SoT-Gateway, Web-UI) tragen dieselbe Version aus
der Datei `VERSION` (SemVer). Neue Version setzen: `python3 scripts/version.py set X.Y.Z`
(aktualisiert `pyproject.toml`, `uv.lock`, `package.json`); die CI prüft mit
`python3 scripts/version.py check`, dass alle Stellen übereinstimmen. Patch = Korrekturen,
Minor = neue Funktionen. Die Agent-Version lautet `<Release>+<Build>` – der Build ist ein
Fingerabdruck der ausgelieferten Dateien und unterscheidet auch Stände ohne neue Release-Nummer.

## 0.110.0 – 2026-10-29

Findings als steckbare Prüfungen und Oberfläche je Modul (Roadmap Version 1 Nr. 1, Stufe 1;
gewählt „Prüfungen + Pakete schrittweise“; ADR 0039). Abnahme bis 0.109.1.

- **Je Prüfung eine Datei** im Paket ihres Moduls (`orchestrator/app/checks/<modul>/`): fünfzehn
  Prüfungen aus `store/reconcile.py` umgezogen (Kern: Firewall-Objekte und Namenskonvention,
  Regeln ohne Quelle/Ziel, SNAT-Pool, ACI-Scope, Contract auf beiden Seiten, EPG-Zuordnung;
  Analyse: Regel-Endpunkte im IPAM, IPAM-Abgleich, VMware, Topologie, Verkabelung, Verbund, Stand
  der Quellsysteme; Design: Zonen; Doku: Verbindungen der Applikationen). Der Abgleich findet sie
  selbst; eine neue Prüfung ändert `reconcile.py` nicht mehr. Befunde bleiben gleich.
- **Seite Findings → Reiter „Prüfungen“:** je Prüfung Zweck, Modul, Befund-Typen (Klick zeigt die
  Befunde), wann sie läuft, Dauer und Anzahl des letzten Durchgangs, Summe; „Neu abgleichen“
  (Operator).
- **Je Mandant schaltbar** (Admin, Audit `check.switched`, Migration `0068`): aus = die Prüfung
  läuft nicht, ihre Befunde fallen beim nächsten Abgleich weg; wieder an = sie kommen mit Status
  zurück. Ist ihr Modul aus, läuft sie auch nicht („Modul aus“).
- **Oberfläche je Modul:** die Ansichten stehen im Ordner ihres Moduls
  (`web-ui/frontend/src/modules/<modul>/`), Navigation und Anzeige baut die Shell aus dem
  Verzeichnis; `tests/test_modules.py` prüft Ordner und Pakete gegen das Verzeichnis der Module.
- Tests: `test_checks.py`, Ordner-Prüfung in `test_modules.py`, UI-Test `e2e/checks.spec.ts`.

## 0.109.1 – 2026-10-29

Korrekturen (Fehlerliste des Anwenders 2026-10-29) und neue Roadmap nach Versionen. Abnahme bis
0.109.0.

- **NetBox-Lauf bleibt „wartet“:** Redis hält die Warteschlange zum zentralen Worker ohne
  Datenträger – nach einem Neustart bzw. Update waren wartende Aufträge weg und der Lauf blieb
  bis zur Frist (24 h) stehen. Jetzt schickt der Zeitplan zentral gelesene Läufe nach
  `AUTOMATO_RUN_REDISPATCH_MINUTES` (5) erneut; gelesen wird trotzdem nur einmal (Claim beim
  Abholen des Ziels, `X-Run-Claim`). Die Liste der Läufe sagt, wer abholen muss (Agent mit
  letztem Lebenszeichen bzw. zentraler Worker, „erneut geschickt“).
- **Prüfung „Zentraler Worker“** in System → Verbindungen und `deploy/aufbau.sh pruefen`:
  antwortet er (Ping), Aufträge in der Warteschlange, wartende Läufe – mit Befehl zum Starten
  und den Logs.
- **Oberfläche schneller:** Ansichten werden erst beim Öffnen geladen – das erste Paket
  1 057 kB → 270 kB (ohne Warnung beim Bauen).
- **Fenster:** Seitenpanels und Dialoge am Rand ziehbar (Breite gemerkt, Doppelklick = Vorgabe),
  ⤢ fast volle Breite; Vorgabe nach Bildschirmbreite, lange Namen brechen in Panels um.
- **Applikation:** „In der Weg-Suche öffnen“ bei einer Verbindung öffnet einen neuen Tab – die
  Doku bleibt offen (Wunsch des Anwenders).
- **Tests:** Testclient mit `httpx2` (keine Warnung mehr); nachts bzw. von Hand alle
  Orchestrator-Tests auch mit `AUTOMATO_TEST_DOC_SNAPSHOTS=1` (Fingerabdruck der Doku);
  `test_aus_wie_bisher` schaltet den Stand selbst aus.
- **Roadmap** nach Versionen: Version 1 bis zur Produktivschaltung (1.0.0), Version 2 neuer Name
  und Cockpit, Version 3 Automatisierung (`ROADMAP.md`).
- Tests (orchestrator 664, UI-Tests 151).

## 0.109.0 – 2026-10-29

Analyse der Wege (Anwender: „weshalb dieser Pfad gewählt wird … dass ich es als Mensch verstehen
kann“, „Erwartet über …“ und „ein Finding-Button“; ADR 0038). Abnahme bis 0.108.0.

- **Warum dieser Weg?** an jeder Weg-Karte der Weg-Suche und im Detail einer Verbindung Ende zu
  Ende: derselbe Weg Hop für Hop in Sätzen – Start (wo die Quelle angeschlossen ist), Eingang mit
  Zone und Routing-Tabelle, NAT, Policy-Routen bzw. SD-WAN-Regeln, **alle Routen zum Ziel** mit
  Grund (gewählt; kürzeres Präfix, höhere Distanz, nur in der BGP-Tabelle, statische Route nicht
  aktiv, Policy-Route geht vor), Next-Hop und wem er gehört, Ende mit Hinweis (fehlende Route,
  unbekannter Next-Hop mit IPAM-Name, Schleife, Default-Route nach aussen). Je Hop, wann das
  Routing gelesen wurde (alt = orange). Bei mehreren Wegen wählbar.
- **Erwartet über …:** ein Gerät wählen, über das der Verkehr gehen müsste – liegt es nicht auf dem
  Weg, zeigt die Erklärung, wo der Weg daran vorbeigeht, welche Route über das Gerät es gibt und
  warum sie verliert bzw. dass sie fehlt, und wie das Gerät selbst Ziel und Rückweg routet.
- **Findings von Hand:** „Als Finding erfassen“ an jedem Schritt mit einem Problem (vorausgefüllt
  mit Satz, Hinweis, Gerät, Quelle/Ziel/Dienst), „+ Finding erfassen“ in der Erklärung und auf der
  Seite Findings (frei). Typ „Von Hand erfasst“, Kategorie „Von Hand“; bleibt nach dem Abgleich
  stehen, Triage wie jedes Finding; nur Operator.
- API `GET /routing/explain` (Parameter wie `/routing/trace`, dazu `expect`, `connection`,
  `starts`), `POST /findings`; Verbindungen liefern ihre Eingaben (`trace`).
- Tests (orchestrator 660, UI-Tests 150).

## 0.108.0 – 2026-10-28

Leistung (Anwender: „das Speichern dauert zu lange“, Doku im Hintergrund rechnen; ADR 0037,
Migration `0067`). Abnahme bis 0.107.0.

- **Doku als Stand:** Doku und Verbindungen Ende zu Ende (mit Plan) je Applikation werden
  gespeichert und nur neu gerechnet, wenn sich etwas ändert, das sie betrifft (Lauf eines
  Quellsystems, IPAM, Zonen, Kategorien, Namensprofil, Verbund, L3Outs, Mitglieder, ausgeblendete
  Regeln) – erkannt am Fingerabdruck der Eingaben. Die Seite zeigt sofort den letzten Stand:
  „Stand von …“, „– wird im Hintergrund aktualisiert…“, danach lädt sie von selbst neu;
  „↻ neu berechnen“ stösst eine Berechnung an. Befunde in der Doku sind immer aktuell. Gemessen
  im grossen Testbestand: Doku 9,5 s → 0,2 s, Verbindungen 4,7 s → 0,04 s.
- **Vorrechnen:** nach Läufen und Änderungen rechnet der Hintergrund die in den letzten 7 Tagen
  angesehenen Stände neu – nach dem Speichern ist die Doku meist schon fertig.
- **Speichern ohne Warten:** Zonen anlegen/ändern/löschen, Absichten, Vorschläge, Zonenplan-Import,
  Netzwerkdesign, Grundschema und Verbund paralleler Firewalls kehren sofort zurück; die Befunde
  rechnet der Hintergrund. Die Prüfung der Zonen lädt die EPG-Zuordnung nur noch einmal je
  Durchgang (grosser Testbestand: Zone speichern 13 min → 3 s).
- Einstellungen `AUTOMATO_DOC_SNAPSHOTS`, `AUTOMATO_DOC_STALE_SECONDS`, `AUTOMATO_DOC_WARM_DAYS`,
  `AUTOMATO_DOC_WARM_SECONDS`; API `GET /applications/{id}/doc/state`, `?fresh=true`.
- Tests (orchestrator 655, UI-Tests 148).

## 0.107.0 – 2026-10-28

Module und Testbefunde zu 0.106.0 (Rückmeldung des Anwenders 2026-10-28; ADR 0036, Migration
`0066`).

- **Module** (ADR 0036): Kern, Einstellungen (Basis, immer an), Worker, Gateway, Analyse,
  Wegsucher, Findings, Doku, Design (lesend) und der Schreibpfad (NetBox-Sync, braucht das
  Gateway). Jede Datei in `app/api` und `app/store` gehört genau einem Modul; die CI prüft das und
  dass **nur der Schreibpfad** den NetBox-Schreiber nutzt – Worker, Gateway-Leser und alle
  lesenden Module bleiben starr getrennt.
- **Module je Mandant schaltbar:** Mandanten & Standorte → Karte „Module“ (Admin, Audit). Aus =
  Ansicht weg, API 403, keine Läufe (Worker) bzw. Referenzquellen (Gateway) bzw. Befunde der
  Applikationen im Hintergrund; die Daten bleiben. Erste Stufe der Basis-Settings fürs Cockpit.
- **Verbindungen Ende zu Ende nur nach dem Routing:** eine Firewall, über die der Verkehr nicht
  geht, die aber dieselbe Regel hat, steht nicht mehr in der Verbindung. Kommt die Quelle von
  aussen (Internet), beginnt der Weg dort, wo der Rückweg das gelesene Netz verlässt. Neuer
  Befund **„Applikation: Regel nie gebraucht“** (Hinweis) mit dem Weg, den der Verkehr nimmt.
- **Namen der Gegenstellen schneller:** das IPAM bleibt im Speicher statt je Anfrage neu gelesen
  (`AUTOMATO_REF_CACHE`, gemessen mit 340 000 IPAM-Einträgen: Doku 11,7 s → 4 s); Namen je Regel
  und Seite einmal berechnet, Gruppen nur mit ihren Mitgliedern geladen; der Schalter zeigt
  sofort den neuen Stand („– lädt…“).
- **Entfernt:** „Doku je System“ (Ansicht, API `/appdoc/systems…`, Export, Standort je System) und
  „Publizierte Dienste“ (Ansicht, API `/published-services`) – VIPs zeigt NAT / VIPs.
- Tests (orchestrator 651, UI-Tests 147).

## 0.106.0 – 2026-10-28

Kategorien einfach (Rückmeldung des Anwenders 2026-10-28 zu 0.105.0: „Kategorien und Bereiche sind
nicht notwendig … einfacher ist besser, in allem“; ADR 0035 überarbeitet, Migration `0065`).

- **Namenskonzept → Kategorien:** nur noch Titel, Titel englisch, **Source-Zone** und **Dest-Zone**
  (je Seite ein oder mehrere Zonentypen des Grundschemas, `* jeder`). Fest mit nur dem Titel:
  innerhalb der Applikation (7), innerhalb des Security Tenants (8), unspezifisch (10). Die
  Vorgaben sind Richtungen auf der Vorlage (z. B. Edge Internet, Public → DC = „Zugriff aus dem
  Internet“) und lassen sich ändern bzw. entfernen.
- **Entfallen:** Bereiche je Zone, Merkmale im Namen der Firewall-Zone, Optionen, Spalten-Layout,
  „nur mit Zone“, Titel mit Zonen, Vorschlag nach Namen, die Ausgänge von Hand (0.105.0) und die
  Spalte „Kategorie“ im Netzwerkdesign.
- **Netzwerkdesign → Kategorien:** je Zone Zonentyp, Netze, Ausschlüsse und **Geräte**
  (Firewall/VDOM, ACI-Tenant, ACI-VRF, Anbindungen aus dem Routing).
- **Ausgänge aus den Daten:** any bzw. ein grosser Bereich gehört zu den Zonen hinter dem
  Interface der Regel (Anbindung, geroutete Netze, Default-Route); eine externe EPG zur Zone der
  Firewall hinter ihrem L3Out. Nicht eindeutig: unspezifisch. Ohne Zone der Zonentyp nach dem
  Namen der Firewall-Zone bzw. des L3Outs (Muster des Grundschemas), Systeme im ACI als DC.
- **Befund „Netz nicht über den Übergang zur Zone“:** aus den Geräten der Zonen und dem Zonenplan
  statt aus Ausgängen von Hand.
- **Spalten nach den Zeilen:** VIP-/Load-Balancer-Kette bzw. NAT-Spalten erscheinen, wo Zeilen sie
  haben.
- Tests (orchestrator 649, UI-Tests 148).

## 0.105.0 – 2026-10-28

Kategorien der Applikations-Doku einfacher: **Zonentyp, Richtungen, Ausgänge** statt Bereichen je
Zone (Rückmeldung des Anwenders 2026-10-28 zu 0.104.0, ADR 0035, Migration `0064`).

- **Zonentyp** je Zone = Zone des Grundschemas im Namensprofil (ohne eigenes die Vorlage):
  automatisch nach Name bzw. Kurzname wie beim Befund „Zone ausserhalb des Schemas“, von Hand in
  Netzwerkdesign → Kategorien (Spalte „Zonentyp“) bzw. im Zonen-Editor; gilt auch für Farbe und
  Schema im Zonenplan.
- **Richtungen** je Kategorie im Namenskonzept: „Zonentyp der Quelle → Zonentyp des Ziels“, `*` =
  jeder (z. B. `DC-Ressourcen → DC`, `Edge Internet → DC`). Mit Richtungen gilt: innerhalb der
  Applikation (7), innerhalb der eigenen Zone (8, Security Tenant), dann die genaueste Richtung,
  sonst wie bisher; die Bereiche je Zone entfallen. Zonentypen ausserhalb des Grundschemas meldet
  die Tabelle.
- **Ausgänge** einer Zone: neue Selektoren „Ausgang Interface“, „Ausgang Firewall-Zone“ (je
  optional mit der Firewall) und „Ausgang L3Out“ – eine Gegenstelle wie any gehört zur Zone, deren
  Ausgang die Regel nutzt; Spalte „Ausgänge“ im Reiter Kategorien.
- **Befund „Netz nicht über den Ausgang der Zone“** (`zone_net_exit`): lernt eine Firewall ein Netz
  einer Zone über ein anderes Interface, nur über die Default-Route oder gar nicht, steht es unter
  Findings → Zonen (mit Interface und den Zonen dahinter).
- Korrektur aus 0.104.0: ein Bereich zählt nicht mehr, wenn die Gegenstelle in der eigenen Zone
  liegt (alle Zonen erschienen als „Ressourcen-Tenant“).
- **Tests schneller** (Vorschlag 6 aus 0.104.0): siehe unten.
- Abgenommen bis 0.103.0. Tests (orchestrator 650, UI-Tests 149). Überarbeitet in 0.106.0.

### Tests schneller

Die Tests laufen parallel – lokal und in der CI; geprüft wird dasselbe wie bisher (Übersicht
`docs/TESTS.md`, Abschnitt „Schnell lokal“). Gemessen auf 4 Kernen:

- **Orchestrator parallel** mit pytest-xdist (MIT, neue Dev-Abhängigkeit des Orchestrators):
  `pytest -n auto`, in den CI-Jobs `python` und `postgres` mit `--dist worksteal`; die Abdeckung
  fasst pytest-cov über alle Worker zusammen. SQLite 9:29 → 2:20 min. Kein Test hing von der
  Reihenfolge ab (auch umgekehrt geprüft).
- **Postgres**: je xdist-Worker eine eigene Datenbank `<name>_gwN` (Tabellen, Advisory-Locks und
  Verbindungen getrennt); das Schema wird je Datenbank einmal angelegt, danach leert jeder Test
  alle Tabellen samt Zählern (`TRUNCATE … RESTART IDENTITY`) statt rund 90 Tabellen neu anzulegen
  – `AUTOMATO_TEST_DB_RESET=schema` wie bisher. Die Postgres-Instanz der CI läuft ohne fsync
  (Wegwerf-Datenbank). Lokal 26:03 → 2:46 min.
- **UI-Tests in Shards**: je Shard ein eigener Orchestrator und Vite mit eigenen Ports und eigener
  SQLite-Datei (`E2E_API_PORT`, `E2E_PORT`, `E2E_DB`); CI-Job `e2e` als Matrix mit zwei Shards
  (Bericht je Shard), lokal `npm run e2e:shards` (Kerne − 1 Shards, 5:20 → 3:00 min);
  `npm run e2e` wie bisher. Aufgeteilt wird je Test (`fullyParallel`): jeder Test läuft für sich
  allein auf den Beispieldaten – die zwei Folgen in `flows.spec.ts`, die aufeinander aufbauen
  (Kunde TE, Verbund fw-06/fw-07), stehen in `test.describe.serial`; ein zweiter Browser-Kontext
  nimmt die `baseURL` der Konfiguration statt fest Port 5173.

## 0.104.0 – 2026-10-28

Weniger Entwicklungszeit: Konfiguration statt Code, Modularisierung der Applikations-Doku,
schlanker Ablauf (Rückmeldung des Anwenders 2026-10-28; ADR 0012 und 0026 Ergänzung 0.104.0).

- **Kategorien im Namenskonzept:** Namensprofile → „Kategorien der Applikations-Doku“ (Admin):
  Titel (deutsch/englisch, mit Zonen `{z}`), Spalten und „nur mit Zone“ je Kategorie anpassen,
  eigene Kategorien dazu; die **Auswahl je Zone** (Bereiche mit Kategorie eingehend/ausgehend,
  Merkmal im Namen der Firewall-Zone, Muster für den Vorschlag nach dem Zonennamen, angeboten)
  anpassen bzw. erweitern; Optionen (breite Quelle ausgehend ausblenden, unspezifisch nur
  eingehend). Vorgaben im Code, gespeichert nur Abweichungen, auf Vorgaben zurücksetzen; Audit
  `appdoc_catalog.saved`/`.reset`. Netzwerkdesign → Kategorien und der Zonen-Editor bieten die
  Bereiche des Katalogs an, der Vorschlag nach Namen kommt vom Server; Doku, Ausblenden und
  Exporte folgen dem Katalog des Mandanten.
- **Modular:** Sichtbarkeit der Zeilen (Kategorie, nur Treffer, nicht möglich, ausgelassen) an
  einer Stelle (`store/appdoc_filter.py`); Markdown vom Server aus demselben Aufbau wie Word
  (`store/md_writer.py`, `?format=md`) – der Markdown-Code in der Oberfläche entfällt.
  Verbindungen im Export mit den Wegen je Variante („Wege uneinheitlich (aktiv über …)“) und dem
  Load Balancer am Hop, auch in Word.
- **Doku je System** als Markdown bzw. neu als Word (`GET /appdoc/systems/{name}/export`).
- **Kunden-Benutzer:** Export ihres Ausschnitts als Markdown bzw. Word – ohne Verbindungen,
  Netzwerkplan und Gerätenamen (Allowlist ergänzt).
- **Ablauf:** `CLAUDE.md` auf die Arbeitsregeln gekürzt (Stand je Bereich in `docs/STAND.md`);
  Abnahme gilt mit der nächsten Rückmeldung ohne Einwand, Klärung vor dem Bauen, Anpassungen
  einer Runde in einer Version, lokal gezielte Tests.
- Abgenommen bis 0.102.0. Tests (orchestrator 647, UI-Tests Namenskonzept und Englisch).

## 0.103.0 – 2026-10-28

Applikations-Doku nur mit relevanten Firewall-Regeln, ACI im Weg (Rückmeldung des Anwenders
2026-10-28; ADR 0012 und 0034 Ergänzung 0.103.0).

- **Ausgehend mit breiter Quelle ausgelassen:** Firewall-Regeln, die das System nur über ein
  Netz, einen Bereich bzw. any in der Quelle erfassen (z. B. 10.0.0.0/8 → irgendwohin), stehen
  nicht mehr in der Applikations-Doku (Kategorien, Regeln & Contracts, Treffer, Verbindungen) und
  nicht in der Doku je System; Anzeige „n ausgehende mit breiter Quelle ausgelassen“
  (`summary.outbound_broad`). Eingehende über grosse Bereiche, Regeln mit dem Server konkret und
  direkt zugeordnete bleiben.
- **ACI im Weg:** leitet die Firewall hinter dem L3Out eine IP zurück ins ACI (keine externe EPG
  nach ihrem Routing), ordnet die Prüfung im Fabric sie der externen EPG mit dem längsten Subnetz
  der VRF zu („… – Zuordnung im ACI“) – mit Contract erlaubt statt „das Fabric verwirft den
  Verkehr“. Ordnet keine externe EPG zu, nennt der Text den Grund.
- Tests (orchestrator 640).

## 0.102.0 – 2026-10-28

Kategorien der Applikations-Doku im Netzwerkdesign (Rückmeldung des Anwenders 2026-10-28; ADR 0012
Ergänzung 0.102.0).

- **Reiter „Kategorien“ im Netzwerkdesign** (Operator): je Zone die Kategorie, ihre **Netze**
  (Quell-Adressen) und **Ausschlüsse** („ohne“, aus den Netzen herausgeschnitten) – `PUT
  /zones/appdoc` ersetzt nur die Selektoren „Netz“, die übrigen bleiben; Audit `zone.appdoc_set`;
  „Vorschlag nach Namen“ (Edge Internet → 1/2, Edge Office → 3/4, Edge VPN → 5, SRA/Remote → 6,
  RES-… → 9, … Management → 11). Der Dialog „Zonen zuordnen“ in der Applikations-Doku entfällt;
  „Zonen der Kategorien ↗“ führt ins Netzwerkdesign (`#design?tab=appdoc`).
- **Titel nach den Zonen:** die Kategorien nennen ihre Zonen („2. Zugriff aus Edge Internet“,
  „5. Zugriff Edge VPN“, „9. Zugriff zu RES-DC“); ohne Zone der Titel der Vorlage. Der Kopf nennt
  die Netze wie erfasst mit den Ausschlüssen („10.0.0.0/8 ohne 10.20.0.0/16“) – Ansicht, Markdown,
  Word, Doku je System.
- **Kategorie 11 „Zugriff Management“** für DC Management (hinter einer eigenen Firewall):
  Firewall-Regeln mit der Gegenstelle in der Zone und Contracts zu EPGs der Zone; erscheint nur
  mit einer solchen Zone. Externe EPGs folgen dem Bereich ihrer Zone wie Firewall-Regeln; Spalte
  „Zone“ auch an Contracts.
- Ausblenden einer Regel in Kategorie 11 möglich; Tests (orchestrator 636, UI 146).

## 0.101.1 – 2026-10-28

Korrektur zu 0.101.0.

- **„Zonen zuordnen“ behält die Auswahl:** kam die Liste der Zonen ein zweites Mal an (z. B.
  langsame Antwort), setzte sie die eben gewählte Kategorie zurück und „Speichern“ blieb
  gesperrt. Eine spätere Antwort überschreibt die Auswahl nicht mehr.
- UI-Test räumt eine Zone aus einem abgebrochenen Versuch vorher weg.

## 0.101.0 – 2026-10-28

Applikations-Doku: Kategorien mit Zonen, Namen breiter Quellen (Rückmeldung des Anwenders
2026-10-28; ADR 0012 Ergänzung 0.101.0).

- **Zonen an den Kategorien:** je logischer Zone ein Bereich der Kategorien
  (`logical_zone.appdoc_area`, Migration `0063`) – Internet (1/2), Unternehmensnetzwerk (3/4),
  VPN (5), Remote Access (6), andere Security Tenants (9). Zuordnen in der Doku („Zonen
  zuordnen“, Operator, `PUT /zones/appdoc-areas`, Audit `zone.appdoc_areas_set`) bzw. im
  Zonen-Editor („Kategorie in der Applikations-Doku“, `appdoc_area` in `PUT /zones/{id}`, ohne
  Feld bleibt der Wert).
- **Kategorie nach der Zone der Gegenstelle** (`store/appdoc_zones.py`): Firewall-Regeln mit
  grossem Quell-Bereich, die direkt auf das System zeigen, stehen in der Kategorie der Zone, aus
  der sie kommen, statt unter „Unspezifische Zugriffe“ – längstes Zonen-Netz bzw. die Zonen der
  Netze im Bereich, bei any die Anbindung bzw. die Netze hinter dem Interface; genau ein Bereich
  entscheidet, vor dem Merkmal im Namen der Firewall-Zone; mehrere Bereiche bleiben unspezifisch.
- **Spalte „Zone“** (Zonen der Gegenstelle) und **Zonen mit ihren Netzen im Kopf jeder
  Kategorie** – Ansicht, Markdown, Word, Druck, Doku je System; nicht für Kunden-Benutzer.
- **Namen breiter Quellen:** mit „Namen der Gegenstellen“ Netze und Bereiche über das Objekt wie
  in der Regel – die Gruppe mit ihren Netzen in einer Zeile („G_clients (10.70.0.0/16,
  10.71.0.0/16)“); Hosts behalten den Namen aus IPAM/VM.
- **Schalter für den Global Admin** direkt in der Karte „Doku nach Kategorien“ („Namen der
  Gegenstellen“, setzt die Einstellung des Mandanten; `peer_names` in der Antwort der Doku).

## 0.100.0 – 2026-10-27

Switches & Router über SNMPv3 – zweiter, rein lesender Zugang neben SSH (Auftrag des Anwenders
2026-10-27; ADR 0033).

- **Zugang „SSH | SNMPv3“** am Quellsystem (`source_system.access`, Migration `0062`). Nur
  SNMPv3 authPriv (Auth SHA/SHA-224/-256/-384/-512, Priv AES-128/-192/-256, Passwörter ≥ 8
  Zeichen, optional Context; kein v1/v2c, keine Community, kein MD5/DES), Zugangsdaten
  verschlüsselt, ausgegeben nur die Protokolle (`snmp`); Wechsel des Zugangs verwirft die
  Zugangsdaten; Audit nur `credentials_set`/`access`.
- **Collector `collectors/snmp/`** (pysnmp, BSD-2, neue Abhängigkeit im Lockfile): nur
  GET/GETBULK auf eine Positivliste (`oids.py`, `ReadOnlyViolation` vor dem Senden, kein SET
  importiert); SNMPv2-, ENTITY-, IF-, EtherLike-, IEEE8023-LAG-, BRIDGE-, Q-BRIDGE- bzw.
  IEEE8021-Q-BRIDGE-, IP-, LLDP- und CISCO-CDP-MIB → dieselben DTOs wie per SSH (Verkabelung,
  Endpunkte, L2-Sicht, NetBox-Sync unverändert); fehlende Tabellen als Hinweis, MAC-Tabelle
  höchstens 50 000; offene Tasks von pysnmp beim Schliessen beendet.
- Prüfen: `--check` = GET auf sysName je Gerät; „Prüfen“ im Orchestrator für `snmp://` nur
  Namensauflösung mit Hinweis (UDP).
- UI: Quellsystem-Dialog mit Zugang, SNMPv3-Benutzer, Auth-/Priv-Protokoll und -Passwort,
  Context (`#ss-access`, `#ss-snmp…`); Chip „SNMPv3“ in der Liste; neue Laufmeldungen zweisprachig
  (`run_text.PHRASES`).
- **Korrigiert:** das Ziel des Agenten enthielt die festgehaltenen SSH-Host-Schlüssel nicht
  (`known_hosts` fiel im Antwortschema weg) – Agent-Läufe prüften den Schlüssel nie; jetzt
  mitgeliefert (nur Switches & Router).

## 0.99.0 – 2026-10-27

Routing bis an die Firmengrenze (Auftrag des Anwenders 2026-10-27; ADR 0032).

- **Switches & Router lesen das Routing der Router und L3-Switches** – nur `show` in der
  Positivliste (`cisco_switch/routing.py`): IOS/IOS-XE (`show ip route` inkl. `vrf *`,
  `show ipv6 route`, `show ip bgp summary`, `show bgp ipv4 unicast`), NX-OS
  (`show ip/ipv6 route vrf all`, `show ip bgp summary vrf all`), Junos (`show route`,
  `show bgp neighbor` als JSON), Arista EOS (RIB, BGP, Interface-Adressen als JSON).
- Je Gerät und VRF ein **Routing-Kontext** (Art `l3`, „rt-01“ bzw. „rt-01 · VRF x“) mit Routen,
  BGP-Pfaden und BGP-Nachbarn (AS, Zustand, lokales AS, ibgp/ebgp); Management-VRFs fallen weg;
  fehlende Rechte = „nicht lesbar“ statt Fehler; Obergrenze 20 000 Routen je
  Kontext/Tabelle/Familie (Fullfeed), zu grosse Ausgaben gekürzt; L2-Switches ohne Routing
  ohne Kontext.
- **Weg-Suche und Topologie laufen durch gelesene Router** (`paths.Graph`: Next-Hop-Besitzer über
  die eigenen Adressen, HSRP/VRRP/VARP virtuell ohne Befund „doppelte IP“); erkannte
  Anbindungen nur noch für nicht gelesene Router.
- **Firmengrenze** (`store/boundary.py`): eBGP zu fremdem AS, Anbindung Internet/MPLS/Partner
  bzw. Default-Route zum Provider – in der Weg-Suche am Weg und am letzten Hop
  (`path.boundary`/`hop.boundary`, „Firmengrenze · AS …“), in der Topologie Grenzknoten und
  Gegenstellen hervorgehoben (`boundary`, `at_boundary`, `summary.boundary`, Filter
  „Firmengrenze“ bzw. „Router (gelesen)“).
- Kunde oben: Router-Kontexte geteilt, wenn sie ein Netz des Kunden routen; Kunden-Doku nennt
  sie „Router“; der Verlauf des Routings nimmt sie mit (kein Ausgangsstand ohne Routing).

## 0.98.0 – 2026-10-27

Weg-Suche: Details am Weg und Anbindungen als Quelle bzw. Ziel (Befunde des Anwenders aus dem
Test 2026-10-27; ADR 0019 Ergänzung 0.98.0).

- **Suche in der URL** (`#wege?src=…&dst=…&service=…`, dazu `start`, `slack`,
  `src_name`/`dst_name`, `src_exit`/`dst_exit`): Zurück, Neuladen bzw. ein neuer Tab stellen die
  Suche wieder her und rechnen neu; Links aus der Applikations-Doku unverändert (`svc` als Alias).
- **Details am Weg im Seitenpanel** statt eines Sprungs: Policy/Contract (`RuleDetailDrawer`),
  VIP/Virtual Service (`NatDrawer`), Objekt der Policy (`ObjectDrawer`), Route (wirksam mit
  Next-Hops, Policy-Routen, Backup, alle Routen zum Ziel im Kontext), Knoten/Interface mit
  Inventar; jeder Link mit „↗“ (neuer Tab), Ctrl-/Mittelklick wie im Browser.
- **Je Hop „mehr“:** Ein-/Ausgang mit Zone, Route, entscheidende Regel mit
  Interfaces/Quelle/Ziel/Dienst, NAT/SNAT/Load Balancer, Zone, L2 – je Firewall eines Verbunds.
- **Anbindungen in der Namenssuche** (`GET /routing/resolve`, Art `exit`, Gruppe „Anbindungen“):
  von Hand erfasste immer, erkannte bei Art, Firewall, Interface bzw. Zone; mit Kunde oben nur
  an seinen Firewalls.
- **Anbindung als Quelle** (`GET /routing/trace?src_exit=`, `store/exit_ends.py`): Start an
  ihren Firewalls mit ihrem Eingang (Policies mit Eingang z. B. wan1 entscheiden); Quelle =
  erstes Netz der Anbindung als Ganzes, bei Default-Route/Internet eine Test-Adresse aus dem
  Dokumentationsbereich („beliebige Adresse im Internet“); `paths.trace` mit `entries`.
- **Anbindung als Ziel** (`dst_exit=`): Ziel aus ihren Netzen, je Weg „über die Anbindung“
  (`via_exit`) bzw. ein Hinweis.
- Mehrere Firewalls einer Anbindung: je ein Weg („Variante ab …“), im Verbund ein Hop; Firewall
  (`@<Quellsystem>`) und anderes Netz in der Karte der Anbindung wählbar.

## 0.97.0 – 2026-10-27

L3Outs: Firewall führend (Befunde des Anwenders aus dem Test 2026-10-27; ADR 0034).

- **Firewall hinter jedem L3Out:** abgeleitet aus dem Transfernetz bzw. BGP-Peer/statischem
  Next-Hop, von Hand bestätigen, ändern, „keine“ oder zurücksetzen (Routing → L3Outs (ACI),
  Spalte „Firewall dahinter“; `GET /routing/l3outs` mit `behind`,
  `GET /routing/l3outs/firewall-choices`, `PUT/DELETE /routing/l3outs/firewall` Operator, Audit
  `routing.l3out_firewall.set`/`.removed`; Migration `0061` `l3out_firewall`).
- **Über welchen L3Out eine Gegenstelle kommt, entscheidet das Routing der Firewalls dahinter**
  (`aci_relevance._by_firewall`: genaueste Route nach aussen, zurück ins ACI = nicht über diesen
  L3Out), mit Begründung „über L3O_inet: fw-01 routet … über wan1“; Rückfall auf das ACI
  (längstes Subnetz) ohne Firewall bzw. ohne Aussage – in Doku, Weg-Suche, Verbindungen,
  Regel-Liste nach IP, Zonen und Kunden.
- **Applikations-Doku:** Contracts zu externen EPGs mit Firewall dahinter nur als Hilfs-Contract
  an der Firewall-Regel (neue Spalte „L3Out“: „L3O_inet · Contract CT_inet“), nicht mehr in
  „Regeln & Contracts“, im Export und in den Kennzahlen; enger als die Firewall „– es fehlt …“
  mit Befund `app_contract_narrower`; ohne Firewall dahinter sichtbar mit Hinweis.
- **Unspezifische Zugriffe je Kategorie nach dem L3Out geordnet**, über den sie hereinkommen
  (Zwischenüberschrift „über L3O_x“, Spalte in Markdown/Word).
- Verbindungen Ende zu Ende: Hilfs-Contract keine eigene Verbindung, prüft im ACI-Hop mit.

## 0.96.0 – 2026-10-27

NAT / VIPs: Kette über den Load Balancer, Namenskonvention für VIPs und IP-Pools (Befunde des
Anwenders aus dem Test 2026-10-27; ADR 0027 und ADR 0016 Ergänzung 0.96.0).

- **Kette in NAT / VIPs:** je VIP der Firewall externe IP:Port → VIP des Load Balancers →
  Virtual Service → Pool → Server (`chain`); am Virtual Service die VIPs davor (`fronted_by`,
  Kunden-Benutzer nur aus ihrem Ausschnitt); Spalte „Ziel-Server“ für alle VIPs (direkt bzw.
  „über vs-…“, `dest_servers`), VIP-Gruppen je VIP (`groups`); im Detail „Kette über den Load
  Balancer“ bzw. „Davor: VIPs der Firewall“ mit Sprung zwischen den Einträgen.
- **Verknüpfung robuster** (`store/nat.Balancers.links`/`fronts`): auch Bereiche und mehrere
  Werte in `mappedip` (1:1), Port-Weiterleitung mit Port-Bereichen, Virtual Server der FortiGate
  mit der VIP des Load Balancers als Real Server und FQDN-VIPs – nur im selben Mandanten;
  Regel-Detail, publizierte Dienste, Applikations-Doku, Relevanz und Regelindex nutzen sie mit
  (Regeln am Virtual Service, Filter `nat=<Virtual Service>`).
- **Suche** über die ganze Kette (externe IP auch im Bereich, Server-IP/-Objekt/-DNS-Name,
  Virtual Service, Pool, VIP-Gruppe); **Export (CSV)** der Liste, je Weg eine Zeile.
- **Collector:** VIP-Gruppen der FortiGate (`firewall/vipgrp`/`vipgrp6`, optional, nur lesend)
  als Adressgruppe vom Typ `vipgrp` – Policies auf eine VIP-Gruppe kennen jetzt ihre VIPs;
  IPv6-IP-Pools auch als Objekte (für die Namensprüfung).
- **Namenskonvention:** VIPs, Virtual Server und IP-Pools mit dem Präfix ihres Typs (VIP bzw.
  VIP / DNAT, neu „Virtual Server“ – ohne Eintrag gelten die VIP-Präfixe –, SNAT IP-Pool); ein
  Adress-Präfix genügt nicht mehr; ohne gepflegtes Präfix des Typs kein Befund; NSX ALB nicht
  geprüft; Befund `invalid_name` zweisprachig mit Link nach NAT / VIPs.

## 0.95.0 – 2026-10-27

Applikations-Doku: Detail statt Sprung, Namen der Gegenstellen (Befunde des Anwenders aus dem
Test 2026-10-27; ADR 0012 Ergänzung 0.95.0).

- **Klick auf eine EPG** (Karte „EPGs im ACI“ bzw. im Detail eines Systems) öffnet ihr Detail wie
  im Regel-Detail (Tenant, AP, BD, VRF, Subnetze mit IPAM, Endpunkte, VMs, Leaf-Ports) statt zu
  den Regeln zu springen; im Detail „Regeln dieser EPG zeigen“ und „In Objekte & Inventar
  öffnen“ (`GET /applications/{id}/epgs/{epg_id}`, nur EPGs der Applikation, Kunden-Benutzer nur
  im Ausschnitt).
- **Firewall-Objekte** im Detail eines Systems öffnen das Objekt-Detail von „Objekte & Inventar“
  in der Doku (mit Knopf dorthin); die Kennzahlen filtern weiter.
- **Namen der Gegenstellen:** neue Einstellung am Mandanten `appdoc_foreign_names` (Migration
  `0060`, Standard aus, nur Admin, Audit `tenant.updated`) – Gegenstellen, von denen die
  Firewall-Regel nur die Adresse kennt, zeigen in den Kategorien (Ansicht, Markdown, Word, Doku
  je System) den Namen aus IPAM, VM, EPG bzw. Firewall-Objekt (`store/peer_names.py`).
  Kunden-Benutzer sehen unabhängig davon nur die Adressen.

## 0.94.1 – 2026-10-27

Korrektur nach dem Test des Anwenders („Load documentation: HTTP 500 beim Laden der Applikation“).

- **Unerwartete Fehler mit Referenz:** statt nur „HTTP 500“ zeigt die Oberfläche „Interner
  Fehler – Referenz …“; der Orchestrator schreibt die Ursache mit derselben Referenz und dem
  Traceback ins Protokoll (keine Details in der Antwort). So lässt sich der Fehler bei der Doku
  einer Applikation mit echten Daten finden – mit den Beispieldaten tritt er nicht auf (alle
  Applikationen auf Deutsch und Englisch geprüft).

## 0.94.0 – 2026-10-27

Switches: IPv6-Nachbarn, Arista EOS und Aruba CX (ADR 0025 Nr. 13; Anwender: „Verkabelung“ –
IPv6-Nachbarn, weitere Hersteller). Weiterhin strikt lesend (Positivliste der show-Befehle).

- **IPv6-Nachbarn (NDP)** wie ARP: IOS/IOS-XE `show ipv6 neighbors`, NX-OS `show ipv6 neighbor
  vrf all` (eigene Auswertung), Junos JSON, Catalyst 1200/1300 `show ipv6 neighbors all`; nur
  Unicast mit MAC, ohne Zone; fehlt der Befehl (alte Software), bleibt der Teil ohne Hinweis
  leer.
- Gespeichert in `arp_entry` ohne Migration; Endpunkte und „Wo hängt …“ (Grund „NDP <IP>“) sowie
  der L2-Anschluss in der Weg-Suche auch über IPv6; Link-local getrennt (`link_local`), nicht als
  Identität.
- **Neu: Arista EOS** (nur `show … | json`, Rolle `network-operator`) und **HPE Aruba CX/AOS-CX**
  (ntc-Vorlagen `aruba_aoscx`, Gruppe `operators`; erlaubte VLANs/LAG-Mitglieder über die Grenze
  der Vorlage hinaus).
- ArubaOS-Switch/ProCurve wird erkannt und nicht gelesen (Abbruch vor dem ersten Senden; Meldung
  zweisprachig).
- Oberfläche: Hersteller und Konto-Rollen im Quellsystem-Dialog, IPv6/Link-local in den
  Endpunkten.

## 0.93.0 – 2026-10-27

NetBox-Sync: Verkabelung und Applikationen (ADR 0031; Freigabe des Anwenders 2026-10-27
„Verkabelung, Applikationen … Go“ für Kabel/Ports und Export der Applikationen nach NetBox –
weiterhin nur NetBox wird geschrieben, Vorschau → Übernehmen).

- Zwei neue Abschnitte im NetBox-Sync, **standardmässig aus** (ohne Angabe gelten Netzinventar,
  DDI-Spiegel, Virtualisierung).
- **Verkabelung:** Switches & Router mit Ports (Typ aus Name/Speed/Medium, Beschreibung,
  aktiviert, Port-Channel) wie im Netzinventar; ohne Standort bzw. gelesenes Modell nichts raten,
  sondern Hinweis. Modus/VLANs nur mit eindeutig vorhandenen VLANs (VLAN-Gruppe des
  Quellsystems, Standort, global), sonst weggelassen mit Hinweis je Gerät. Kabel (`dcim/cables`)
  nur für beidseitig gemeldete Verbindungen zwischen Ports, die in NetBox sind bzw. im Sync
  angelegt werden; einseitige/nicht gelesene Verbindungen als Hinweis, belegter Port =
  Konflikt, umgesteckter Port mit „Löschen zulassen“ in einem Durchgang.
- **Applikationen:** je Applikation ein Tag `automato-app-<Mandant>-<Kürzel bzw. Name>` an den VMs
  und IP-Adressen ihrer Systeme; nur an Objekten mit Tag `automato`, fremde Tags bleiben, von
  Hand gepflegte Objekte als Konflikt; nicht mehr passende Applikations-Tags werden entfernt.
  Keine Custom Fields.
- Objekte eines nicht gewählten Abschnitts sind nicht mehr „veraltet“ (auch nicht mit „Löschen
  zulassen“).
- Audit `netbox_sync.planned`/`applied` mit den gewählten Abschnitten; Sync-Benutzer braucht
  zusätzlich `dcim.cable` (`sync-user.sh` erneut ausführen).

## 0.92.0 – 2026-10-27

Verlauf des Routings (ADR 0030; Anwender: „Routing: Verlauf der Routen“, bisher Momentaufnahme).

- **Änderungen von Lauf zu Lauf** je Quellsystem (`topo_change`, Migration `0059`,
  `store/topo_changes.py`): Routen (RIB, BGP-Pfade, statische), BGP-Nachbarn, HA,
  Policy-Routen/SD-WAN, Kontexte und L3Outs – je Eintrag hinzugefügt, entfernt oder geändert,
  vorher/nachher nur mit den verglichenen Feldern (Next-Hops, Protokoll, Distanz/Metrik,
  aktiv/Backup, BGP-Zustand, AS, AS-Pfad …); Reihenfolge und Zähler zählen nicht.
- **Vermerke statt Fluten:** erster Lauf = „Ausgangsstand“; nicht lesbarer Kontext ohne Flut
  „entfernt“ bzw. danach „hinzugefügt“; neuer bzw. nicht mehr gelesener Kontext und
  Gerätename-Wechsel (HA) als ein Eintrag; höchstens 5000 Einträge je Lauf, der Rest als
  Sammel-Eintrag.
- **Aufbewahrung** `AUTOMATO_RETENTION_TOPO_CHANGES_DAYS` (180 Tage, `0` = nie), auf der
  System-Seite und in `automato purge`.
- **API** `GET /routing/changes` (Filter Quellsystem, Kontext, Art, Änderung, Suche, Zeitraum;
  seitenweise; Texte in der Sprache der Anfrage; Kunde oben = sein Ausschnitt; nicht für
  Kunden-Benutzer), dazu `changed_at` an den Routen und `changes_7d`.
- **UI:** Routing → Reiter **Verlauf** mit Detail je Feld und Link zum Lauf (Läufe mit gewähltem
  Quellsystem, Lauf hervorgehoben); Chip „geändert“ an den Routen; „Routing-Änderungen (7 Tage)“
  in „Gelesene Kontexte“.

## 0.91.0 – 2026-10-27

Datenqualität (Roadmap Schritt 6 „Daten verschärfen“, erste Fassung, ADR 0029 – Inhalt von uns
vorgeschlagen, zur Abstimmung mit dem Anwender).

- **Reiter „Datenqualität“** im Abgleich und Kachel in der Übersicht: fünf Bereiche
  (Quellsysteme, Regeln, VMs, IPAM, Zonen & Kunden) mit Ampel, Kennzahl, Ursache, Hinweis und
  Link in die Ansicht zum Nachbessern; Gesamtnote = Anteil der Bereiche ohne Warnung.
  `GET /data-quality` (viewer, nicht für Kunden-Benutzer, mit Kunden-Filter).
- **Relevanz „unbekannt“ nach Ursache:** Firewall ohne Routing, Interface/Zone nicht im Inventar,
  Stichprobe über höchstens 300 Regeln (keine Route, Interface der Route unbekannt,
  Policy-Route); `relevance.check` liefert die Ursache als Schlüssel `cause`.
- **Neue Befunde** „Quellsystem: Stand veraltet“ (`source_stale`, Grenze `AUTOMATO_STALE_DAYS`
  = 7, mit Zeitplan mindestens ein Abstand plus ein Tag) und „Quellsystem: nur teilweise
  gelesen“ (`source_partial`: Geräte nicht erreicht, nicht lesbar, Routing-Kontexte
  unvollständig). Gruppe „Datenqualität“ in den Findings; der Zeitplan rechnet sie stündlich nach.
- Betriebszustand: „Stand veraltet“ mit derselben Grenze wie der Befund. Läufe: Filter auf ein
  Quellsystem per Link (`#laeufe?system=`).

## 0.90.0 – 2026-10-27

Läufe, Agent und CLI auf Deutsch und Englisch (ADR 0028 Schritt 4; Anwender: „3 Sprache“).

- **Meldungen der Läufe** und der Grund der Update-Prüfung eines Agenten bleiben deutsch
  gespeichert und erscheinen in der Sprache der Anfrage: Läufe-Liste, „Letzte Läufe“,
  Betriebszustand, Agentenliste, `automato status` (`store/run_text.py`, `runs.show_message`;
  bekannte Teile, auch in zusammengesetzten Meldungen, Unbekanntes bleibt).
- „Alle einlesen“: Gründe für übersprungene Quellsysteme zweisprachig (vorher nur englisch).
- **Agent-Konsole:** `AUTOMATO_LANG=en` in der `agent.env` stellt `-Check`, `-Once`/Wach-Modus,
  Selbst-Update, Hilfe und Log auf Englisch um (`collectors/common/i18n.py`); was an Automato
  geht, bleibt deutsch, die Konsole zeigt die Meldung des Laufs in der Übersetzung des
  Orchestrators (Quittung `shown`).
- **CLI:** `automato --lang en …` bzw. `AUTOMATO_LANG=en` (Standard Deutsch); speichernde Befehle
  bleiben deutsch.
- Collector-Agenten → Einrichtung: Meldungen des Agenten in der Fehlertabelle deutsch und
  englisch; die erzeugte `agent.env` enthält `AUTOMATO_LANG=en` bei englischer Oberfläche.

## 0.89.0 – 2026-10-27

Feinere Rollen der Kunden-Benutzer (Anwender: „Kunden: feinere Rollen“; ADR 0018 Ergänzung).

- **Kunden-Rollen je Kunde:** Lesen (`viewer`), Triage (`triage` = bisher „operator“ des
  Kunden), Editor (`editor`), Kunden-Admin (`customer_admin`) – aufgelöst in Capabilities
  (`auth/capabilities.py`). Unbekannte Rolle = Lesen, `admin` bleibt dem globalen Admin
  vorbehalten; kein bestehendes Recht wird erweitert.
- **Freigabeliste mit Capability** je Methode und Pfad; ein schreibender Endpunkt öffnet sich nie
  mit dem Leserecht.
- **Editor** pflegt Applikationen, die ganz beim Kunden liegen: Stammdaten, Regeln
  zuordnen/ausschliessen, Regeln je Kategorie ausblenden – nur mit Regeln des eigenen
  Ausschnitts; `editable` an den Applikationen, Chip „pflegbar“.
- **Kunden-Admin** ordnet die Kunden-Benutzer seines Kunden selbst zu (höchstens die eigene
  Rolle, nie das eigene Konto, nie andere Kunden); Seite „Kunden-Benutzer“. Migration `0058`
  `customer_user.origin` – seine Zuordnungen wirken nie auf Betreuer.
- **Keycloak:** optionaler Claim `customer_role` (Mapper in der Realm-Vorlage,
  `AUTOMATO_OIDC_CUSTOMER_ROLE_CLAIM`, SAML fest je IdP); mit Zuordnung gilt je Kunde die höhere
  Rolle.
- `GET /auth/me` mit `capabilities`, `customer_role`, `customer_roles`; Audit mit
  `customer_roles`, `origin`, `previous_role`.

## 0.88.0 – 2026-10-27

Abhängigkeiten zwischen Applikationen wieder in der Doku (Anwender: „Abhängigkeiten wieder
einblenden“; 0.73.0 hatte sie entfernt, weil z. B. Monitoring keine fachliche Abhängigkeit ist).

- **Infrastruktur kennzeichnen:** neues Feld im Formular der Applikation („Infrastruktur
  (Monitoring, Backup, DNS …)“, Migration `0057`, Audit). Abhängigkeiten zu solchen
  Applikationen stehen eingeklappt in einem eigenen Teil „Infrastruktur“, die fachlichen oben;
  Chip „Infrastruktur“ im Kopf der Doku.
- **Bereich „Abhängigkeiten“** in der Applikations-Doku: zugeklappt, lädt erst beim Aufklappen
  (`GET /applications/{id}/dependencies`, auch für Kunden-Benutzer mit ihrem Ausschnitt);
  „nutzt“ / „wird genutzt von“ mit den Regeln, Klick öffnet Applikation bzw. Regel.
- **Export:** Abschnitt „Abhängigkeiten“ für Markdown, Word und Druck (Deutsch/Englisch).

## 0.87.0 – 2026-10-27

Texte vom Server und Exporte auf Deutsch und Englisch (ADR 0028 Schritte 2 und 3; Anwender: „es
schadet auch nicht, wenn die Doku auch in beiden Sprachen möglich ist“).

- **Sprache je Anfrage** im Orchestrator (`app/i18n.py`): `?lang=` bzw. `Accept-Language`, ohne
  Anfrage Deutsch. Folgen der Sprache: Befundtexte und Hinweise, Weg-Suche (Start, Ende,
  Hinweise, Backup-Gründe, Urteile der Policies/Contracts, Relevanz, Scope, Leak, Zonen im Weg),
  Applikations-Doku (Titel der Kategorien, Spaltenköpfe, Bezüge, Verbindungen, Netzwerkplan),
  Zonen (Vorschläge, Gründe, Filter, Hilfszonen), Betriebszustand, Verbindungsprüfung,
  Verkabelung, Vorschau des NetBox-Syncs, Gründe der Kunden-Zuordnung, Fehlermeldungen der API.
- **Exporte** Word, PDF, SVG und draw.io (Applikation, Netzwerkplan, Zonenplan, Verkabelung) in
  der **Sprache der Datei**: eigene Wahl im Export-Dialog bzw. an den Export-Knöpfen, gemerkt im
  Browser, Vorgabe = Sprache der Oberfläche; Dateinamen der Applikation/Verkabelung folgen.
  Der Zonenplan aus draw.io wird in beiden Sprachen wieder eingelesen.
- **Gespeichertes bleibt deutsch**: Abgleich, Befunde, Einspielen, Regelindex, Kunden-Zuordnung,
  Versionen des Zonenplans und alles, was nach NetBox geschrieben wird, laufen immer deutsch –
  unabhängig von der Sprache, in der sie ausgelöst werden (NetBox: Vorschau und Übernahme mit
  gleichem Fingerabdruck). Gespeicherte Hinweise/Gründe werden bei der Anzeige übersetzt.
- Logik hängt nicht mehr an Texten: implizit gesperrt im Netzwerkplan (`implicit`), Leak im Weg
  (`kind`), Rückrichtung/beide Richtungen in beiden Fassungen, Timeout der Verbindungsprüfung
  (`failure`), Spalten der Kategorien als Schlüssel mit Anzeige `labels`.
- Noch deutsch: Meldungen der Läufe, Collector/Agent, CLI und Logs (Schritt 4).
- Tests: `test_i18n.py`, `test_i18n_findings.py`, `test_i18n_paths.py`, `test_i18n_apps.py`,
  `test_i18n_zones.py`, `test_i18n_ops.py`; UI: Befundtext auf Englisch, Sprache der Datei.

## 0.86.0 – 2026-10-27

Oberfläche auf Deutsch und Englisch umschaltbar (Anwender: „alle Texte umschalten von Deutsch auf
English und zurück“; Englisch für die Weboberfläche, die Doku soll auch in beiden Sprachen
möglich sein; alle Benutzer; Fachbegriffe bleiben englisch – ADR 0028, Schritt 1).

- **Benutzermenü → Sprache** *Deutsch* | *English*, gemerkt je Browser (`automato_lang`),
  Standard Deutsch, für alle Rollen inkl. Kunden-Benutzer; Umschalten lädt die Seite neu,
  `<html lang>` und Datum/Zeit (`de-CH` bzw. `en-GB`) folgen.
- **Alle Ansichten** zweisprachig: Navigation, Kopfzeile, Anmeldung, Seitentitel, Reiter,
  Spalten, Filter, Knöpfe, Dialoge, Hinweise, Tooltips, Bestätigungen, Fehlermeldungen der
  Oberfläche, Markdown-Export und Druck aus der Oberfläche (Applikations-Doku, Verbindungen,
  publizierte Dienste). Der deutsche Text ist unverändert.
- **Noch deutsch** (folgt in Schritt 2/3): Texte vom Orchestrator – Befundtexte und Hinweise,
  Gründe und Hinweise im Weg, Meldungen von Läufen, Fehlermeldungen der API, Spaltenköpfe und
  Titel der Kategorien, Beschriftungen in Plänen sowie die Exporte Word/PDF/draw.io/SVG. Die
  Oberfläche schickt die Sprache bereits als `Accept-Language` mit.
- **Glossar** `docs/GLOSSAR.md` mit allen Begriffen in beiden Sprachen; Fachbegriffe (Policy,
  Contract, EPG, L3Out, VDOM, VIP, Load Balancer, Source/Destination …) bleiben englisch.
- Tests: `e2e/i18n.spec.ts` – Sprache wählen, gemerkt, zurück; jede Ansicht auf Englisch ohne
  Fehler, Titel = Eintrag der Navigation.

## 0.85.0 – 2026-10-27

Netzwerkplan der Applikation: VIP und Load Balancer (Anwender: „die EPG VIP mit dem Loadbalancer
auch einzeichnen und entsprechend verbinden mit den Ziel-EPGs“; ADR 0021 Ergänzung 0.85.0).

- **Load Balancer als Knoten** nach dem Hop, an dem seine VIP liegt: im ACI benannt nach der
  EPG der VIP (statischer Endpunkt, Portgruppe, gelernt bzw. Subnetz), sonst nach dem Load
  Balancer; je Virtual Service eine Zeile, Untertitel „Load Balancer · VIP …“. Von dort die
  Linie zur EPG der Server.
- Geht der Weg nach dem Load Balancer am selben Knoten weiter, steht dieser nicht doppelt da;
  seine Regeln (Contract VIP-EPG → Server-EPG, Policy der Service Engine) im Kasten des Load
  Balancers mit dem Gerät.
- **VIP der Firewall** (DNAT) als Zeile am Knoten der Firewall.
- Gleich in Oberfläche, SVG und draw.io (`automato_kind` „Load Balancer“).

## 0.84.0 – 2026-10-27

Applikations-Doku: Kategorien-Tabellen neu (Anwender: „heute steht alles in Server Name“ – VIPs,
Load Balancer und der Weg zum Server in die Spalten verteilen; ADR 0012 Ergänzung 0.84.0).

- **Source** und **Destination** in Verkehrsrichtung statt „Server Name“/„Target Systems“:
  eingehend Gegenstelle → System der Applikation, ausgehend umgekehrt; beide mit **Name und IP**
  (`web01 (10.20.0.10)`, `N_partner (192.0.2.0/24)`), mehrere Einträge je auf einer Zeile.
- **Zugriff aus dem Internet (2):** Externer DNS Name · **VIP (Firewall)**
  (`V-EXT_shop: 203.0.113.10 → 10.30.0.50`) · **Load Balancer** (`vs-web · 10.30.0.50:443 ·
  Pool pool-web`, nur die Ports der Regel) · **Destination** = Server mit dem Port im Pool.
- **NAT** als Spalte in den übrigen Kategorien (`SNAT 198.51.100.7`); Spalten ohne Wert (NAT,
  DNS, VIP, Load Balancer, Schutzmechanismen) fallen je Kategorie weg – Ansicht, Markdown, Word
  und Druck.
- Markdown mit Zeilenumbrüchen in den Zellen (`<br>`), Word mit Umbruch.
- Spezifikation `uc1-applikations-doku-spec.md`, ADR 0012, BETRIEB, TESTS.

## 0.83.1 – 2026-10-27

Korrekturen nach dem Test des Anwenders (ACI): „In den Kategorien steht vorne 20× L3O_vpn,
hinten Contracts, die nichts mit der EPG zu tun haben“ und gestrichelte Wege im Netzwerkplan.

- **VM über die Portgruppe im Fabric:** Eine IP, die die Portgruppe einer VM bzw. ein
  statischer Endpunkt einer EPG zuordnet, liegt im Fabric – auch in einer BD ohne Subnetz
  (Gateway auf der Firewall, das ACI lernt dort keine IPs). Vorher ordnete Automato sie der
  externen EPG mit 0.0.0.0/0 zu (z. B. L3O_vpn), und alle deren Contracts mit anderen EPGs
  standen in der Doku der Applikation. Gilt für Doku je System, Applikations-Doku,
  Regel-Liste und Weg-Suche (`aci_relevance.Classifier._fabric` mit `epg_locate`).
- **VRF der Bridge Domains aus `common`:** Der Collector schrieb für eine BD die VRF immer im
  Tenant der BD (`TE_a:VRF1`), auch wenn sie in `common` liegt. Jetzt aus dem aufgelösten Ziel
  der Beziehung (`fvRsCtx.tDn`), sonst wie der APIC (eigener Tenant, dann `common`). Für schon
  eingelesene Läufe gilt `common:<VRF>`, wenn es die VRF dort gibt – die Wege dieser Systeme
  beginnen wieder in der VRF (vorher „kein gelesenes Gerät hat … angeschlossen“, gestrichelte
  Linie, Start auf dem Gerät der Regel).
- **System ausserhalb des Fabrics:** Contracts über seine externe EPG stehen in der Kategorie
  seines L3Outs (z. B. 5 „Zugriff L3O_vpn“), sonst in 9 – nicht mehr in 7/8 („ACI intern“).
- BETRIEB: wann eine Linie im Netzwerkplan gestrichelt ist und warum ein Weg über L3Out, VRF
  oder direkt zur Firewall läuft.

## 0.83.0 – 2026-10-27

Zonen komplett, Teil 4 (ROADMAP Schritt 5 abgeschlossen, ADR 0024 Nr. 20): Zonenplan aus
draw.io zurück einlesen. Ohne Migration.

- **draw.io einlesen…** im Zonenplan (Operator): den exportierten Plan nach dem Bearbeiten
  zurück nach Automato – Vorschau, dann je Teil übernehmen: **Lage** (Anordnung der Ansicht,
  aus der der Export stammt), **Namen** (erste Zeile der Beschriftung), **Absichten** (neue
  Pfeile bzw. Linien mit `automato_intent`, Auswertung wie im Grundschema), **neue Formen** als
  Zonen ohne Selektoren (nur auf Wunsch). Gelöschte Formen löschen nichts; eine seit dem Export
  in Automato umbenannte Zone behält ihren Namen (Hinweis). `POST /zones/plan/import[/preview]`,
  Audit `zone_plan.imported`.
- Export: Titel mit Ansicht und Verschiebung (`automato_view`, `automato_offset`), damit die
  Lage genau zurückkommt.
- Grundschema: Absicht und Richtung einer Linie als gemeinsame Auswertung
  (`zone_schema.edge_intent`/`edge_pairs`), unverändert im Ergebnis.
- Korrektur Weg-Suche: bei einem mehrdeutigen Namen schloss sich die Auswahl nach „Weg suchen“
  manchmal gleich wieder (Fokus-Verlust des Felds); sie bleibt jetzt offen.
- ADR 0024 Nr. 20, BETRIEB, TESTS.

## 0.82.0 – 2026-10-27

Zonen komplett, Teil 3 (ROADMAP Schritt 5, ADR 0024 Nr. 19): Zonen je Kunde im Grundschema.
Ohne Migration.

- **Vorlage je Kunde:** eine Zone des Grundschemas mit `automato_customer` = „je Kunde“ (im
  Editor das Häkchen „je Kunde“) wird je Kunde vorgeschlagen – „<Zone> <Kunde>“ mit Kurzname
  „<Kurzname>-<Kürzel>“ und den Treffern, die ganz in den Netzen bzw. VDOMs genau dieses Kunden
  liegen (Interfaces, Firewall-Zonen, IPAM-Merkmale, Firewalls). Treffer keines Kunden bleiben
  in der Zone selbst.
- Zonen eines Kunden passen ohne Name bzw. Kürzel des Kunden auf die Vorlage: kein Hinweis
  „Zone ausserhalb des Schemas“, Zuordnung in der Karte Grundschema. Die Absichten der Vorlage
  gelten nur zwischen Zonen desselben Kunden.
- Zeichnung (Vorlage, Download nach dem Bearbeiten) mit `automato_customer`; Anzeige „je Kunde“
  in der Liste des Grundschemas.
- ADR 0024 Nr. 19, BETRIEB, TESTS.

## 0.81.0 – 2026-10-27

Zonen komplett, Teil 2 (ROADMAP Schritt 5, ADR 0024 Nr. 18). Ohne Migration.

- **Zonenmatrix nach dem Weg:** eine Regel zählt für ein Zonenpaar nur, wenn ihr Gerät die
  Zonen nach dem Routing erreichen kann – wie in der Doku. Je Regel, Seite und Zone wird die
  erste Adresse der Zone im Bereich der Regel geprüft: FortiGate-Policy über Ein- bzw. Ausgang
  (`relevance.check`), ACI-Contract über externe EPG und Scope. Ausgeschlossene Paare stehen in
  der Zelle als „N nicht möglich“, nicht als erlaubend/sperrend und nicht als Verstoss gegen die
  Absicht – auch nicht im Befund „Zone: gegen die Absicht“, in der Kommunikation im Zonenplan
  und in der Sicht der Kunden. Die Zelle zeigt sie auf Wunsch mit Begründung
  (`GET /zones/matrix/rules?impossible=true`, Umschalter „nach dem Weg nicht möglich“); oben
  die Zahl der Regeln, die das Routing ganz ausschliesst. Unbekannt (Routing nicht gelesen,
  Policy-Route, SD-WAN) zählt weiter.
- **Zone nur hinter einem Router:** liegt ein Netz einer Zone an keinem gelesenen Gerät, routet
  aber eine Firewall bzw. VRF es (nicht über 0.0.0.0/0) über einen Router ohne gelesenes Gerät,
  ist das Netz dieses Routers der Übergang zur Zone – mit der Route am Übergang (`routes`, im
  Plan „Route 10.88.0.0/16“). Bisher stand dort nur „ausserhalb“, die Zone hing ohne Übergang.
- ADR 0024 Nr. 18, BETRIEB, TESTS.

## 0.80.1 – 2026-10-27

Korrektur nach dem Test des Anwenders: Läufe stabil beim Einlesen aller Geräte. Ohne Migration.

- **„Einspielen fehlgeschlagen: DeadlockDetected … relation rule_index“:** mehrere Ergebnisse
  kamen zugleich an, jedes schrieb danach den ganzen Zustand des Mandanten neu (Findings,
  Regelindex, Zwillinge paralleler Firewalls, Applikations-Befunde, Kunden) – zwei davon
  sperrten sich gegenseitig. Jetzt werden Ergebnisse **nacheinander** eingespielt
  (`store/tenant_lock.slot`); was den Zustand des Mandanten schreibt (Einspielen,
  Referenzlauf, „Abgleich“, Paare, Applikationen/Kunden nachziehen), sperrt den Mandanten in
  Postgres bis zum Ende der Transaktion (`pg_advisory_xact_lock`). Bleibt ein Deadlock bzw.
  Serialisierungsfehler, wird das Einspielen bis zu dreimal wiederholt.
- **HTTP 500 beim Aktualisieren der Läufe:** wartende Uploads hielten je eine Verbindung zur
  Datenbank, bis die Liste keine mehr bekam. Ein wartendes Ergebnis gibt seine Verbindung jetzt
  zurück; ein schon abgeschlossener Lauf wird ohne Warten quittiert. Die Liste beendet hängende
  Läufe im Savepoint und nur in einer Abfrage zugleich – ein Fehler dabei macht keinen 500 mehr.
  Mehr Verbindungen und Threads: `AUTOMATO_DB_POOL_SIZE` (10), `AUTOMATO_DB_MAX_OVERFLOW` (20),
  `AUTOMATO_API_THREADS` (80).
- **Läufe:** ein angekommenes, noch nicht eingespieltes Ergebnis steht als „Ergebnis
  angekommen – wartet aufs Einspielen (n davor)“ bzw. „… wird eingespielt“ am Lauf und wird
  nicht als hängend beendet.
- BETRIEB, TESTS, `.env.example`.

## 0.80.0 – 2026-10-27

Filter in den Verbindungen Ende zu Ende (Wunsch des Anwenders). Ohne Migration, nur Oberfläche.

- **Filter nach Art:** unter der Suche je Gruppe Knöpfe mit der Zahl der Treffer – *Urteil*
  (erlaubt, gesperrt, abhängig, offen = nicht prüfbar bzw. Weg endet vor dem Ziel), *Regeln*
  (erlaubende, sperrende Regeln der Doku), *Regel auf dem Weg* (entscheidet, möglich, greift
  nicht, nicht auf dem Weg, nicht prüfbar), *Richtung* (eingehend, ausgehend), *Hinweise*
  (Abweichungen, Wege uneinheitlich, gegen die Zonen-Absicht). In einer Gruppe gilt „oder“,
  zwischen den Gruppen „und“; die Zahl am Knopf rechnet mit den übrigen Filtern. Nur Arten,
  die es in der Applikation gibt; „N von M“ und „Filter zurücksetzen“. Gilt auch im Plan
  (hervorgehoben bzw. ausgegraut wie bei der Suche). „nur Abweichungen und Gesperrte“ bleibt.
- BETRIEB, TESTS.

## 0.79.1 – 2026-10-27

Korrekturen nach Rückmeldung des Anwenders. Ohne Migration.

- **Verbindungen Ende zu Ende zu einem Netz:** War die Gegenstelle einer Firewall-Regel ein
  ganzes Netz (z. B. eine grosse Gruppe), lief die Verbindung über die Route, die das ganze
  Netz enthält – oft 0.0.0.0/0. Netze, die genauer über andere L3Outs bzw. Interfaces geroutet
  werden, standen damit auf dem falschen Weg. Jetzt sind die genauer gerouteten Teile eigene
  Verbindungen mit ihrem Weg („Netz › Teil“, „Teil der Verbindung …“, höchstens acht je Netz,
  ohne Host-Routen); die Verbindung zum ganzen Netz nennt sie.
- **Contract mit EPG als Consumer und Provider:** neuer Befund „Contract: EPG Consumer und
  Provider“ (`aci_contract_both_sides`, Warnung, je Contract mit den EPGs): der Contract gilt
  zwischen diesen EPGs in beide Richtungen, gegenüber sich selbst wirkt er nicht. In der
  Applikations-Doku ist die eigene EPG keine Gegenstelle mehr (bisher Quelle die Server, Ziel
  die EPG selbst); die Zeile trägt im Bezug „Consumer und Provider“.
- BETRIEB, TESTS.

## 0.79.0 – 2026-10-27

VIP der FortiGate vor dem NSX ALB (Rückmeldung des Anwenders): zeigt das Ziel einer VIP der
FortiGate auf die VIP eines Virtual Service, gehören beide zusammen – öffentliche IP → VIP der
FortiGate → Virtual Service → Server des Pools. ADR 0027 Ergänzung 0.79.0, ohne Migration.

- **Firewall-Regeln:** die Server hinter dem Load Balancer sind Ziel der Regel (Bezug
  „V-EXT_x → vs-web“) – die Suche findet die Regel über den Server, Virtual Service oder Pool;
  das Regel-Detail zeigt bei der VIP den Virtual Service mit VIP:Port, Pool und Servern. Gilt
  auch für Regeln, deren Ziel direkt die VIP des Load Balancers ist. Port-Weiterleitung wählt
  den Virtual Service mit dem weitergeleiteten Port.
- **Applikations-Doku:** Regeln über VIP und Load Balancer stehen in Kategorie 2 (Zugriff aus
  dem Internet) mit der öffentlichen IP und der neuen Spalte **Load Balancer**; die Relevanz
  prüft die Route zur VIP des Load Balancers, nicht zum Server dahinter. **Verbindungen Ende zu
  Ende** gehen zur öffentlichen IP und über DNAT und Load Balancer zum Server.
- **Publizierte Dienste:** VIP der FortiGate „→ NSX ALB vs-… · n Server“, Virtual Service „über
  V-EXT_… (IP)“, im Detail beide Seiten (auch im Markdown-Export); die Regeln der VIP davor
  zählen beim Virtual Service mit.
- **Weg-Suche:** am DNAT-Hop „NSX ALB vs-…“; liegt die VIP des Load Balancers hinter einem
  Router ohne gelesenes Gerät, geht der Weg trotzdem vom Load Balancer zum Server weiter.
- Nach einem Lauf des NSX ALB wird der Regelindex des ganzen Mandanten neu aufgebaut.
- ADR 0027 Ergänzung, BETRIEB, TESTS.

## 0.78.5 – 2026-10-27

Korrekturen nach dem Test des NSX ALB (Avi 32.1.2): der Lauf kam durch, „Letzte Läufe“ meldete
dabei HTTP 500 und danach „0“ beim Lauf. Ohne Migration.

- **Spalte „Einträge“** statt „Regeln“ in den Läufen: Regeln bzw. – ohne Regeln – die
  publizierten Dienste (NSX ALB), VMs (vCenter) oder Geräte (Switches); beim IPAM wie bisher
  die Datensätze. Bisher stand beim NSX ALB immer 0.
- **Zusammenfassung des NSX-ALB-Laufs** auch bei Erfolg: Version, gelesene Avi-Tenants, Anzahl
  Virtual Services, VS VIPs, Pools, Pool Groups und Einträge (VIP × Port), dazu, was nicht
  lesbar war – zum Vergleich mit dem Controller (in den Läufen sichtbar).
- **„Letzte Läufe“ ohne Stau:** die Übersicht fragt erst wieder nach, wenn die vorige Antwort
  da ist, und behält die Liste bei einem Fehler beim Nachladen. Die Liste beendet einen
  überfälligen Lauf nur, wenn er nicht gerade gesperrt ist (`SKIP LOCKED`) – sie wartet nicht
  auf ein laufendes Einspielen und überschreibt keinen Lauf, dessen Ergebnis gerade ankommt.
- BETRIEB (Läufe, Fehlerbehebung), TESTS.

## 0.78.4 – 2026-10-27

Korrektur nach Rückmeldung des Anwenders: die Weg-Suche zeigte bei einer Sperre zuoberst
(„Default Block Darknet“) nur diese Policy, nicht die übrigen Regeln. Ohne Migration.

- **Policies dahinter:** je Firewall bzw. VDOM unter „dahinter, nicht erreicht“ bis zu fünf
  weitere passende Policies nach der entscheidenden (`rules.after`). Sperrt die erste und
  erlaubt eine dahinter, nennt das Urteil sie („gesperrt durch Policy 9 „Default Block …“ –
  dahinter würde Policy 1 „…“ erlauben (nicht erreicht)“). Das Urteil selbst bleibt: die erste
  sicher passende Policy entscheidet, wie auf der FortiGate.
- **Über welches Objekt:** je Policy Quelle und Ziel mit dem passenden Objekt („Quelle any,
  Ziel G_darknet“) – so ist sichtbar, warum eine Sperre greift.
- Applikations-Doku, Verbindungen und Abgleich rechnen wie bisher ohne die Policies dahinter.
- BETRIEB, TESTS.

## 0.78.3 – 2026-10-27

Korrektur nach Rückmeldung des Anwenders beim Test des NSX ALB (Avi): „Verbindung prüfen“
meldete nur „HTTP 400 …/api/virtualservice“, der Lauf „nicht lesbar: : virtualservice (HTTP
400); …“ für alle Objekte – ohne Grund. Ohne Migration.

- **Begründung des Controllers** in der Prüfung („Geraet meldet: …“) und je fehlendem Objekt im
  Lauf („Standard-Tenant: virtualservice (HTTP 400: …)“); ohne gewählten Tenant steht
  „Standard-Tenant“ statt eines leeren Namens.
- **API-Version:** lehnt der Controller die Abfrage mit `X-Avi-Version` ab (HTTP 400), liest
  Automato einmal ohne sie und bleibt dabei, wenn das klappt (Prüftext „(ohne X-Avi-Version)“).
  Die Version kommt aus `/api/initial-data` – ein kurzer Versuch vor, sonst nach der Anmeldung,
  ohne Wiederholungen (die den Lauf aufhalten konnten).
- **Verbindung prüfen** liest den ersten gewählten Avi-Tenant (bisher immer den Standard-Tenant
  des Kontos).
- BETRIEB (Fehlerbehebung NSX ALB), TESTS.

## 0.78.2 – 2026-10-27

Korrekturen nach Rückmeldung des Anwenders: Kategorien der Applikations-Doku und Agent nach
TLS intern. Ohne Migration.

- **Kategorien Richtung L3Out – die Firewall ist führend:** Contracts zu externen EPGs (L3Out)
  stehen nicht mehr separat in den Kategorien, solange sie die Dienste der Firewall-Regeln
  derselben Kategorie und Richtung abdecken (bzw. die Firewall dort nichts öffnet). Öffnet ein
  Contract weniger, steht er da mit den fehlenden Diensten („öffnet weniger als die
  Firewall-Regeln, es fehlt tcp/8443“) und ist ein **Befund** „Applikation: Contract enger als
  Firewall“ (`app_contract_narrower`, Warnung, je Contract und Applikation). Sperrende Contracts
  bleiben stehen; ohne Firewall im Mandanten bleibt alles wie bisher.
- **Kategorie 10 „Unspezifische Zugriffe auf die Applikation“:** nur noch Zugriffe auf die
  Applikation (eingehend), nicht ihre Zugriffe auf andere Systeme bzw. Adressen.
- Treffer, „Regeln & Contracts“ und „Verbindungen Ende zu Ende“ bleiben vollständig; gleich in
  der Doku je System, im Export und im Druck.
- **Agent nach TLS intern** (Befund des Anwenders: „RemoteProtocolError: Server disconnected
  without sending a response“ auf `http://…:8000`): seit TLS intern spricht der Orchestrator
  auf Port 8000 nur HTTPS. Der Agent nennt bei diesem Fehler die Lösung (`https://` und
  `AUTOMATO_ORCHESTRATOR_CA_BUNDLE`); das Agent-Paket enthält mit TLS intern die CA
  (`automato-ca.crt`, nur das öffentliche Zertifikat), `install.ps1` legt sie ab, stellt eine
  `http://…:8000`-Adresse auf `https://` um und ergänzt die CA-Zeile. Die Einrichtung im Web-UI
  schlägt `https://` vor, schreibt die CA-Zeile in die `agent.env` und bietet die CA zum
  Herunterladen (`GET /agents/package/info`, `GET /agents/package/ca`, Admin).
- ADR 0012 Ergänzung 0.78.2, BETRIEB, Agent-README, TESTS.

## 0.78.1 – 2026-10-27

Korrektur nach Rückmeldung des Anwenders: der Agent bekam vom Orchestrator keine Antwort
(„ReadTimeout“ bei Update-Prüfung, nächstem Lauf und Zielen bzw. „Server disconnected without
sending a response“). Ohne Migration; der Agent muss nichts tun.

- **Ursache:** Beim Ergebnis-Upload schrieb der Orchestrator das Lebenszeichen des Agenten in
  derselben Transaktion wie das Einspielen – die Zeile des Agenten blieb gesperrt, bis das
  (bei grossen Läufen minutenlange) Einspielen fertig war. Jede weitere Anfrage desselben
  Agenten wartete darauf, lief nach 30 s in den Timeout und belegte so lange eine Verbindung
  der Datenbank.
- **Behoben:** Der Upload schreibt die Agent-Zeile nicht mehr; das Lebenszeichen (mit Version
  und Update-Hinweis) wird nur gesetzt, wenn die Zeile frei ist (`SKIP LOCKED`), sonst beim
  nächsten Aufruf (`store/agents.touch`).
- BETRIEB: Fehlerbild und Prüfschritte auf dem Server; TESTS.

## 0.78.0 – 2026-10-27

Zonen komplett, Teil 1 (ROADMAP Schritt 5, offene Punkte aus ADR 0024). Ohne Migration.

- **Zonen in den Verbindungen** der Applikations-Doku: je Verbindung die Zone von Quelle und
  Ziel, die Kette der Zonen auf dem Weg (mit Übergängen) und die Absicht zwischen Quell- und
  Zielzone samt Urteil – wie in der Weg-Suche (`zone_path.ZoneContext`: Zonen, Zonenmodell und
  Absichten einmal je Applikation geladen). Kennzahl und Chip „gegen Zonen-Absicht“, Spalte
  „Zonen“ im Markdown-/Druck-Export und im Word-Export. Die Befunde „Abweichung Regel ↔ Weg“
  rechnen ohne Zonen (schneller).
- **Absicht je Richtung im Zonenplan:** an jedem Übergang je Richtung ein Pfeil am Ziel in der
  Farbe der Absicht (grün erlaubt, orange eingeschränkt, rot gesperrt), Titel mit Diensten
  („Public → Edge Internet: eingeschränkt (tcp/443)“); Absichten zwischen Zonen ohne Übergang
  als eigene, farbig gestrichelte Linie. In der Übersicht gilt je Kunde die strengste Absicht
  seiner Zonen („teils weniger streng“). Kunden-Benutzer sehen die Absichten ihrer Zonen ohne
  Notiz. Ausblendbar unter Elemente „Absichten“. Tabelle der Übergänge mit Spalte Absicht,
  Karte „Absichten ohne Übergang“; Export SVG/PDF (Pfeile, Tabelle „Absicht je Richtung“),
  draw.io (Pfeile an den Kanten, `automato_intents`) und Word.
- **Befund „Zone ausserhalb des Schemas“** (`zone_outside_schema`, Hinweis): eine logische Zone,
  die zu keiner Zone des Grundschemas im Namensprofil passt (Name, Kurzname, Muster); neu nach
  dem Speichern einer Zone und nach Hochladen/Bearbeiten/Löschen des Grundschemas. Ohne
  Grundschema kein Befund.
- ADR 0024 Nr. 17, BETRIEB, TESTS.

## 0.77.0 – 2026-10-27

VMware NSX Advanced Load Balancer (Avi) als lesende Quelle für die Doku (Frage des Anwenders,
ADR 0027). Ohne Migration.

- **Collector** `collectors/avi/` (strikt lesend): Anmeldung `POST /login`, Abmelden
  `POST /logout` – sonst nur `GET` auf die Positivliste Virtual Service, VS VIP, Pool, Pool
  Group, VRF Context, Application Profile und `virtualservice-inventory`, je Avi-Tenant
  (`X-Avi-Tenant`, Lesebereich, leer = alle sichtbaren), Seiten zu 200, `X-Avi-Version` aus
  `/api/initial-data`; jeder andere Schreibversuch `ReadOnlyViolation`; Teilfehler je Tenant
  und Objekt in der Meldung. Keine Zertifikate, Schlüssel oder Benutzer.
- **Abbildung:** je Virtual Service, VIP-Adresse (IPv4/IPv6/Floating) und Port ein publizierter
  Dienst (`nat_rule`, Typ `server-load-balance` wie der Virtual Server der FortiGate): Server
  der Pools (Pool Group nach Priorität), Methode, Health-Monitore, FQDN, Tenant/Cloud/VRF,
  Zustand, SNI-Child mit der VIP des Parents, SNAT der Service Engine bzw. Client-IP
  (`preserve_client_ip`).
- **Publizierte Dienste:** Quelle, Pool und Zahl der Server; Regeln über die VIP-Adresse.
  **NAT:** Avi-Angaben als Einstellungen. VIP-Adressen gelten als gemanagt (kein Befund
  „extern“, 0.76.1).
- **Weg-Suche:** an der angeschlossenen VIP „Load Balancer …“, dann am selben Knoten weiter zum
  ersten aktiven Server; Regeln danach mit der Service Engine (VIP-Netz) bzw. dem Client als
  Quelle und dem Port des Servers (`Graph.balancer`, `policy_check._behind_balancer`); auch in
  den Verbindungen der Applikations-Doku.
- **Oberfläche:** Plattform „NSX ALB (Avi)“ im Quellsystem-Dialog (Benutzer/Passwort,
  Avi-Tenants), Spalte Quelle bei den publizierten Diensten, „LB“ in der Weg-Suche.
- CI: UI-Tests fest auf `ubuntu-24.04` (Playwright 1.56 installiert Chromium nicht auf
  `ubuntu-latest` = Ubuntu 26.04).
- ADR 0027, Lesevertrag Abschnitt 6, BETRIEB, TESTS.

## 0.76.1 – 2026-10-26

Korrektur nach Rückmeldung des Anwenders: externe Netze und Hosts tauchen nicht mehr als
Befund „nicht im IPAM“ auf. Ohne Migration.

- **Extern** (`store/external.py`) = öffentliche Adresse bzw. öffentliches Netz/Bereich, das
  keinem gemanagten Netz angehört; gemanagt sind die Netze der gelesenen Geräte (`inv_subnet`:
  Interface-Netze der Firewalls inkl. WAN, ACI-BD/-EPG-Subnetze, Router-Interfaces). Solche
  Werte-Objekte stehen auf `expected_external` („extern (ohne IPAM)“) und zählen nicht als
  Problem (Befunde, „nur mit Befund“, Kennzahlen der Regeln).
- **Bleibt Befund:** öffentliche Adressen im Interface-Netz am WAN-Anschluss, das WAN-Netz
  selbst („Subnetz fehlt im IPAM“), private Adressen, VIPs und Objekte ohne Wert.
- Nach jedem Abgleich neu eingeordnet: kommt die Adresse ins IPAM, gilt sie als gefunden; ein
  neues Interface-Netz macht sie wieder gemanagt (`references.rematch` prüft öffentliche Werte
  wieder gegen das IPAM, `external.apply` danach).
- ADR 0010 Ergänzung 0.76.1, BETRIEB, TESTS.

## 0.76.0 – 2026-10-26

Kunde oben wählen filtert alle Ansichten (Rückmeldung des Anwenders: „keine zusätzlichen
Infos“; Entscheid: eigenes plus geteiltes, das geteilte markiert). Ohne Migration.

- **Alle Ansichten im Ausschnitt:** Routing (Kontexte, Routen, Nachbarn, HA, Policy-Routen,
  L3Outs, Anbindungen, Vorschläge, parallele Firewalls), Topologie, Weg-Suche (Namenssuche),
  Inventar (Subnetze, VLANs, Ports, Geräte, VRFs), Verkabelung (Geräte, Kabel, Ports, VLANs,
  Endpunkte, „Wo hängt …“, Plan und Export), Abgleich (Befunde, DDI ↔ NetBox), publizierte
  Dienste und Zonen (Liste, Matrix, Regeln einer Zelle) nehmen `customer_id` an.
- **Eigen oder geteilt** (`store/customer_infra.py`): ein VDOM ist eigen über einen
  VDOM-Selektor des Kunden, eine VRF im eigenen ACI-Tenant – jeweils ohne Regeln bzw. EPGs
  anderer Kunden; VDOMs nur mit Regeln (z. B. `root`) und VRFs aus `common` sind geteilt und
  zeigen nur Routen zu den Netzen des Kunden (samt Default-Route). VLANs, Ports, Geräte, Kabel
  und Endpunkte folgen aus seinen EPGs, Netzen, VDOMs und VMs. Geteiltes trägt `shared`.
- **Oberfläche:** Hinweis „Ausschnitt <Kunde>“ über der Ansicht mit „alle Kunden“; „geteilt“
  an Kontexten, Routen, Nachbarn, Inventar, Geräten der Verkabelung und Zonen.
- ADR 0018 Ergänzung 0.76.0, BETRIEB, TESTS.

## 0.75.0 – 2026-10-26

Applikationen anlegen und aufräumen, weniger Info auf einen Blick (Rückmeldung des Anwenders).
Ohne Migration.

- **Stammdaten aus CSV** (einzige Ausnahme vom „kein CSV-Import“, Entscheid des Anwenders):
  „CSV importieren“ über der Liste der Applikationen – Name, externer/interner Name, Zweck,
  Kürzel, Verantwortlich, Kritikalität, Standort, Beschreibung; Kopfzeile deutsch oder
  englisch, „;“/„,“/Tabulator, UTF-8 bzw. Windows-1252 (Excel). Vorschau je Zeile (neu,
  ändern, unverändert, Fehler mit Grund), dann „N übernehmen“; gleicher Name ändert nur die
  ausgefüllten Zellen, Zeilen mit Fehler bleiben aus. Vorlage zum Herunterladen. Die Mitglieder
  werden danach wie bisher zugeordnet. API `POST /applications/import[?apply=true]`,
  `GET /applications/import/template`, Audit `application.imported`.
- **Applikationen ohne VMs:** Spalte „VMs“ (an den Portgruppen der EPGs bzw. hinter den
  Servern, `vm_count`), „ohne VMs ausblenden“ (bleibt im Browser), „ohne VMs auswählen“ bzw.
  ankreuzen und mehrere auf einmal löschen (`POST /applications/delete`, Audit je Applikation).
- **Weniger Info auf einen Blick:** Routing „Gelesene Kontexte“ standardmässig zugeklappt
  (die Reiter ohne Scrollen sichtbar, nicht Lesbares als Zahl); die Erklärung unter jedem
  Seitentitel eine Zeile, Klick klappt auf (für alle Seiten, bleibt im Browser); zuklappbar
  auch „Letzte Läufe“ und „Regeln je Zone“ (Übersicht), „Zonen“ und „Anschluss (L2)“
  (Weg-Suche).
- CLAUDE.md, ADR 0012 (Ergänzung 0.75.0), BETRIEB.

## 0.74.1 – 2026-10-26

Korrekturen nach der Rückmeldung des Anwenders (Wartezeiten von Minuten beim Speichern,
Vorschläge, Objekt-Filter, IPAM). Ohne Migration.

- **Speichern ohne Warten:** Ändern, Anlegen, Löschen einer Applikation, Mitglieder, Vorschlag
  übernehmen und Regel zuordnen speichern sofort und antworten; die Abweichungen Regel ↔ Weg
  und die Findings der Kunden ziehen danach im Hintergrund nach (`store/app_refresh.py`,
  eigene Session, mehrere Änderungen kurz nacheinander = ein Durchgang, je Mandant einer
  zugleich). Statt der ganzen Kunden-Ableitung ordnet `customers.derive_findings` nur die
  Findings neu zu (gleiches Ergebnis, getestet).
- **Mitglieder:** die Liste im Drawer zeigt die neuen Mitglieder sofort („speichere…“ während
  der Anfrage) – vorher wartete die Antwort auf die Neuberechnung und wirkte leer.
- **Doku schneller:** das Routing liegt für viele Abfragen als Index im Speicher (je Kontext
  die besten RIB-Routen nach Präfixlänge, Routen erst bei Bedarf geladen; ab 16 Abfragen je
  Kontext); die Kunden-Ableitung liest Objekte und Findings einmal für alle Kunden und merkt
  sich ausgewertete Adressen. Gemessen (Postgres, 620 EPGs, 2000 Contracts, 3000 VMs,
  3000 Firewall-Regeln, 21 000 Routen, 20 Kunden): Kunden-Ableitung 15,5 s → 4,4 s, nur
  Findings 1,0 s; Speichern einer Applikation vorher 13–14 s, jetzt ohne Warten auf die
  Neuberechnung. Die Doku selbst braucht dort ≈ 1,5 s, die Verbindungen Ende zu Ende ≈ 3 s –
  sie laden jetzt erst, wenn ihr Bereich offen ist. Messskript `scripts/bench_applications.py`.
- **Vorschläge je Tenant:** gleiche Projektnummer in verschiedenen ACI-Tenants (z. B. `AP_001`)
  ergibt getrennte Vorschläge („Projekt 001 · TE_a“); Server zur Nummer gehen zum einzigen
  Tenant mit der Nummer, sonst in einen eigenen Vorschlag. Schlüssel `<Tenant>|<Nummer>`
  (`key`, `tenant` in `GET /applications/suggestions`, Übernehmen mit dem Schlüssel; die
  Nummer allein nur, wenn eindeutig).
- **Objekte:** beim Wechsel des Reiters (z. B. Adressobjekte → Gruppen) fällt der Typ-Filter
  sofort weg; ältere Antworten werden verworfen – die Liste zeigt nicht mehr „nichts“.
- **IPAM-Container:** aus Infoblox auch `networkcontainer`/`ipv6networkcontainer` (Status
  „container“, wie die Container-Prefixe in NetBox) – ein weiter unterteiltes Netz fehlt nicht
  mehr im IPAM (falscher Befund `subnet_not_in_ipam`); der DDI-Spiegel nach NetBox legt sie als
  Container an. Braucht den Agent dieser Version.

## 0.74.0 – 2026-10-26

Netzwerkplan der Applikation kompakt (Rückmeldung des Anwenders: zu gross, zu viele Linien).
Eigener Plan in Automato, der Export nach draw.io bleibt. Ohne Migration.

- **Applikation als Aufbau:** je Application Profile ein Rahmen (Tenant/AP), darin je EPG ein
  Kasten mit ihren VMs bzw. Servern (höchstens sechs, der Rest als Zahl); Systeme ohne EPG im
  Rahmen „ohne EPG“. Die Linien beginnen an der EPG statt an jeder VM.
- **ACI als L3Out:** der Hop im ACI heisst nach dem L3Out zur Gegenstelle hin – nach dem
  Routing (Next-Hop im Netz bzw. Peer eines L3Out-Pfads, Interface des Pfads; ausgehend der
  Ausgang, eingehend der Eingang), sonst der L3Out der externen EPG; ohne L3Out bleibt die VRF.
- **Ebene „Regeln“:** unter jedem Knoten des Wegs ein kleiner Kasten mit den entscheidenden
  Policies bzw. Contracts und ihren Diensten (Filter), „✕ implizit gesperrt“ ohne Regel; im
  Verbund je Firewall. Ein- und ausblendbar, ebenso die Ebene „Dienste und Interfaces“ an den
  Linien; die Wahl bleibt im Browser.
- **Zoom und Verschieben:** −/+/100 %/einpassen, Strg bzw. ⌘ + Mausrad, Ziehen auf freier
  Fläche; der Plan scrollt in seinem Rahmen.
- **Export:** SVG und draw.io zeichnen dasselbe – Rahmen der APs, EPGs mit VMs, im draw.io die
  Regeln als eigene Ebene „Regeln“ (in draw.io ein- und ausblendbar). Ohne die Ebene mit
  `GET …/connections/plan/export?rules=false` (folgt der Wahl in der Oberfläche); auch der
  Netzwerkplan im ZIP für Confluence ist kompakt.
- API: `GET /applications/{id}/connections` liefert `members` (EPGs mit Tenant/AP, Systeme mit
  ihren EPGs), der Plan `groups` und je Knoten `lines`/`rules`/`rules_box`; je ACI-Hop
  `l3out_in`/`l3out_out`/`l3outs`.
- UI-Test „Verbund auflösen“ wartet auf die Antwort, bevor er neu lädt (Wettlauf, in der CI von
  0.73.0 rot; die Anwendung war nicht betroffen).

## 0.73.0 – 2026-10-26

Applikations-Doku übersichtlicher (Rückmeldung des Anwenders). Ohne Migration.

- **Bereiche zuklappen:** Systeme, EPGs, Verbindungen Ende zu Ende, Regeln & Contracts,
  Findings und Doku nach Kategorien (auch jede Kategorie einzeln) klappen per Klick auf den
  Titel auf bzw. zu; „alle aufklappen“ / „alle zuklappen“. Der Zustand bleibt im Browser. Die
  Kategorien sind zu Beginn zu (wie bisher „anzeigen“).
- **Systeme kompakt:** in der Übersicht nur System, Zustand, IPs, EPG und Regeln; Klick öffnet
  das Detail (Mitgliedschaft, Betriebssystem, DNS, Portgruppe je NIC mit EPG, Cluster/Host,
  Standort, Firewall-Objekte mit Namensprüfung) mit „Regeln dieses Systems zeigen“.
- **Export / Drucken mit Auswahl:** ein Dialog mit den Abschnitten (Übersicht, Systeme, EPGs,
  Verbindungen, Netzwerkplan, Regeln, Findings, Kategorien, ausgeblendete Regeln) für Markdown,
  Word bzw. Word + draw.io (ZIP, nur mit Netzwerkplan) und Drucken / PDF – im Druck sind die
  gewählten Abschnitte offen, die übrigen fallen weg. Die Auswahl bleibt im Browser.
  `GET /applications/{id}/export?sections=…` (Komma-Liste, unbekannte 422). Markdown neu mit den
  Findings. Dateinamen schreiben Umlaute aus (`portal-sued`).
- **Abhängigkeiten vorerst entfernt:** sie zeigten z. B. einen Monitoring-Contract als
  Abhängigkeit, ohne fachliche Aussage. Weder Karte noch Export; die API liefert sie nur noch mit
  `GET /applications/{id}/doc?dependencies=true` (für einen späteren Ausbau).
- **Befund nur bei uneinheitlichen Wegen:** „Applikation: Wege uneinheitlich“ (vorher „Regel ↔
  Weg“) entsteht nur noch, wenn eine Verbindung über einen Weg offen und über einen anderen
  gesperrt ist – aktive Wege bzw. ECMP, Backup-Wege oder die Firewalls eines Verbunds urteilen
  verschieden. Sperrt der Weg durchgehend bzw. liegt die Regel nicht auf dem Weg, hat das einen
  Grund: weiter als „Abweichung“ in den Verbindungen, aber kein Befund. Die Verbindungen zeigen
  „Wege uneinheitlich“ mit dem Urteil je Weg (`variants`, `inconsistent`; Zusammenfassung
  `inconsistent`), auch in Markdown und Word.

## 0.72.1 – 2026-10-26

Korrekturen aus der Übersicht der Schwachstellen (Umplanung des Anwenders, Schritt 3). Ohne
Migration.

- **ACI: Filter nur mit Quellport.** Lässt ein Contract-Subject vom Consumer zum Provider nur
  Antworten zu (Einträge nur mit Quellport wie 443 bzw. TCP ohne SYN; Läufe ab 0.69.1), greift
  der Contract in dieser Richtung nicht mehr als „möglich“: die Weg-Suche urteilt „gesperrt“ und
  nennt ihn mit „greift nicht: … nur Antworten“, das Regel-Detail zeigt „← nur Provider →
  Consumer“ bzw. „nur Antworten“. Die Applikations-Doku führt dann nur die Rückrichtung, die
  Verbindungen Ende zu Ende bauen vorwärts keine Verbindung (kein falscher Befund „Regel ↔
  Weg“), die Zonenmatrix zählt den Contract nur zurück.
- **Doku je System wie die Applikations-Doku:** Vorwärts- und Rückrichtung eines ACI-Contracts
  in derselben Kategorie stehen als eine Zeile „(↔ beide Richtungen)“; in verschiedenen
  Kategorien bleibt die Rückrichtung eine eigene Zeile.
- **Leistung der Zonenmatrix mit der Rückrichtung gemessen:** Richtung und „nur Antworten“ kommen
  aus einer Abfrage (`aci_direction.directions`). Gemessen gegen Postgres: 5 000 Contracts
  0,1–0,3 s für die Richtungen, 0,2–0,4 s für die ganze Matrix; 20 000 Contracts 0,5–0,7 s bzw.
  rund 1,2 s – linear, kein Zwischenspeicher nötig.

## 0.72.0 – 2026-10-26

Parallele Firewalls: Abweichungen bei Policy-Routen, Routing und NAT (Umplanung des Anwenders,
Schritt 2; ADR 0020 Ergänzung 0.72.0). Rein lesend, ohne Migration.

- **Policy-Routen und SD-WAN-Regeln** im Verbund je VDOM über die Nummer bzw. den Namen
  verglichen (Status, Bedingung, Aktion, Gateway, Ausgang mit Reihenfolge, SD-WAN-Zonen). Befund
  „Parallele Policy-Route abweichend“ – abweichend bzw. fehlt auf einer Firewall.
- **Routing** je VDOM gegen die führende Firewall: wirksame Routen nach Protokoll und Interface
  (Next-Hops zählen nicht – je Firewall eigene Router), statische Routen mit Distanz, Interface,
  Gateway, Status; VDOM nur auf einer Firewall. Ein Befund „Paralleles Routing abweichend“ je
  Firewall und VDOM mit der Liste der Präfixe (Tabelle im Befund). Nicht lesbare Tabellen zählen
  nicht.
- **NAT:** VIPs und IP-Pools je VDOM und Name (IPs, Ports, Typ, externes Interface, Real Server,
  Einstellungen). Befund „Paralleles NAT abweichend“.
- **Übersicht** Routing → Reiter „Parallele Firewalls“: je Verbund gleich/abweichend/fehlt für
  Policies, Objekte, Policy-Routen, Routen und NAT mit Link auf die Befunde
  (`GET /systems/parallel`). Die Befunde öffnen die Routen des VDOMs, die Policy-Routen bzw. die
  NAT-Ansicht; Werte im Befund zuerst die Firewall des Befunds, dann die führende.

## 0.71.0 – 2026-10-26

Export für Confluence (Umplanung des Anwenders, Schritt 1; ADR 0026). Kein Schreibweg:
Automato liefert Dateien, der Import in Confluence geschieht von Hand. Ohne Migration.

- **Applikations-Doku:** Knopf „Für Confluence (Word + draw.io)“ lädt ein ZIP mit der Doku als
  Word-Datei (Steckbrief, Übersicht, Systeme, EPGs, Regeln & Contracts, Abhängigkeiten,
  Verbindungen Ende zu Ende, Doku nach Kategorien, ausgeblendete Regeln, Befunde) und dem
  Netzwerkplan als draw.io-Datei. API `GET /applications/{id}/export?format=zip|docx` (nicht für
  Kunden-Benutzer).
- **Zonenplan:** Export zusätzlich als **Word** (Zonen, Übergänge, Zonenmatrix mit Absicht,
  Diensten und Notiz, Netze ohne Zone) und „Für Confluence (Word + draw.io)“; wie die Ansicht
  gefiltert bzw. von einer gespeicherten Version. `GET /zones/plan/export?format=docx|zip`.
- Word-Dateien aus einem eigenen, kleinen Schreiber (`store/docx_writer.py`, keine neue
  Abhängigkeit): Querformat A4, Überschriften mit Gliederung (Confluence übernimmt die
  Struktur), Tabellen mit wiederholter Kopfzeile, keine Makros, keine eingebetteten Objekte.

## 0.70.0 – 2026-10-26

Patchungen im Verkabelungsplan und Grundschema der Zonen in der Oberfläche bearbeiten (Wünsche
des Anwenders, ADR 0025 Nr. 12, ADR 0024 Nr. 16). Ohne Migration.

- **Patchungen im Plan:** Umschalter „Klick: Patchungen | Ausfall“ (Standard Patchungen). Klick
  auf ein Gerät zeigt unter dem Plan seine Kabel: eigener Port und Gegenstelle je mit
  Beschreibung, Speed/Duplex, Access/Trunk mit Native VLAN, Port-Channel/vPC (als Gruppe),
  Spanning Tree, Medium, EPGs (ACI), dazu Zustand, Protokoll, Abweichungen und die Endpunkte am
  Port. Klick auf ein Kabel zeigt nur die Kabel zwischen den beiden Geräten; die Gegenstelle
  öffnet ihre Patchungen, die Zahl der Endpunkte den Reiter Endpunkte. API
  `GET /cabling/patches?device=&peer=`.
- **Grundschema bearbeiten** (Namensprofile → Grundschema der Zonen → „Bearbeiten“, Admin):
  Zonen umbenennen (Übergänge ziehen mit), Art, Kurzname, Farbe, Erkennung, Gruppe,
  Beschreibung, Reihenfolge, Zonen hinzufügen/entfernen; Übergänge mit Absicht, Diensten und
  Notiz; Vorschau der Zeichnung beim Bearbeiten. „Zeichnung herunterladen“ liefert danach den
  bearbeiteten Stand (Lage wie hochgeladen). API `PATCH /naming-profiles/{id}/zone-schema`,
  Audit `zone_schema.edited`.

## 0.69.1 – 2026-10-26

Korrekturen nach dem Test des Anwenders. Ohne Migration; der Agent holt die neue Version mit dem
Selbst-Update, danach die ACI-Quellsysteme einmal neu einlesen.

- **ACI ist zustandslos:** ein Contract mit Filter ohne Ports (`any`, `ip`, `tcp`/`udp` ohne
  Port, ICMP) erlaubt den Verkehr in **beide Richtungen** – auch der Provider kann Verbindungen
  zum Consumer aufbauen. Mit Zielport (`tcp/443`) lässt der Rückweg nur Antworten zu (fast
  zustandsbehaftet), ebenso `stateful` an TCP-Filtern. Der Collector liest die Richtung je
  Subject (`vzInTerm`/`vzOutTerm`, „Reverse Filter Ports“); Filter nur für Provider → Consumer
  zählen nicht mehr als Dienste Consumer → Provider (bisher zusammengelegt). Ältere Läufe: aus
  den Filtern ohne Ports abgeleitet (`store/aci_direction.py`).
  - **Weg-Suche:** prüft den Contract auch in der Rückrichtung (Quelle = Provider), Hinweis
    „↩ Rückrichtung“ und Text „… in der Rückrichtung (ACI ist zustandslos …)“.
  - **Regel-Detail:** „Richtung“ – „↔ beide Richtungen“ mit den Diensten bzw. „→ Consumer →
    Provider“.
  - **Applikations-Doku:** Contract mit Rückrichtung zusätzlich in der Gegenrichtung (z. B.
    externe EPG Internet als Consumer → auch „Internet Zugriff“ der Applikation), innerhalb des
    Fabrics „(↔ beide Richtungen)“ an der Zeile; Verbindungen Ende zu Ende und Abhängigkeiten
    auch in der Gegenrichtung; Kategorie 2 der ACI-Zeilen mit der Spalte „Source“.
  - **Zonenmatrix:** der Contract öffnet auch Provider-Zone → Consumer-Zone; Absicht und Befund
    `zone_policy_violation` prüfen die Rückrichtung mit ihren Diensten.
- **VLANs aus dem ACI:** der Reiter Verkabelung → VLANs zeigt auch die VLANs des APIC (Name,
  EPGs, je Leaf Ports und Port-Channel/vPC; Kennzeichen ACI/Switch). Der Collector liest dazu
  die auf den Leafs ausgerollten VLANs (`vlanCktEp` mit Ports) – auch EPGs ohne statischen Pfad
  (AAEP, VMM-Domäne) – und die Port-Channels der Leafs (`pcAggrIf`).

## 0.69.0 – 2026-10-26

Applikations-Doku: Firewall-Regeln je Kategorie ausblenden, mit Begründung und Audit (Wunsch
des Anwenders, ADR 0012 Ergänzung 0.69.0). Migration `0056` (`application_rule_hide`).

- **Ausblenden:** in „Doku nach Kategorien“ je Zeile „ausblenden“ (Operator); Begründung ist
  Pflicht, gilt nur in dieser Kategorie oder in allen. Die Zeile fällt aus Ansicht, Export und
  Druck der Kategorie; die Regel bleibt sonst in der Doku (Liste, Verbindungen, Kennzahlen).
- **Nachweis:** je Kategorie „N ausgeblendet“ mit Regel, System, Begründung, wer und wann und
  „einblenden“; im Markdown-Export der Abschnitt „Ausgeblendete Regeln“; Audit
  `application.rule_hidden`/`application.rule_shown` mit Begründung. Hält über neue Läufe.
- API: `POST /applications/{id}/hidden`, `DELETE /applications/{id}/hidden/{hide_id}`; in der
  Doku `tables[].hidden`, `hides`, `summary.hidden`.

## 0.68.0 – 2026-10-26

Weitere Switches und Router als Quellsysteme (Wunsch des Anwenders, ADR 0025 Nr. 11). Ohne
Migration; der Agent holt die neue Version mit dem Selbst-Update.

- **Quellsystem „Switches & Router“** (bisher „Cisco-Switches“): die Plattform erkennt der
  Collector je Gerät an `show version` – gemischte Gruppen sind möglich.
  - **Cisco Catalyst 9200L, 9300, 3650, 4500** (IOS/IOS-XE, auch klassisches IOS der 4500 mit
    eigener Form der MAC-Tabelle) und **Nexus 9000** (NX-OS): wie bisher, mit Tests je Modell.
  - **Cisco Catalyst 1200/1300** (Business-CLI): eigene Befehle und Auswertung (Ports, VLANs aus
    `show vlan`, Port-Channel, LLDP, MAC, ARP). Diese Geräte nehmen kein Exec an – Automato liest
    sie über eine **begrenzte Sitzung**: Anmeldung in der Sitzung, danach nur die Befehle der
    Positivliste und das Abschalten des Seitenumbruchs für die Sitzung.
  - **Juniper MX** (Router) sowie EX/QFX (Switches): Junos mit strukturierter Ausgabe (JSON) –
    Ports, Aggregate, Units mit VLAN und IPv4/IPv6-Adressen, Interface-Netze ins Inventar, LLDP,
    ARP, MAC-Tabelle, VLANs. Konto der Klasse `read-only`.
- **Verkabelung:** Router als eigene Art (in der L2-Sicht Endgerät, nicht Switch), Kennzahl
  Router; Speed/Duplex verschiedener Hersteller (`10gbps`, `10G`, `Full`) gleich verglichen.
- „Verbindung prüfen“ nennt je Gerät die erkannte Plattform (und „Sitzung“ beim Catalyst 1300).

## 0.67.0 – 2026-10-26

Netzwerkplan je Applikation (Wunsch des Anwenders, ADR 0021 Ergänzung 0.67.0). Ohne Migration.

- **Plan in „Verbindungen Ende zu Ende“:** Umschalter **Tabelle | Plan**. Links die Systeme der
  Applikation, in der Mitte Firewalls, VDOMs und ACI-VRFs auf ihren Wegen, rechts die
  Gegenstellen. Je Linie die Dienste in beide Richtungen (→ von der Applikation weg, ← zu ihr
  hin, ✕ vorher gesperrt) und an den Enden die Interfaces der Knoten; Farbe nach dem Urteil,
  gestrichelt, wo der Weg nicht bekannt ist. Linien über mehrere Spalten laufen an den Knoten
  vorbei.
- **Hervorheben:** Suche und „nur Abweichungen und Gesperrte“ der Karte bzw. ein Klick auf
  Knoten oder Linie heben deren Verbindungen hervor; Liste darunter mit Sprung in die Tabelle.
- **Export** als draw.io bzw. SVG (nur die hervorgehobenen Verbindungen, falls gewählt), mit
  Interfaces, Diensten und Urteil als Eigenschaften. API: `plan` in
  `GET /applications/{id}/connections`, `GET /applications/{id}/connections/plan/export`.

## 0.66.0 – 2026-10-26

Zonen nach dem Test des Anwenders (ADR 0024 Nr. 15): Kommunikation im Zonenplan, Vorschläge beim
Erfassen einer Zone. Ohne Migration.

- **Zonenplan – Kommunikation (Regeln):** Schalter in der Leiste; je Paar Zonen die erlaubenden
  Regeln als blau gepunktete Linie („2 Regeln“, je Richtung im Titel), auch ohne Übergang. Mit
  Fokus bzw. Suche kommen die Zonen dazu, mit denen die Treffer kommunizieren – so lassen sich
  die Nachbar-Tenants eines Tenants einblenden. Im Plan eines Kunden stehen die Partner (Zonen
  anderer Kunden mit Regeln) einzeln da. Auch im Export (SVG, draw.io, PDF).
- **Zonen-Editor – Vorschläge beim Erfassen:** zu Name, Kurzname und Art passende
  FortiGate-Zonen, Interfaces, VDOMs, IPAM-Netze und -Merkmale, ACI-Objekte und Anbindungen,
  je mit Netzen und dem Hinweis, wenn sie schon in einer anderen Zone liegen; mit einem Klick
  übernehmen. API `POST /zones/assist`.

## 0.65.1 – 2026-10-26

Korrekturen nach dem Test des Anwenders.

- **Weg-Suche:** Knopf **⇄ Richtung tauschen** – Quelle und Ziel getauscht, war schon gesucht,
  gleich der Rückweg.
- **Zonenplan:** die Linien der Übergänge verlassen die Zone an der Seite zur anderen Zone und
  laufen als weiche Kurve hinüber (übereinander von unten nach oben, mit einer Zone dazwischen
  rechts herum); Anschlüsse je Seite nach der Lage der Gegenseite geordnet, weniger Kreuzungen.
  Gleich in der Oberfläche, im SVG/PDF und im draw.io (gebogene Kanten an denselben Seiten).
- **Zonen-Vorschläge:** IPAM-Werte, die ein Objekt im ACI nennen (z. B. die ESGs `SZ_…`), werden
  keine Zone mehr. Vorschläge lassen sich filtern und gesammelt ausblenden.

## 0.65.0 – 2026-10-26

Switching/Verkabelung abgerundet (ROADMAP, ADR 0025 Nr. 10): vPC-Paare, Spanning Tree, mehrere
gleichzeitige Ausfälle, Export des Plans. Ohne Migration; ein weiterer Lesebefehl auf den
Switches (`show spanning-tree`), die Agenten holen ihn mit dem Selbst-Update.

- **Spanning Tree lesen:** je Port bzw. Port-Channel die VLANs, in denen er blockiert, und die
  Root-Ports. Im Plan sind solche Kabel gestrichelt („STP 30“), mit VLAN als *Reserve*
  (violett); der L2-Weg in der Weg-Suche meidet blockierte Kabel. Im Reiter Ports unter der
  Konfiguration (Mitglieder zeigen die Werte ihres Port-Channels).
- **vPC-Paar als logischer Switch:** `show vpc` genauer (Domäne, Rolle, Peer, Keepalive, je vPC
  Status und Konsistenz); im Plan nebeneinander im Rahmen „vPC-Domäne n“. Neuer Befund
  **vPC-Paar uneinheitlich** (Peer nicht verbunden, vPC nur auf einem Peer, vPC down bzw.
  inkonsistent); „alle Kabel am selben Switch“ nennt den ungenutzten vPC-Peer.
- **Mehrere Ausfälle gleichzeitig:** im Plan Shift-Klick auf weitere Geräte bzw. Kabel (bei
  mehreren Kabeln zwischen zwei Geräten alle); `GET /cabling/failure` nimmt `device`/`link`
  mehrfach.
- **Export des Plans** als SVG bzw. draw.io (wie gezeigt, mit VLAN), `GET /cabling/plan/export`.
- Korrektur: Passt eine Vorlage nicht auf die Ausgabe eines Befehls, bleibt nur dieser Teil leer
  (Hinweis im Lauf); bisher brach die Auswertung aller Switches des Quellsystems ab.

## 0.64.0 – 2026-10-26

Switching/Verkabelung weiter (ROADMAP, ADR 0025 Nr. 9): die L2-Sicht – Plan der Verkabelung,
Ausfall simulieren, Redundanz und VLAN-Wege, L2 in der Weg-Suche. Ohne Migration, ohne neue
Befehle auf den Geräten; alles aus der gelesenen Verkabelung abgeleitet.

- **Reiter Plan** in der Verkabelung: Geräte nach Ebenen ab dem Kern, Linien je Gerätepaar
  (mehrere Kabel bzw. Port-Channel „2×“, Abweichungen rot), Endgeräte ein-/ausblendbar.
- **Ausfall simulieren:** Klick auf ein Gerät bzw. ein Kabel zeigt, was dann getrennt ist;
  durch ESXi-Hosts und Firewalls führt kein Weg.
- **VLAN hervorheben:** je Linie, ob sie das VLAN durchlässt, welche Geräte es nutzen und ob es
  durchgehend ist oder in Inseln zerfällt.
- **Neue Befunde** (Kategorie Verkabelung): nur ein Kabel, alle Kabel am selben Switch, Switch
  als einzelner Ausfallpunkt, Port-Channel mit nicht verbundenem Mitglied, VLAN getrennt.
- **Weg-Suche:** Karte „Anschluss (L2)“ mit dem Port von Quelle und Ziel (MAC-Tabelle/ARP) und
  je Hop die Zeile „L2: …“ – der Weg über die Switches bis zum nächsten Hop bzw. bis zum Port des
  Ziels, im VLAN des Ausgangs.
- API `GET /cabling/plan`, `GET /cabling/failure`; L2-Abschnitte in `GET /routing/trace`.

## 0.63.0 – 2026-10-25

Switching/Verkabelung weiter (ROADMAP, ADR 0025 Nr. 8): welches Gerät an welchem Port hängt –
aus MAC-Tabelle und ARP der Switches. Migration `0055` (`mac_entry`, `arp_entry`); nur lesend.

- **Zwei weitere Befehle** der Positivliste: `show mac address-table` und ARP (`show ip arp`
  bzw. NX-OS `show ip arp detail vrf all`). Nur Unicast-MACs an einzelnen Ports (nicht CPU,
  Supervisor, vPC-Peer-Link), höchstens 50 000 je Switch; ARP ohne die eigenen Adressen.
- **Gesammelt statt ersetzt:** ein stiller Server fällt aus der MAC-Tabelle, bleibt in Automato
  aber mit „zuletzt gesehen“; aktuell ist je Switch, was sein letzter Lauf gesehen hat, ältere
  Einträge zeigen, wo ein Gerät früher hing. Neue Frist `AUTOMATO_RETENTION_L2_DAYS` (30 Tage)
  in der Aufbewahrung (System-Seite, `automato purge`).
- **Reiter Endpunkte** in der Verkabelung: „Wo hängt …“ nach MAC, IP oder Name (VM, DNS) →
  Switch, Port, VLAN, Gegenstelle am Port (z. B. ESXi-Host mit vmnic), früher gesehene Ports;
  „nur auf Uplinks gesehen“ nennt das Gerät, hinter dem es hängt (ACI, Switch ohne Zugang).
  Liste aller MACs an den Ports (ohne Uplinks) mit VM, IPs, IPAM-Namen, EPG.
- **Ports:** Anzahl Endpunkte je Port, ein Klick zeigt sie.
- **Befund „VLAN fehlt am ESXi-Uplink“** (`uplink_vlan_missing`): an einem Port werden VMs eines
  ESXi-Hosts gesehen, er lässt aber ein VLAN nicht durch, das VMs im selben Cluster auf
  demselben vSwitch/DVS nutzen – nach vMotion bzw. beim Ausfall des anderen Uplinks ohne Netz.
  Funktioniert ohne CDP der ESXi-Hosts (über die MACs der VMs).
- Korrektur Test: der echte SSH-Testserver bediente nach einer abgebrochenen Verbindung keine
  weitere mehr (wackliger Test, 0.62.0).

## 0.62.0 – 2026-10-25

Switching/Verkabelung (ROADMAP, ADR 0025): Cisco-Switches strikt lesend über SSH und die
Verkabelung aus CDP/LLDP mit Befunden. Migration `0054` (`inv_neighbor`, neue Spalten an
`inv_interface`, `source_system.ssh_host_keys`, `read_scope` als Text). Neue Abhängigkeiten des
Collectors: paramiko, textfsm, ntc-templates (Agenten laden sie mit dem Selbst-Update).

- **Neues Quellsystem „Cisco-Switches“** (IOS, IOS-XE, NX-OS): eine Gruppe von Switches mit
  einem Konto, das nur lesen darf (IOS privilege 1 bzw. Parser-View, NX-OS
  `network-operator`); Adressen eine je Zeile. Gelesen über den Agenten, 8 Switches parallel;
  ein Switch, der nicht antwortet, steht in der Meldung, die anderen werden eingelesen.
- **Nur lesen, auch im Code:** je Befehl ein SSH-Exec-Aufruf ohne Shell und Terminal; nur feste
  `show`-Befehle (Version, Ports, Beschreibungen, Switchport, VLANs, Port-Channel, CDP, LLDP,
  vPC), alles andere bricht vor dem Senden ab; kein `show running-config`.
- **Host-Schlüssel:** der erste Lauf hält den SSH-Host-Schlüssel je Switch fest (Audit), danach
  muss der Switch ihn vorweisen, bevor das Passwort gesendet wird. Fingerabdrücke im Dialog des
  Quellsystems, „vergessen“ nach einem Gerätetausch.
- **Ports und Nachbarn:** Status, Speed, Duplex, Medium, Access-VLAN, Native VLAN, erlaubte
  VLANs, Port-Channel, vPC; VLANs mit Namen; Nachbarn je Port aus CDP und LLDP. Geräte und Ports
  erscheinen auch unter Objekte & Inventar.
- **Ansicht Verkabelung** (nach Routing): Verbindungen (beidseitig, einseitig, Gegenstelle ohne
  CDP/LLDP) aus den Nachbarn aller Quellen – Switches und ACI-Leafs –, Geräte (auch Firewalls,
  ESXi-Hosts und nicht gelesene Geräte), Ports der Switches mit Gegenstelle, VLANs je Switch.
- **Befunde** (Kategorie „Verkabelung“) zwischen zwei gelesenen Switches: Speed bzw. Duplex
  verschieden, VLANs verschieden (Modus, Access-VLAN, Native VLAN, erlaubte VLANs), Port-Channel
  nur auf einer Seite, Verbindung nur einseitig gemeldet.
- **Verbindung prüfen** (`automato-collector --check`): je Switch erreichbar, Plattform und
  Fingerabdruck des Host-Schlüssels.
- Neues Risiko R22 (SSH-Zugang), Lesevertrag Abschnitt 5.

## 0.61.0 – 2026-10-25

Zonenplan übersichtlich (Wunsch des Anwenders, ADR 0024 Nr. 14): bei vielen Tenants und Zonen
Filter, Ebenen zum Ausblenden und Zonen von Hand anordnen. Migration `0053`
(`zone_plan_arrangement`); nur lesend gegenüber allen Quellsystemen.

- **Suche und Fokus:** Leiste über dem Plan – Suche nach Zone, Kunde, Firewall/VRF, Übergang
  oder Netz bzw. „Fokus auf Zone …“ (auch Doppelklick auf eine Zone); gezeigt werden die Treffer
  mit ihren direkten Nachbarn (wählbar: nur die Treffer bis 3 Übergänge weit), „N von M Zonen
  gezeigt“.
- **Ebenen:** Spalten (DC, Kunden, Edge, Transfer, Core, Distribution, Access, Public, Knoten
  ohne Zone, ausserhalb) und Elemente (Knoten in den Zonen, Namen der Übergänge, Kontakte, Zonen
  ohne Knoten und Übergang) ein- und ausblenden; die Auswahl merkt sich der Browser.
- **Zonen ziehen:** Zonen in der Zeichnung verschieben, die Linien folgen. Als Operator wird die
  Anordnung je Ansicht (Übersicht, Kunde, alle Zonen) gespeichert, neue Zonen kommen darunter;
  „Anordnung zurücksetzen“ ordnet wieder nach Ebenen.
- **Export und Versionen** zeigen dasselbe wie die Ansicht: gefiltert (Filter im Kopf der Datei)
  und angeordnet; der Fingerabdruck einer Version hängt nicht an der Anordnung.
- Kunden-Benutzer filtern ihren Plan ebenfalls (Suche nur über Zonen- und Kundennamen), ordnen
  aber nicht.

## 0.60.0 – 2026-10-25

Applikationen ausbauen (ROADMAP, ADR 0012 Ergänzung 0.60.0): Regeln direkt zuordnen bzw.
ausschliessen und Abhängigkeiten zwischen Applikationen. Ohne Migration (neue Arten von
Mitgliedern im bestehenden Text-Verweis); nur lesend gegenüber allen Quellsystemen.

- **Regeln direkt zuordnen:** Im Regel-Detail der Applikations-Doku „Der Applikation
  zuordnen“ – die Regel gehört dazu, auch wenn sie nur über any, ein grosses Netz oder gar
  nicht trifft; sie steht mit der Art „direkt zugeordnet“ in der Doku und zählt wie eine
  spezifische. In „Regeln & Contracts“ ordnet das Regel-Detail eine Regel einer Applikation zu
  und springt in deren Doku. Nur Operator, mit Audit.
- **Regeln ausschliessen:** „Aus der Applikation ausschliessen“ nimmt eine Regel trotz Treffer
  aus Doku, Kategorien, Verbindungen Ende zu Ende und Kennzahlen. Über der Regel-Tabelle „N
  zugeordnet · M ausgeschlossen“ mit „rückgängig“.
- Die Zuordnung hält über neue Läufe (Quellsystem, VDOM, Policy-ID bzw. Kennung des Contracts
  statt der Regel-ID).
- **Abhängigkeiten zwischen Applikationen:** Karte „Abhängigkeiten“ mit „nutzt“ bzw. „wird
  genutzt von“ – abgeleitet aus erlaubenden Regeln mit spezifischem Treffer, deren Gegenseite
  EPGs, Server bzw. ein Netz im Subnetz der EPGs einer anderen Applikation trifft; je
  Applikation die Regeln mit Bezug und Diensten, auch im Export (Markdown). Kunden-Benutzer
  sehen nur Applikationen ihres Ausschnitts.

## 0.59.0 – 2026-10-25

Applikationen ausbauen (ROADMAP, ADR 0021 Ergänzung 0.59.0): Abweichung Regel ↔ Weg als Befund,
Verbindungen Ende zu Ende in Export und Druck. Ohne Migration; nur lesend gegenüber allen
Quellsystemen.

- **Befund „Applikation: Regel ↔ Weg“:** je erlaubender Regel und Applikation, deren Verbindung
  der Weg sperrt, die nicht auf dem Weg liegt bzw. dort nicht greift – mit den betroffenen
  Verbindungen (Quelle → Ziel, Dienst, Begründung). Neue Kategorie „Applikationen“ in den
  Findings, Link auf die Regel, in der Applikations-Doku bei den Befunden. Neu berechnet nach
  jedem Lauf und Abgleich sowie beim Ändern einer Applikation; die Triage bleibt erhalten.
- **Zeitbudget** `AUTOMATO_APP_FINDINGS_SECONDS` (120 s) für die Neuberechnung nach einem Lauf –
  der Rest kommt beim nächsten Lauf zuerst dran.
- **Export und Druck:** „Export (Markdown)“ der Applikations-Doku mit dem Abschnitt
  „Verbindungen Ende zu Ende“ (Weg mit der entscheidenden Regel je Hop, Urteil, Abweichung);
  „Drucken / PDF“ schreibt Weg und Abweichung je Verbindung aus.

## 0.58.0 – 2026-10-25

Kunden fertig (ROADMAP „Zonen/Kunden fertig“, ADR 0018 Schritt 4): Zugehörigkeit von Regeln nach
dem Weg und die Doku des Kunden ohne Gerätenamen. Ohne Migration; nur lesend gegenüber allen
Quellsystemen.

- **Zugehörigkeit nach dem Weg:** Eine Regel, die nur über ein Netz des Kunden dazukommt (Quelle
  bzw. Ziel überschneidet es), zählt nur noch, wenn der Verkehr sie erreichen kann – die Firewall
  leitet das Netz über den Ein- bzw. Ausgang der Regel, im ACI ordnet die VRF das Netz der
  externen EPG des Contracts zu. Nicht prüfbar zählt sie weiter; Regeln über VDOM, Zone,
  Interface bzw. EPG des Kunden zählen wie bisher. Die Vorschau im Kunden-Editor zeigt die
  ausgeschlossenen Regeln als „nicht auf dem Weg“ mit Begründung.
- **Kunden-Doku ohne Gerätenamen:** Kunden-Benutzer sehen statt der Namen der Infrastruktur
  neutrale Bezeichnungen – „Firewall 1“, „ACI“, „vCenter“, „IPAM“, nummerierte VDOMs, „Verbund 1“,
  „Leaf“, „Nachbar“; ESXi-Host, Cluster, Datacenter und Switch bleiben leer. Das gilt für jede
  Antwort an Kunden-Benutzer (zentral, auch für künftige Endpunkte), für Texte wie Begründungen
  und Befunde und für den Export des Zonenplans. Betreuer sehen weiter alles.
- Korrektur: Befunde in der Applikations-Doku eines Kunden-Benutzers nennen keine fremden Regeln
  mehr (wie die Findings-Liste seit 0.33.0).

## 0.57.1 – 2026-10-25

Korrektur des Anwenders am Zonenplan je Kunde („es zeigt immer noch alles an – lediglich die
Firewall und ACI, welche der Kunde braucht“, ADR 0024 Nr. 12). Ohne Migration; nur lesend.

- **Nur benötigte Knoten:** der Plan eines Kunden zeigt seine Zonen mit ihren Knoten, von den
  geteilten Zonen aber nur die Firewalls/VDOMs und ACI-VRFs, die er braucht – an seinen Netzen und
  auf den kürzesten Wegen zu den Zonen seiner Regeln (gleich lange Wege alle, Partner eines
  Verbunds dazu). Geteilte Zonen ohne solche Knoten und ohne Übergang fallen weg, Übergänge nur
  mit Netzen an benötigten Knoten; Knoten ohne Zone nur, wenn der Kunde sie braucht. Der Hinweis
  über der Zeichnung nennt die ausgeblendeten Knoten („alle Zonen einzeln“ zeigt sie).
- **Kunden-Filter der Kopfzeile:** ist oben ein Kunde gewählt, öffnet der Zonenplan dessen Ansicht
  (bisher immer die Übersicht mit allem).
- Kunden-Benutzer: geteilte Zonen nur mit einem Übergang in ihrem Ausschnitt, ohne die Anzahl
  ihrer Knoten.

## 0.57.0 – 2026-10-25

Zonen je Hop in der Weg-Suche und Absicht je Dienst (ROADMAP „Zonen/Kunden fertig“, ADR 0024
Nr. 13). Migration `0052` (`zone_policy.services`); nur lesend gegenüber allen Quellsystemen.

- **Absicht je Dienst:** „eingeschränkt“ nennt die erlaubten Dienste – `tcp/443`, `udp/53`,
  `tcp/8000-8080`, `tcp`, `icmp`, `any` bzw. Service-Objekt/ACI-Filter (`FT_https`). Eine
  erlaubende Regel mit anderen Diensten (auch `ALL`) zwischen den Zonen ist gegen die Absicht:
  Zonenmatrix („nur tcp/443“, Verstösse), Regeln der Zelle mit „Dienste ausserhalb“, Befund
  „öffnet tcp/22 – erlaubt sind nur tcp/443“. Unbekannte Dienste lehnt die API ab.
- **Grundschema:** ein Übergang mit `automato_services` ist „eingeschränkt“ auf diese Dienste;
  „Absichten übernehmen“ setzt sie mit, die Vorlage aus den Zonen trägt sie weiter.
- **Weg-Suche mit Zonen:** Karte „Zonen“ mit Quell- und Zielzone, der Kette des Wegs mit den
  Übergängen („LAN →LAN-EINET→ Edge Internet →DMZ-EINET→ DMZ“) und der Absicht der Zonenmatrix
  mit Urteil gegenüber dem Weg (gegen die Absicht, Dienst erlaubt bzw. nicht, Hinweis); je Hop
  die Zone, in der Übersicht je Weg die Kette.
- Korrektur: das Detail einer Zelle der Zonenmatrix lädt die Regeln nach dem Speichern der
  Absicht neu.

## 0.56.0 – 2026-10-25

Zonenmodell nach Ebenen – Korrekturen des Anwenders nach dem Test der Zonen (ADR 0024 Nr. 12).
Ohne Migration (neue Arten als Text); nur lesend gegenüber allen Quellsystemen.

- **Zonen mit Knoten und Übergängen:** Selektor VDOM (je Firewall), ACI-Tenant bzw. ACI-VRF ordnet
  ganze Knoten einer Zone zu (die ACI-Tenants sind das Datacenter, eine Internet-Firewall
  „deklariert“ Edge Internet). Netze zwischen Knoten verschiedener Zonen sind **Übergänge**
  („DC-EINET“: L3Out ↔ Firewall-Interface, mit Beinen und Border-Routern) und gehören keiner
  Zone; Firewalls derselben Zone untereinander sind zonenintern (Transfernetz in der Zone); ein
  LAN direkt am Interface bleibt Netz seiner Zone. Zonen ohne eigene Netze sind normal.
  Namen der Übergänge nach Ebene geordnet, von beiden Seiten gleich.
- **Vorlage** nach Ebenen: DC, Edge Internet/Office/VPN/Management, Edge Transfer, Core, Office
  Distribution/Access, Branch Distribution/Access, Public – mit Kurzname, Muster, **fester
  Farbe** und Übergängen. Neue Arten Datacenter, Edge, Core, Distribution, Access.
- **Farben** aus dem Grundschema bzw. der Vorlage, überall gleich (Liste, Matrix, Plan, Export);
  im Editor fest, wenn sie vorgegeben sind.
- **Vorschläge nach dem Modell:** ACI-Tenants = DC (je Kunde eine Zone), Firewalls nach Name bzw.
  Muster, Firewalls mit einem Bein an einer Internet-Anbindung = Edge Internet, Anbindungen =
  Public, Firewall-Zonen und Interfaces nach den Mustern der Vorlage; bestehende Zonen derselben
  Vorlagen-Zone werden ergänzt; **Knoten ohne Zone** direkt zuordnen
  (`POST /zones/{id}/nodes`, Operator). Keine Transfer-Zonen aus Transfernetzen mehr.
- **Zonenplan je Kunde:** Übersicht mit den Kunden je als eine Zone, Plan eines Kunden mit seinen
  Zonen, den geteilten Zonen dahinter und den genutzten Zugängen zu anderen Kunden (je als eine
  Zone), alle Zonen einzeln; Spalten nach Ebene, Zonen mit ihren Knoten, Übergänge mit Namen,
  Tabelle der Übergänge, Knoten ohne Zone; Export in der gewählten Ansicht. Kunden-Benutzer
  sehen ihren Plan ohne Geräte. Versionen bis 0.55.0 behalten ihre Zeichnung.

## 0.55.0 – 2026-10-25

Grundschema der Zonen in der Namenskonvention als draw.io-Zeichnung (Entscheid des Anwenders,
ADR 0024 Nr. 11). Ohne Migration (Wörterbuch-Arten `zone_schema`, `zone_schema_link`,
`zone_schema_meta`); ohne Schema arbeitet alles wie bisher. Nur lesend gegenüber allen
Quellsystemen.

- **Namensprofile → Grundschema der Zonen:**
  - **Vorlage (draw.io)** mit Beispiel-Zonen und Übergängen bzw. **aus den Zonen** eines
    Mandanten als Ausgangspunkt; in draw.io bearbeiten.
  - **Zeichnung hochladen** (Admin, `.drawio`, auch komprimiert): jede Form = Zone (Name,
    Beschreibung, Füllfarbe), Eigenschaften `automato_kind`, `automato_code`, `automato_match`
    (Erkennungsmuster), `automato_order`; Formen mit Zonen darin = Gruppe. Pfeile = Übergänge mit
    Absicht aus Beschriftung (erlaubt, eingeschränkt, gesperrt), `automato_intent`, Strichart
    bzw. Farbe; Richtung nach Pfeil.
  - Vorschau mit Zonen, Übergängen, Hinweisen und Unterschied zum gespeicherten Schema; Ansicht
    als Zeichnung und Tabellen; Zeichnung wieder herunterladen; entfernen.
- **Vorschläge nach dem Schema** (Netzwerkdesign → Vorschläge): Name, Art und Farbe der passenden
  Schema-Zone (Name, Kurzname bzw. Muster), Interfaces auch allein über die Muster; Chip
  „Schema“ bzw. „nicht im Schema“, Filter „nur aus dem Grundschema“.
- **Karte Grundschema:** Schema-Zonen ohne Zone und ohne Vorschlag (anlegen über den Editor),
  Zonen ausserhalb des Schemas und die **Absichten** des Schemas, die in der Zonenmatrix fehlen
  bzw. abweichen – fehlende auf einmal übernehmen, abweichende einzeln ersetzen (Operator,
  `POST /zones/schema/intents`, Audit mit `via`).
- Sicherheit: Upload höchstens 2 MB, keine DTD/Entitäten, entpackt höchstens 10 MB.

## 0.54.0 – 2026-10-25

Zonen automatisch erkennen und vorschlagen (Entscheid des Anwenders, ADR 0024 Nr. 10).
Migration `0051` (`inv_interface.role`, `zone_suggestion_dismissal`); nur lesend gegenüber allen
Quellsystemen – gespeichert wird erst, was der Operator übernimmt.

- **Netzwerkdesign → Vorschläge** (`GET /zones/suggestions`): Zonen aus dem Gelesenen –
  - Transfernetze zwischen gelesenen Firewalls/VDOMs/VRFs → „Transfer“;
  - FortiGate-Zonen (gleicher Name auf mehreren Firewalls = eine Zone);
  - Interfaces ohne Firewall-Zone mit sprechendem Namen bzw. Alias (`dmz-web`, `wan1`), FortiOS-Rolle
    dmz/wan bzw. Management-Interfaces → „Management“;
  - IPAM-Merkmal Zone bzw. Rolle (NetBox-Rolle des Prefixes, Custom Field, DDI-Attribut);
  - ACI-VRFs (`common` geteilt, `mgmt` Management), mit Kunde, wenn alle Netze in einem Kunden
    liegen;
  - Anbindungen: Internet als eine Zone ohne die privaten Netze, sonst je Anbindung.
- Art aus dem Namen, der Rolle bzw. der Anbindung; gleicher Name = ein Vorschlag; eine
  bestehende Zone mit dem Namen wird **ergänzt**. Netze, die schon in einer Zone liegen, schliesst
  der Vorschlag aus (kein `zone_overlap` nach dem Übernehmen).
- **Übernehmen** einzeln über den Editor (vorbelegt) oder mehrere auf einmal; **ausblenden** und
  wieder vorschlagen (Operator, Audit). Dazu die gelesenen Netze ohne Zone und ohne Vorschlag.
- Collector: Rolle des FortiGate-Interfaces (`role`, `dedicated-to management`); SoT-Gateway:
  Rolle des NetBox-Prefixes als Merkmal `role=…`.

## 0.53.0 – 2026-10-24

Kunden auf Zonen und Kunden-Benutzer in der Oberfläche (ADR 0018 Schritt 4, ADR 0024;
Entscheid des Anwenders: Kunden auf Zonen mit Benutzer-Zuordnung in Automato). Migration `0050`
(`customer_user`); nur lesend gegenüber allen Quellsystemen, Automato schreibt nicht nach
Keycloak.

- **Zonen des Kunden:** Die Zonen, die einem Kunden gehören, zählen für seine Zugehörigkeit
  (EPGs und Netze der Zone mit Grund „Zone …“; ausschliessende Selektoren des Kunden gelten
  auch hier). Nach dem Speichern bzw. Löschen einer Zone rechnet Automato die betroffenen Kunden
  neu ab. Administration → Kunden zeigt die Zonen je Kunde.
- **Sicht der Kunden-Benutzer** (Netzwerkdesign, lesend):
  - **Zonen:** die eigenen mit Netzen und EPGs, die geteilten Zonen am eigenen Plan nur mit
    Namen; keine Selektoren, keine Vorschau.
  - **Zonenmatrix:** nur Regeln, die eine eigene Zone berühren; Zonen anderer Kunden als
    „anderer Kunde“; Absichten nur zwischen sichtbaren Zonen.
  - **Zonenplan** samt Export (draw.io, SVG, PDF): eigene Zonen, ihre Knoten und die geteilten
    Zonen daran (ohne deren Netze); keine Netze ohne Zone, keine Versionen.
- **Kunden-Benutzer in Automato zuordnen** (Administration → Kunden → Kunden-Benutzer,
  `/customer-users`, Admin):
  - Kennung (Benutzername, E-Mail bzw. `sub` aus Keycloak, klein verglichen) → Kunde mit Rolle
    Viewer bzw. Operator, Notiz; Audit `customer_user.assigned/changed/removed`.
  - Eine Zuordnung schränkt immer ein, nie Admin; zusammen mit dem Claim `customer_ids` gilt die
    Vereinigung. Das eigene Konto lässt sich nicht zuordnen. Wirkt bei der nächsten Anfrage.
  - Zuordnung nicht lesbar → 503 (fail-closed), nicht „ohne Einschränkung“.
  - `GET /auth/me`: Rollen, Mandanten und Kunden laut Orchestrator – die Oberfläche richtet
    sich danach (auch ein Konto ohne Rolle in Keycloak, das nur in Automato zugeordnet ist).

## 0.52.0 – 2026-10-24

Zonenplan exportieren und Versionen des Plans (ADR 0024, Entscheid des Anwenders: Plan im Tool
finden und exportieren). Migration `0049` (`zone_plan_version`); ohne neue Abhängigkeit, nur
lesend gegenüber allen Quellsystemen.

- **Export** (Netzwerkdesign → Zonenplan, `GET /zones/plan/export`):
  - **draw.io**: Zonen und Knoten als Formen mit Eigenschaften (Zone, Art, Netze, Interfaces),
    Verbindungen als Kanten – in diagrams.net weiter bearbeitbar;
  - **SVG** für Wiki und Präsentation;
  - **PDF**: Plan, Zonenmatrix (Zahlen, Absicht als Farbe, Verstösse umrandet, Notizen), Zonen
    an den Knoten, Netze ohne Zone.
  - Ein Layout auf dem Server für Oberfläche und alle Formate.
- **Versionen** (Netzwerkdesign → Versionen, `/zones/plan/versions`):
  - Version speichern (Operator, Bezeichnung und Notiz): Zonen mit Selektoren, Plan, Matrix und
    Absichten, so wie sie gerade abgeleitet sind; Nummer je Umgebung fortlaufend.
  - „aktueller Stand = Version n“ bzw. „geändert seit Version n“ (Fingerabdruck über das
    Design, ohne Regel-Zahlen).
  - Vergleich mit dem aktuellen Stand oder einer anderen Version: Zonen, Absichten, Zonen an den
    Knoten, Netze ohne Zone, Zellen der Matrix mit anderen Zahlen. Zeichnung und Export je
    Version.
  - Löschen nur Admin; die Nummer bleibt vergeben. Nicht für Kunden-Benutzer.

## 0.51.0 – 2026-10-24

Logische Zonen, Zonenmatrix und Zonenplan (Entscheid des Anwenders: Zonen in Automato pflegen,
ADR 0024). Migration `0048` (`logical_zone`, `logical_zone_selector`, `zone_policy`); nur
lesend gegenüber allen Quellsystemen.

- **Zonen** (Netzwerkdesign → Zonen, `/zones`, Operator schreibt):
  - Name, Kurzname, Art (intern, DMZ, extern, Management, geteilt, Transfer), Farbe,
    Reihenfolge, Kunde (leer = geteilt).
  - Selektoren wie beim Kunden, dazu **Anbindung** (Netze hinter einer Anbindung, z. B.
    Internet 0.0.0.0/0); ein- bzw. ausschliessend. Die Netze sind abgeleitet – neue EPGs und
    Interfaces gehören ohne Pflege dazu. Vorschau mit gleichen Netzen in anderen Zonen.
  - Eine IP gehört zur Zone mit dem längsten Präfix.
- **Zonenmatrix:** je Paar Quell-/Zielzone die erlaubenden und sperrenden Regeln
  (Firewall-Policies, ACI-Contracts, ohne Zwillinge), dazu „any“ und „ohne Zone“; Klick zeigt
  die Regeln. **Absicht** je Paar (erlaubt, eingeschränkt, gesperrt, Notiz).
- **Zonenplan:** Zonen an Firewalls/VDOMs und ACI-VRFs (Interface, L3Out, Fabric, Anbindung,
  sonst Route zu einem Router ohne gelesenes Gerät), Übergänge zwischen Zonen, Netze ohne Zone;
  als Zeichnung und Tabelle.
- **Befunde** (Findings → Zonen, sofort nach dem Speichern und nach jedem Lauf): erlaubende
  Regel zwischen Zonen mit Absicht „gesperrt“; dasselbe Netz in mehreren Zonen.
- Nicht für Kunden-Benutzer (folgt mit 0.53.0). Beim Löschen eines Kunden bleiben seine Zonen
  (ohne Kunde), beim Löschen einer Zone fällt ihre Absicht weg.

## 0.50.0 – 2026-10-23

Topologie-Modell (ADR 0019): die Umgebung als ein Modell aus Knoten und Kanten, Grundlage für
Zonen, Zonenplan und Export. Dazu Befunde zur Plausibilität. Ohne Migration, nur lesend.

- **`GET /topology`** (`store/topo_model.py`), Routing → **Topologie**:
  - Knoten: Firewalls bzw. VDOMs, ACI-VRFs (alle Border-Leafs), Segmente (ein Netz mit mehr
    als zwei gelesenen Geräten), Anbindungen (von Hand) und Router ohne gelesenes Gerät. Beim
    ACI zählen nur Gegenstellen an den L3Outs, nicht die Adressen im Fabric.
  - Kanten mit Herkunft (abgeleitet, von Hand, bestätigt): Transfernetz, Segment,
    Inter-VDOM-Link, BGP, nach aussen (aktiv/Backup, AS), manuelle Route, Verbund,
    Session-Sync. Dazu je Kante die Routen darüber, je Richtung.
  - Klick auf einen Knoten zeigt seine Verbindungen mit Netz, Adressen und Details.
  - Nicht für Kunden-Benutzer.
- **Plausibilität** (Findings → Topologie, nach jedem Lauf):
  - dieselbe IP auf zwei Geräten im selben Netz;
  - Gegenstellen im selben Netz mit verschiedener Netzmaske;
  - Routing-Schleife zwischen zwei gelesenen Geräten;
  - eingeschaltete statische Route, die nicht in der Routing-Tabelle steht und für die keine
    andere Route gilt;
  - BGP-Sitzung nicht aufgebaut.

## 0.49.0 – 2026-10-23

TLS intern mit eigener CA (Entscheid des Anwenders vor dem Produktivbetrieb, R14, ADR 0023,
ADR 0005 Nr. 3). Ohne Migration; eingeschaltet wird bewusst (`deploy/aufbau.sh tls-intern`),
bis dahin bleibt alles wie bisher.

- **Eigene CA:** `automato tls-init` stellt CA und Zertifikate für Postgres, Redis, Orchestrator
  und Web-UI aus, je Dienst in einem eigenen Ordner unter `deploy/tls/pki` (gitignored). Den
  CA-Schlüssel bekommt kein Container.
- **Mit `AUTOMATO_INTERNAL_TLS=on`:**
  - Postgres nimmt über das Netz nur noch TLS an; Orchestrator und Migration prüfen CA und Name
    (`verify-full`). Healthcheck und Sicherung im Container wie bisher.
  - Redis nur noch über TLS; Orchestrator und zentraler Collector über `rediss://`.
  - Orchestrator und Web-UI per HTTPS. Collector, Web-UI, Proxy und Agent-Gateway prüfen das
    Zertifikat gegen die interne CA.
- **`deploy/aufbau.sh tls-intern [--erneuern]`:** Zertifikate ausstellen (als root im Dienst
  `migrate`), Schalter in `.env`, Container neu starten, Hinweise für Agenten ohne Gateway.
  `pruefen` zeigt Zustand und Ablauf (Warnung 30 Tage vorher). `update.sh` und `aufbau.sh`
  fragen den Orchestrator dann per HTTPS ab.
- **Anzeige:** Betriebszustand („TLS intern aktiv“ bzw. Warnung), System → Verbindungen
  (Schritt `tls` bei Datenbank und Redis), System-Seite (Intern: Zertifikat, TLS der Datenbank).
- **CI:** Postgres und Redis aus `docker-compose.yml` mit TLS – Klartext und fremde CA werden
  abgewiesen.
- **Achtung beim Einschalten:** Agenten, die direkt auf Port 8000 gehen, brauchen
  `https://<VM>:8000` und die CA (`deploy/tls/pki/client/ca.crt`); über das Agent-Gateway
  ändert sich nichts.

## 0.48.0 – 2026-10-23

Betrieb vor dem Produktivbetrieb (Entscheid des Anwenders, R14, ADR 0022): Fristen für alte
Daten und getrennte DB-Rollen. Ohne Migration; neuer Compose-Dienst `migrate`.

- **Aufbewahrung:** Der Zeitplan löscht einmal am Tag, was über seiner Frist ist – beendete
  Läufe (90 Tage), NetBox-Sync-Vorschauen (90) und Audit-Einträge (365); `0` = nie
  (`AUTOMATO_RETENTION_*_DAYS`).
  - Es bleibt immer der aktuelle Stand: Läufe, auf die Regeln, Objekte, NAT, VMs bzw. der
    Referenzstand zeigen, je Quellsystem der neuste und der neuste erfolgreiche Lauf, offene
    Läufe, die neuste Vorschau je NetBox.
  - System → **Aufbewahrung**: Fristen, was fällig ist, zuletzt aufgeräumt, **Jetzt aufräumen**
    (Admin); CLI `automato purge [--dry-run]`; jedes Aufräumen im Audit (`retention.purged`).
  - Betriebszustand: Warnung, wenn Läufe über der Frist liegen und nicht aufgeräumt wurde.
- **Getrennte DB-Rollen:** Die Migration läuft im Dienst `migrate` als Eigentümer der Tabellen
  und richtet danach die App-Rolle ein (`automato db-roles`); der Orchestrator verbindet sich
  mit ihr – Daten lesen und schreiben, aber keine Tabellen anlegen, ändern oder löschen.
  - Einmal einrichten: `deploy/aufbau.sh db-rollen` (Rolle `automato_app` mit Zufalls-Passwort
    in `.env`, Neustart; `--neu` = neues Passwort). Das Passwort geht nur als SCRAM-Verifier an
    Postgres.
  - Ohne Eintrag bleibt es wie bisher (Orchestrator als Eigentümer); Betriebszustand,
    System → Verbindungen, System-Seite (DB-Rolle) und `deploy/aufbau.sh pruefen` warnen.
  - `deploy/aufbau.sh pruefen`/`status` kennen den einmaligen Dienst `migrate` (beendet mit
    Exit 0 = in Ordnung).

## 0.47.0 – 2026-10-23

SNAT der FortiGate genauer, damit weniger Wege unbestimmt bleiben (Entscheid des Anwenders,
ohne Central-NAT). Ohne Migration; der Collector-Agent aktualisiert sich.

- **IP-Pools nach Typ:**
  - `overload`, `one-to-one`, `port-block-allocation`: Die Quelle danach ist der ganze
    Bereich des Pools, als Netz, wenn er eines ist. Bisher galt nur die erste IP.
  - Policies dahinter sind sicher, wenn sie den ganzen Bereich erlauben, sonst „nur teilweise“.
  - `fixed-port-range`: die IP, die der Quelle fest zugeordnet ist.
  - Ein Pool mit einer IP ergibt genau diese.
- **IPv6:** Pools aus `firewall/ippool6`, die Felder `nat6`/`ippool6`/`poolname6`
  (FortiOS 7.x) bzw. die von `policy6`. Ohne Pool gilt die IPv6-Adresse des Ausgangs.
- **VIP mit `nat-source-vip`:** Verkehr vom Server geht mit der externen IP der VIP hinaus –
  vor dem NAT der Policy.
- **Befund „SNAT-Pool fehlt“** (Regeln): Eine Policy übersetzt auf einen IP-Pool, den die
  Firewall im VDOM nicht hat. In der Weg-Suche steht dann „Pool … nicht gelesen“.
- **Anzeige:** Weg-Suche und Verbindungen nennen Pool, Typ und Bereich bzw. die VIP.
- **Collector:** liest `firewall/ippool6` (nur lesend; ohne Tabelle bzw. Recht leer) und gibt
  Typ und internen Bereich der Pools mit.

## 0.46.0 – 2026-10-23

Konfiguration vor Gelerntem überall (R12, Entscheid des Anwenders). Migration `0047`; der
Collector-Agent aktualisiert sich, danach APICs neu einlesen.

- **Reihenfolge der Zuordnung IP → EPG** (`store/epg_locate.py`):
  1. statischer Endpunkt (fvStCEp);
  2. Portgruppe der VM (vCenter);
  3. gelernter Endpunkt;
  4. EPG-Subnetz, dann BD-Subnetz.
  Bisher galt in der Weg-Suche zuerst der gelernte Endpunkt, und die Portgruppe der VM
  zählte dort gar nicht.
- **Weg-Suche:** Die EPG von Quelle bzw. Ziel folgt der Konfiguration und nennt die Herkunft,
  z. B. „EPG TE/AP/EG_a (Portgruppe der VM vm-a)“.
- **Regel-Liste (Suche nach einer IP):** Erfasst ein Contract die IP nur über ein gemeinsames
  Subnetz, gehört sie nach der Konfiguration aber zu einer anderen EPG, ist er „nicht möglich“
  mit Begründung.
- **Neuer Befund „EPG: gelernt ≠ konfiguriert“** (ACI / VMware): Das ACI hat eine IP in einer
  anderen EPG gelernt, als Portgruppe bzw. statischer Endpunkt vorgeben (gleiche VRF). Ein
  Befund je VM bzw. EPG, mit den IPs.
- **Collector:** liest statische Endpunkte (`fvStCEp`/`fvStIp`) und die Herkunft gelernter
  Endpunkte (`fvCEp.lcC`: static, vmm, learned). Alles nur lesend, aus dem bereits
  gelesenen Baum des Tenants.

## 0.45.1 – 2026-10-23

Korrektur: Scope der ACI-Contracts im ganzen Modell (Hinweis des Anwenders). Ohne Migration.

- **Was der Scope verbindet** (eine Stelle für alle Prüfungen, `aci_relevance.Classifier`):
  - AP: nur innerhalb des AP. vzAny steht für alle EPGs seiner VRF und verbindet mit einer
    EPG derselben VRF die EPGs ihres AP. Bisher fiel ein solcher Contract weg.
  - VRF: nur innerhalb der VRF.
  - Tenant: im Tenant, auch über mehrere VRFs.
  - Global: alle verbundenen EPGs.
  - Zwischen VRFs braucht es weiter geteilte Subnetze bzw. Route-Leaking (0.44.0).
- **Regel-Detail:** Beim Scope steht, was er verbindet, und wie viele Paare Consumer →
  Provider es gibt. EPGs ohne Gegenstelle im Scope tragen „ohne Gegenstelle im Scope“.
- **Regel-Liste:** Bei der Suche nach einer IP ist ein Contract „nicht möglich“, wenn sein
  Scope die EPG dieser IP mit keiner Gegenstelle verbindet, mit Begründung („Scope VRF: …
  liegen in verschiedenen VRFs“).
- **Neuer Befund „Contract: ohne Gegenstelle im Scope“** (Findings → Regeln): Warnung, wenn
  der Contract wegen des Scopes gar nichts verbindet; sonst Hinweis mit den EPGs, für die
  er keine Wirkung hat.
- Applikations-Doku, Weg-Suche, Shared Services und Verbindungen Ende zu Ende nutzen
  dieselbe Prüfung wie bisher.

## 0.45.0 – 2026-10-23

Verbindungen Ende zu Ende in der Applikations-Doku (Wunsch des Anwenders: eine Zeile je Weg,
wenn mehrere Firewalls und das ACI hintereinander stehen). Ohne Migration. ADR 0021 (Entwurf).

- **Eine Zeile je Verbindung:**
  - Neue Karte „Verbindungen Ende zu Ende“ über „Regeln & Contracts“. Je Verbindung (Quelle →
    Ziel, Dienst) der Weg über ACI und Firewalls wie in der Weg-Suche, je Knoten nach seinem
    Urteil eingefärbt, dazu das Gesamturteil.
  - Abgeleitet aus den Regeln der Doku: das System der Applikation mit seiner IP, die
    Gegenseite aus den Objekten der Regel bzw. den Gegenstellen des Contracts im Scope. Gleiche
    Verbindungen aus mehreren Regeln (Firewall, Contract) stehen einmal da.
  - Gegenstelle any, vzAny und Preferred Group bleiben Regeln ohne Verbindung.
- **Details je Hop:** die entscheidende Policy bzw. der Contract mit den Objekten (Quelle,
  Ziel, Dienste), die möglichen davor, Ein- und Ausgang, NAT und Leak zwischen VRFs.
- **Regel ↔ Weg:**
  - Je Regel der Doku: entscheidet, möglich, greift nicht (vorher entscheidet eine andere),
    nicht auf dem Weg (ihr Gerät liegt nicht auf dem Weg), nicht prüfbar (kein Weg bekannt).
  - **Abweichung**, wenn eine Regel erlaubt, aber der Weg sperrt bzw. sie nicht auf dem Weg
    liegt. Das ist der Fall „Regel zeigt Zugriff, Weg-Suche sperrt“. Abweichungen stehen zuerst,
    ein Filter zeigt nur sie und die Gesperrten.
- **Weg-Suche aus der Doku:** „In der Weg-Suche öffnen“ füllt Quelle, Ziel und Dienst aus und
  sucht gleich (`#wege?src=…&dst=…&service=…`).
- API `GET /applications/{id}/connections`; nicht für Kunden-Benutzer (zeigt die
  Infrastruktur).
- Intern: Weg-Suche und Regel-Prüfung laden Graph, Policies und Contracts für viele Wege nur
  einmal (`paths.trace(graph=, checks=, backups=)`, `policy_check.Checks`).

## 0.44.1 – 2026-10-23

Korrektur der Web-Oberfläche. Ohne Migration.

- **Listen mit Filtern:** Kam die Antwort einer älteren Anfrage nach der neueren (z. B. Filter
  gesetzt, bevor die ungefilterte Liste da war), überschrieb sie die gefilterte Liste. Jetzt
  zählt nur die Antwort zur letzten Anfrage – in Routen, BGP-Nachbarn und weiteren Reitern des
  Routings, Inventar, VMs, Portgruppen, Abgleich, NetBox-Sync und Kunden-Zugehörigkeit. Fiel
  in der CI als sporadisch fehlschlagender UI-Test auf.

## 0.44.0 – 2026-10-23

ACI zwischen VRFs und Tenants (Befund aus dem Test von 0.43.2: eine Regel zeigte in der
Applikations-Doku einen Zugriff auf ein System in einem anderen Tenant, die Weg-Suche sperrte
ihn). Migration `0046`; der Collector-Agent aktualisiert sich, danach die APICs neu einlesen.

- **Shared Services und Route-Leaking:**
  - Ein Contract zwischen EPGs verschiedener VRFs öffnet allein keinen Weg: das Fabric muss
    die Subnetze in die andere VRF leaken.
  - *Shared Service:* Das Subnetz ist geteilt (`fvSubnet.scope` mit `shared`, an der EPG
    bzw. ihrem BD), und ein Contract mit Scope `global` bzw. `tenant` verbindet die beiden
    VRFs.
  - *Route-Leaking* (`leakRoutes`, ACI 5.0+): Die VRF gibt das Subnetz an die andere VRF.
  - Neues Modul `store/aci_shared.py`; Weg-Suche und Applikations-Doku entscheiden damit
    gleich.
- **Weg-Suche:**
  - Liegt das Ziel in einer anderen VRF, die es in diese leakt, endet der Weg im Fabric:
    „Ziel im Fabric der VRF … – Shared Service …“ bzw. „Route-Leaking …“.
  - Der Hop zeigt „→ VRF …“. Der Contract wird mit der EPG des Ziels in dessen VRF geprüft.
  - Ohne Leak folgt der Weg wie bisher dem Routing der VRF (L3Out, Firewall).
- **Applikations-Doku:**
  - Ein Contract zwischen VRFs zählt nur mit dem Leak beider Seiten (Hin- und Rückweg).
  - Ohne Leak fehlt er in der Applikations-Doku. In der Doku je System steht er unter „nicht
    möglich“ mit der Begründung „kein geteiltes Subnetz … siehe Weg-Suche“.
  - Ohne Scope der Subnetze (Lauf vor 0.44.0) bleibt er „unbekannt“, bis neu eingelesen ist.
- **VRF eindeutig:**
  - Der Tenant der VRF kommt aus `fvRsCtx.tDn` (`epg.vrf_tenant`), z. B. eine VRF aus
    `common`.
  - vzAny vergleicht die aufgelöste VRF statt Name und Tenant.
- **Collector:** liest zusätzlich `fvSubnet.scope` der BDs und EPGs, den Tenant der VRF aus
  `tDn` und `leakRoutes` je VRF (`vrf_leaks`). Alles nur lesend.
- **UI:** Weg-Suche mit „→ VRF …“ und dem Grund am Hop; im EPG-Detail „geteilt“ an
  geteilten Subnetzen.

## 0.43.2 – 2026-10-23

Korrektur der Applikations-Doku (Befunde aus dem Test von 0.43.1). Ohne Migration.

- **Contracts nur mit den Beteiligten:**
  - Ein Contract (z. B. aus `common`) zeigt in der Applikation nur die eigenen EPGs bzw.
    VMs und die Gegenstellen, die über den Contract zugreifen können (Scope).
  - Bisher standen in der Regel-Liste alle Consumer bzw. Provider und im Regel-Detail jede
    EPG mit ihren VMs, bei Contracts aus `common` hunderte. Die Regel-Liste nennt jetzt dazu
    „von N im Contract“; das Detail blendet die übrigen aus, „alle zeigen“ holt sie zurück.
  - Je Treffer tragen die Daten die DNs der Beteiligten (`parties`).
- **Firewall mit VDOM:** Herkunft und Erklärung nennen bei Firewalls mit mehreren VDOMs den
  VDOM der Policy („fw-01 · VDOM kunde-a“), wie in Regeln & Contracts.

## 0.43.1 – 2026-10-23

Korrektur der Applikations-Doku (Befunde aus dem Test von 0.43.0: Regeln, bei denen nicht klar
war, warum sie zur Applikation gehören). Ohne Migration; der Collector-Agent aktualisiert sich.

- **Scope der Contracts:**
  - Ein Contract mit Scope VRF bzw. AP verbindet nur EPGs derselben VRF bzw. desselben AP. Das
    gilt auch, wenn er in `common` liegt und EPGs vieler Tenants ihn nutzen.
  - Consumer bzw. Provider ausserhalb des Scopes sind keine Gegenstelle mehr. Bleibt keine
    übrig, fehlt der Contract in der Doku.
  - Scope `tenant`: gleicher ACI-Tenant. Gleiche Prüfung in der Weg-Suche.
- **Firewall-Regeln einer EPG mit gemeinsamem BD:**
  - Teilt eine EPG ihren BD mit anderen EPGs, zählen Regeln zum BD-Subnetz nur, wenn sie das
    ganze Subnetz enthalten.
  - Ein Host bzw. Teilnetz darin zählt nur über die IPs der EPG selbst: ihre VMs
    (Portgruppe) und ihre Endpunkte. Bisher kam die Regel zum Host einer anderen EPG im
    gleichen BD dazu – sogar als „spezifisch“.
- **VMs:**
  - Die Portgruppe einer NIC wird über Name, VLAN und – beim Distributed Switch – den Switch
    bestimmt. Bisher nur über den Namen; eine gleichnamige Portgruppe mit anderem VLAN brachte
    eine fremde EPG.
  - Ist die Portgruppe mehrdeutig, gibt es keine EPG statt einer geratenen.
  - Der Collector liefert Standard-Portgruppen je Name und VLAN.
  - Gilt für Applikations-Doku, VM-Liste, Portgruppen, Kunden und NetBox-Sync.
- **Vorgabe vor Gelerntem (R12):** Für IPs an einer NIC mit EPG gilt die EPG der Portgruppe.
  Ein gelernter Endpunkt mit derselben IP in einer anderen EPG (alt, verschoben) bringt keine
  Contracts mehr. Das gilt auch in der Doku je System.
- **Applikations-Doku, Systeme:**
  - Je NIC steht die Portgruppe mit VLAN und EPG da, bei Zuordnung nur über das VLAN mit
    Hinweis.
  - Nennt der VM-Name ein anderes Projekt als das AP der EPG, steht ein Hinweis dabei, z. B.
    „Name nennt Projekt 000, die Portgruppe … gehört zur EPG …/AP_001/… (Projekt 001)“.
- Tests (Orchestrator, Collector, UI), Doku (BETRIEB, TESTS).

## 0.43.0 – 2026-10-23

Docker und Images aktuell halten (Anwender: „wie kann man den Docker gleich mit updaten,
damit er immer aktuell ist?“). Ohne Migration.

- **Images:** `deploy/update.sh` holt die Fremd-Images (Postgres, Redis, Keycloak, Caddy)
  und die Basis-Images der eigenen bei jedem Update neu (`pull --ignore-buildable`,
  `build --pull`). Es nimmt den neusten Stand im festen Tag, die Hauptversion bleibt in
  `docker-compose.yml`. Alte Image-Stände räumt es danach auf. Abschalten mit
  `AUTOMATO_PULL_IMAGES=0`; ist die Registry nicht erreichbar, baut es mit den vorhandenen.
- **Docker selbst:** `deploy/aufbau.sh pruefen` zeigt je installiertem Docker-Paket die
  Version und eine neuere aus der Paketliste. `deploy/aufbau.sh docker-update` hebt nur
  diese Pakete an und startet danach Automato und die lokale NetBox. Mit
  `AUTOMATO_DOCKER_UPDATE=1` in `.env` macht `deploy/update.sh` das bei jedem Update,
  nach der Sicherung. snap bzw. das Ubuntu-Paket `docker.io` werden erkannt.
- `--help` beider Skripte zeigt den ganzen Kopf. Doku (BETRIEB), Test mit Ersatz für
  apt-cache/apt-get/docker.

## 0.42.3 – 2026-10-23

Korrektur der Applikations-Doku (Befund: im Projekt eines Tenants standen Contracts anderer
Tenants, die weder Tenant noch VRF der Applikation betreffen). Ohne Migration.

- EPGs, die nur über die IP passen (gelernter Endpunkt, damit verknüpfter Server), zählen
  nur in den VRFs der Portgruppen der VM. Ohne Portgruppe gelten die VRFs der Applikation,
  sofern eine passt. Gleiche Adressen in anderen Tenants bzw. VRFs bringen keine fremden
  Contracts mehr.
- Externe EPGs anderer VRFs (z. B. 0.0.0.0/0) zählen für Systeme in einer EPG nicht mehr –
  in der Applikations-Doku und in der Doku je System.
- Jeder Treffer eines ACI-Contracts nennt die EPG, über die er gilt; der Filter „EPG“ zeigt
  die Contracts eines Systems nur über diese EPG.
- Test mit zwei weiteren Tenants (gleicher EPG-Name, gleiche IP, externe EPG 0.0.0.0/0).

## 0.42.2 – 2026-10-23

Korrektur der Prüfung (Befund nach dem Update auf 0.42.0). Ohne Migration.

- `deploy/aufbau.sh pruefen` meldet Container, die laufen, aber nicht in den Profilen von
  `deploy/update.sh` sind (`AUTOMATO_COMPOSE_PROFILES`) – sie blieben bei jedem Update auf
  altem Stand, hier der Collector. Abhilfe in `.env`:
  `AUTOMATO_COMPOSE_PROFILES=--profile core --profile collectors --profile proxy`.
- „Health starting“ direkt nach dem Start gilt nicht mehr als Warnung.
- Frühere ufw-Sperren, deren Ziele jetzt antworten, stehen nur noch als Hinweis da.
- Scheitert `docker compose ps` im NetBox-Ordner, steht die Meldung dabei (und ob NetBox
  trotzdem antwortet).

## 0.42.1 – 2026-10-23

Korrektur (Befund 0.42.0: `deploy/update.sh` brach in der Sicherung mit „service "netbox" has
neither an image nor a build context specified“ ab – die NetBox-Override-Datei lag im
Automato-Ordner). Ohne Migration.

- `deploy/update.sh` prüft vor der Sicherung, ob `docker compose` das Projekt lesen kann, und
  nennt bei einem Fehler die VM-spezifische `docker-compose.override.yml` als Ursache.
- `deploy/aufbau.sh pruefen`: neuer Abschnitt Compose-Konfiguration – Automato- und
  NetBox-Projekt gültig, sonst die Meldung mit Hinweis auf die Override-Datei.
- Doku: Fehlerbild in BETRIEB; Test für die ungültige Konfiguration.

## 0.42.0 – 2026-10-23

Sicherung einmal einrichten (Befund 0.41.0: `deploy/update.sh` brach ab – „AUTOMATO_BACKUP_AGE_
RECIPIENT fehlt“). Ohne Migration.

- **Einstellungen aus `.env`:** `deploy/update.sh`, `deploy/aufbau.sh`, `deploy/backup/backup.sh`
  und `restore.sh` lesen `AUTOMATO_BACKUP_AGE_RECIPIENT`, `…_AGE_IDENTITY`, `…_DIR`,
  `…_KEEP_DAYS`, `…_PLAIN` und `AUTOMATO_COMPOSE_PROFILES` auch aus `.env` (`deploy/env.sh`:
  nur bekannte Schlüssel, gelesen statt ausgeführt; die Umgebung geht vor). Ein Update
  vergisst damit keine Angabe.
- **`deploy/aufbau.sh backup-schluessel`:** legt die age-Identität an (bzw. nimmt die
  vorhandene, nur für root lesbar) und trägt Empfänger und Pfad in `.env` ein.
- **`deploy/aufbau.sh pruefen`:** neuer Abschnitt Sicherung – Empfänger, age, Identität für
  den Rückfall, Alter der letzten Sicherung.
- `deploy/update.sh` nennt ohne Empfänger gleich den Weg (`backup-schluessel`).
- Tests: Schlüssel einrichten, wiederholt aufrufen, Werte aus `.env`, Umgebung geht vor.

## 0.41.0 – 2026-10-23

Neustart-Regeln und NetBox im Aufbau-Skript (Befund 0.40.0: nach einem Neustart der VM liefen
die Automato-Container wieder, NetBox nicht – „ConnectTimeout“ beim Einlesen und im Sync).
Ohne Migration.

- **`restart: unless-stopped`** für alle Automato-Dienste in `docker-compose.yml` (postgres,
  redis, orchestrator, collector, web-ui, keycloak; proxy und agent-gateway hatten sie schon).
  Wirkt mit dem nächsten `deploy/update.sh` bzw. `docker compose up -d`.
- **NetBox-Setup:** `setup.sh` schreibt die Neustart-Regel für alle Dienste der vorhandenen
  netbox-docker-Version ins Override (nur Dienste aus `docker compose config --services` –
  ein unbekannter Dienst liesse `up` scheitern).
- **`deploy/aufbau.sh`:**
  - `pruefen` meldet zusätzlich, ob Docker beim Booten startet, welche Container keine
    Neustart-Regel haben und – bei einer NetBox auf derselben VM – ob ihre Container laufen
    und die Login-Seite antwortet (Hintergrunddienste wie `netbox-worker` nur als Warnung).
  - neu `netbox-start`: startet die lokale NetBox, setzt die Neustart-Regel
    (`docker update`) und wartet, bis sie antwortet – ohne `setup.sh` neu auszuführen (das
    würde neue Tokens erzeugen).
- Doku: Fehlerbild „NetBox aus nach Neustart“ (BETRIEB, NetBox-README).
- Tests: Aufbau-Skript mit NetBox aus, ohne Neustart-Regel, `netbox-start`.

## 0.40.0 – 2026-10-23

Verbindungen prüfen – im Aufbau-Skript und unter System (Anwender, Befund: ufw sperrte den Weg
vom Container zu NetBox). Ohne Migration.

- **System → Verbindungen:** „Jetzt prüfen“ vom Orchestrator-Container aus, auf dem Weg von
  Einlesen und NetBox-Sync:
  - Datenbank, Redis (Warteschlange zum zentralen Worker), Keycloak (JWKS bei OIDC),
  - jedes zentral gelesene Quellsystem (Name, TCP, bei NetBox `/api/status/` mit dem
    Lese-Token, nur lesend; Schreib-Token für den Sync hinterlegt?),
  - Agent-Systeme als Hinweis je Agent.

  Je Ziel die Schritte und die Abhilfe. Antwortet ein Dienst auf dem Docker-Host nicht, steht
  die schmale ufw-Regel zum Kopieren da (Container-Netz aus der Routing-Tabelle, Gateway-
  Adresse, Port). API `GET /system/connections` (admin), CLI `automato check-connections
  [--json]`.
- **`deploy/aufbau.sh`** (nach dem Muster von Magisters `plattform-aufbau.sh`): `pruefen`
  (Werkzeuge, Docker, Container, Health, Verbindungen aus dem Container, ufw aktiv und Sperren
  aus Docker-Netzen der letzten Stunde), `firewall [--anwenden]` (nötige ufw-Regeln zeigen bzw.
  setzen und erneut prüfen), `status`. `deploy/update.sh` ruft am Ende `pruefen` auf (Hinweis,
  kein Abbruch).
- Doku: Fehlerbild „NetBox ConnectTimeout“ mit der Ursache ufw und der Abhilfe.
- Tests: Verbindungen (Netz aus `/proc/net/route`, ufw-Regel, Agent-Hinweis, Rechte, Redis-PING,
  CLI), Aufbau-Skript mit Ersatz für `docker compose`, UI (Karte mit Regel zum Kopieren).

## 0.39.0 – 2026-10-23

Weg-Suche: ein Netz als Ganzes prüfen (Anwender: „ob das Netzwerk in die Richtung geroutet wird
und ob das Subnetz über die Firewalls offen ist“). Ohne Migration.

- **Netz als Quelle bzw. Ziel** (`10.10.0.0/23`, aus der Namenssuche oder direkt) statt einer
  Stellvertreter-Adresse:
  - **Routing:** die Route, die das ganze Netz enthält (längstes solches Präfix). Genauere
    Routen innerhalb des Netzes mit anderem Next-Hop stehen am Hop („Teile anders geroutet“)
    und als Hinweis oben.
  - **Firewall-Policies:** sicher nur, wenn ein Objekt das ganze Netz enthält (`10.10.0.0/23`
    in `10.10.0.0/16` = offen); nur teilweise erfasst = „möglich (Quelle/Ziel nur
    teilweise)“, das Urteil wird „abhängig“. Policy-Routen bzw. SD-WAN-Regeln ebenso.
  - **ACI:** EPG bzw. externe EPG über das Subnetz, das das ganze Netz enthält; nur Teile =
    Zuordnung nicht sicher.
  - Kein DNAT für ein Netz; eine Quelle muss ganz in einem angeschlossenen Netz liegen.
- `GET /routing/trace` und `GET /routing/lookup` nehmen auch ein Netz; die Namenssuche liefert
  für Netze das Netz selbst (`ip` = CIDR) statt einer geprüften Adresse.
- Tests: Route und Policy für ein Netz (Beispiel /23 in /16), nur teilweise, Netz über Firewall
  und ACI, Lookup; UI (Netz mit „nur teilweise“ und anders gerouteten Teilen).

## 0.38.0 – 2026-10-23

Weg-Suche mit Netzen, IPv4/IPv6-Paare im Rahmen, Applikationen (Anwender). Ohne Migration.

- **Weg-Suche – Netze und vollständige Trefferliste:** Die Namenssuche bei Quelle und Ziel
  findet neben Hosts auch **Netze**:
  - IPAM-Netze (Name, Beschreibung bzw. Anfang des Netzes),
  - Netz-Objekte der Firewalls (ohne 0.0.0.0/0),
  - Subnetze des Inventars (FortiGate-Interfaces, ACI-BD/-EPG über Tenant, AP, EPG, BD),
  - ein Netz direkt (`10.20.0.0/24`).
  Die Liste ist nach **Hosts** und **Netzen** gegliedert, mit Anzahl je Art und Umschalter
  (Alle/Hosts/Netze), zeigt „x von y“ und lädt mit **„Weitere laden“** alle Treffer
  seitenweise nach (statt der ersten 20). Auch der Anfang einer Adresse sucht (`10.20.0.`).
  Ein Netz wird mit **einer Adresse daraus** geprüft – der ersten, die keinem gelesenen
  Gerät gehört (Interface, Gateway); sie steht beim Feld und im Ergebnis
  („Netz 10.20.0.0/24 (geprüft mit 10.20.0.2)“). `GET /routing/resolve` antwortet mit
  `hits`, `total`, `counts`, `capped` (Parameter `type`, `limit`, `offset`).
- **IPv4/IPv6-Paare in „Regeln & Contracts“:** beide Regeln eines Paars stehen in einem
  **Rahmen** (oben und unten durchgezogen, dazwischen gestrichelt), je Paar abwechselnd
  getönt, mit der Marke **„Paar“** (Vorschlag: „Paar?“, gelb) – auch viele Paare
  nacheinander bleiben unterscheidbar.
- **Applikationen:** die Vorschläge aus der Namenskonvention stehen **unter** der Liste.
- **Applikations-Doku – Findings nach Tenant, AP, EPG filtern:** je Befund die EPGs, zu
  denen er gehört (Server/VM über ihre EPGs, Subnetz über seine EPG, Regel über die Systeme,
  die sie trifft); Auswahl Tenant → AP → EPG mit „x von y“.
- Tests: Netze und Seiten der Namenssuche, EPGs der Befunde, UI (Netze und Umschalter in der
  Weg-Suche, Paar-Rahmen, Lage der Vorschläge, Findings-Filter).

## 0.37.0 – 2026-10-23

Applikations-Doku: offene Regeln nur als Regel (Anwender). Ohne Migration.

- **Unspezifische Regeln** (any bzw. grosse Netze, vzAny, Preferred Group – typisch Zugriffe
  ins oder aus dem Internet bzw. MPLS) stehen nur als Regel mit ihrem Objekt (`any`,
  `N_srv16 (10.20.0.0/16)`) statt mit allen Systemen, die sie erfasst – in beiden Richtungen.
  Welche Systeme der Applikation sie nutzen können, zeigt der Knopf **„▸ N Systeme“**:
  - in „Regeln & Contracts“ unter Quelle bzw. Ziel,
  - in der Doku nach Kategorien in der Spalte „Server Name“ (`_systems` je Zeile),
  - im Markdown-Export als Anzahl statt der Liste.
- Spezifische Objekte (Server, Gruppe, EPG) nennen ihre Systeme weiter direkt.
- Tests: Kategorien mit Objekt und Systemliste, UI (Knopf in Regeln und Kategorien, öffnet
  nicht das Detail).

## 0.36.0 – 2026-10-23

Objekte, Namenskonvention, IPv4/IPv6-Paare und Kunden-Editor (Anwender, „Weitere Arbeiten“
Punkte 3–10). Mit Migration `0045` (Datenmigration: `FT_` im Profil „default“); für die
Angaben dynamischer Objekte die FortiGates neu einlesen (Agent aktualisiert sich selbst).

- **IPv4/IPv6-Paare zusammen:** In „Regeln & Contracts“ steht die IPv6-Regel eines Paars
  direkt unter ihrer IPv4-Regel (auch seitenweise), als zusammengehörend markiert – grün
  gleich/bestätigt, gelb Vorschlag („ähnlich“).
- **Typ-Filter für Objekte:** Auswahl nach dem Typ wie auf dem Gerät mit Anzahl, z. B.
  **dynamisch (SDN-Connector)** für die Objekte der ACI-Integration über den FortiManager.
  Der Collector liest dazu `sub-type`, `sdn`, `filter`, `obj-tag` dynamischer Adressen
  (Positivliste).
- **Standardobjekte des Herstellers** (FortiOS-Services wie `HTTPS`/`ALL`, ACI-Filter in
  `common`): zuunterst unter eigener Überschrift, gedämpft, „Standard“ statt grünem Haken,
  „Standardobjekte ausblenden“. Neuer Befund **„Standardobjekt verwendet“**
  (`predefined_service_used`), wenn eine erlaubende Regel eines verwendet (direkt bzw. über
  eine Gruppe).
- **Objekt öffnen** filtert auch die Liste dahinter auf genau dieses Objekt
  (`GET /objects?object_id=`).
- **Namenskonvention:**
  - `FT_` steht in „Objekt-Prefixe → Typ“ mit dem neuen Typ **„Service / ACI-Filter“** und
    ist dort pflegbar (bisher fest eingebaut); die Prüfung der Services und Filter nimmt die
    Präfixe von dort.
  - Die **Projektnummern** sind aus der Namenskonvention entfernt (ausgeblendet, Daten
    bleiben).
- **Kunden-Editor:** breite Maske, Felder in voller Breite, Werte ganz lesbar, kein
  Querscrollen.
- Tests: Paar-Reihenfolge (auch seitenweise), Standardobjekte (Reihenfolge, Ausblenden,
  Befund direkt und über Gruppe, nicht bei Deny), Typ-Filter und Anzahl, Objekt-Filter,
  eigenes Service-Präfix im Profil, Collector (SDN-Attribute); UI (Standardobjekte, Typ
  dynamisch, Objekt öffnen filtert, Paar zusammen, FT_ im Namensprofil ohne Projektnummern,
  Kunden-Editor ohne Querscrollen).

## 0.35.0 – 2026-10-23

Weg-Suche als eigene Ansicht und Dienste mit Objektnamen (Anwender, „Weitere Arbeiten“
Punkte 1 und 2). Ohne Migration; die Regel-Liste zeigt die Namen für FortiGate-Regeln nach dem
nächsten Lauf, für ACI-Filter sofort.

- **Weg-Suche** steht als eigener Punkt in der Navigation (`#wege`), nicht mehr als Reiter
  im Routing. Die Ansicht „Routing & Wege“ heisst jetzt **Routing**. „Als Anbindung erfassen“
  springt aus der Weg-Suche ins Routing (Reiter Anbindungen, vorausgefüllt).
- **Quelle und Ziel als Name:** IP oder Name bzw. FQDN. Vorschläge ab zwei Zeichen aus dem
  IPAM (DNS-Name, auch nur das erste Label), den Adressobjekten der Firewalls (auch ohne
  Präfix; FQDN-Objekte über das IPAM) und den VMs. Eindeutige Namen werden direkt aufgelöst,
  bei mehreren IPs fragt die Suche nach (`GET /routing/resolve?q=`).
- **Dienst als Objekt:** `FT_https` bzw. `Tenant/FT_https` (Service-Objekt der FortiGate bzw.
  ACI-Filter, Gruppen aufgelöst). Bei mehreren Diensten wird der erste geprüft, ein Hinweis
  nennt die übrigen.
- **ACI-Contracts auf dem Weg:** Je ACI-VRF-Hop die Zuordnung von Quelle und Ziel (EPG im
  Fabric bzw. externe EPG über das längste zuordnende Subnetz) und das Urteil: gleiche EPG
  erlaubt, Taboo sperrt zuerst, sonst ein Contract Consumer → Provider (direkt, vzAny, ESG,
  Preferred Group) mit passendem Filter; kein Contract = gesperrt. Das Gesamturteil zählt
  Firewalls und ACI-VRFs („erlaubt (1 Firewall, 1 ACI-VRF)“). `store/aci_check.py`.
- **Dienste mit Objektnamen** statt Werten: Regel-Liste, Regel-Detail (Werte darunter),
  Applikations-Doku (Spalte TCP/UDP) und Weg-Suche zeigen `FT_https` bzw. `HTTPS` wie auf der
  Firewall statt `tcp/443`; ACI-Filter ohne den Tenant davor. Ohne Objekt (Werte direkt in
  der Regel) bleiben die Werte.
- Tests: Contracts je VRF-Hop (externe EPG, Taboo, vzAny, gleiche EPG, mehrdeutige
  Zuordnung, ohne Quelle), Dienst als Objekt, Namenssuche, Dienstnamen in Liste und Weg; UI
  (Weg-Suche als Ansicht, Ziel als Name mit Vorschlag, mehrdeutiger Name, Dienst als Objekt).

## 0.34.0 – 2026-10-23

Befunde aus dem Test von 0.33.0. Ohne Migration; für die Management-Adresse die FortiGates
neu einlesen (Agent aktualisiert sich selbst).

- **NetBox nicht erreichbar:** neu **Quellsysteme → Prüfen** für zentral gelesene Systeme.
  Die Prüfung läuft vom Orchestrator aus in Sekunden: Name auflösen, TCP-Verbindung, bei
  NetBox `GET /api/status/` mit dem Lese-Token. Jeder Schritt zeigt einen Hinweis, z. B.
  Firewall/Docker-Netz bei keiner Antwort bzw. Port bei abgelehnter Verbindung
  (`POST /systems/{id}/check`). Der Verbindungsaufbau zu NetBox bricht nach 10 statt 30 s
  ab, die Meldung kommt nach rund 1,5 statt 3 Minuten. Die Meldungen nennen den Grund
  (ConnectTimeout = keine Antwort, ConnectError = abgelehnt bzw. Name unbekannt).
  Abhilfe-Weg in `docs/BETRIEB.md`.
- **Seitenkopf fixiert:** Titel und Reiter (Objekte, Routing) bleiben beim Scrollen stehen.
- **Geräte:** FortiGates haben eine Management-Adresse – die IPv4 des aktiven Interfaces mit
  HTTPS-Zugriff (`allowaccess`), Management-Interfaces zuerst.
- **Finding „Portgruppe: Name ≠ EPG“** (`portgroup_name_mismatch`): Portgruppe nach
  ACI-Schema (`Tenant|AP|EPG`), die nur über das VLAN einer anders benannten EPG zugeordnet
  ist. Die Zuordnung über das VLAN bleibt.
- **Routing:**
  - Spalte VRF an den Routen und ein Filter nach VRF bzw. Management-Tabelle
    (`GET /routing/routes?vrf=`, VRFs in `/routing/summary`).
  - Suche und System-Filter in BGP-Nachbarn (dazu Zustand), HA, Policy-Routen/SD-WAN
    (dazu Art) und L3Outs.
- **Applikations-Doku, Kategorien:**
  - Neue Spalten **Aktion** (Allow/Deny) und **Gerät** (Firewall mit VDOM bzw. APIC); die
    Spalte „Bezug“ entfällt.
  - **IPv4/IPv6-Paare** (gleich bzw. bestätigt) stehen als eine Zeile.
  - **Klick auf die Regel** öffnet sie im Detail, bei einem Paar je Familie.
  - **NAT nur aus der Regel:** Internet-Zugriff mit dem SNAT der Policy (ihr Pool bzw.
    „Interface-IP“) statt allen Pools des Mandanten. Zugriff aus dem Internet mit der VIP, auf
    die die Regel zeigt (externer DNS-Name aus dem IPAM). Sperrende Regeln haben kein NAT.
    Eine VIP zum Server macht eine Regel aus dem eigenen Netz nicht mehr zu „Zugriff aus dem
    Internet“.
- **Applikations-Doku ohne unmögliche Regeln:** Routingtechnisch unmögliche Treffer, z. B.
  intern → Internet mit `any` im Ziel, stehen nicht mehr in der Applikation. Sie sind auch
  nicht einblendbar, nur gezählt.
- **Kunden-Übersicht:** Die Zugehörigkeit verlinkt auf Regel, Objekt, VM, NAT, Netz bzw.
  Finding.
- **Roadmap:** „Jede Verbindung einmal, Ende zu Ende“ über Firewalls und ACI mit der
  Reihenfolge der Regeln (Applikationen ausbauen).
- Tests: Verbindungsprüfung, Management-Adresse, Portgruppen-Befund, VRF-Filter, NAT je Regel
  (Pool, VIP, Deny), Kategorien-Spalten, IPv4/IPv6-Paar, UI (Kategorien mit Link,
  Routing-Filter, Kunden-Links).

## 0.33.0 – 2026-10-22

Kunden-Benutzer mit serverseitiger Einschränkung (ADR 0018, Schritt 3). Ohne Migration.

- **Kunden-Benutzer** tragen in Keycloak die Rolle `customer` bzw. Kunden-IDs im Attribut
  `customer_ids` (Claim `AUTOMATO_OIDC_CUSTOMERS_CLAIM`). Sie sind nie Admin (auch nicht mit
  der Rolle `admin`), immer auf ihre Mandanten und Kunden eingeschränkt; ohne Kunden sehen sie
  nichts (fail-closed). Bestehende Benutzer ohne Attribut bleiben Betreuer.
- **Freigabeliste (fail-closed):** Kunden-Benutzer erreichen nur die Lese-Endpunkte der Doku
  (`auth/customer_routes.py`): Regeln, Findings (Triage eigener mit `operator`), NAT,
  Objekte, VMs, Portgruppen, Doku je System, Applikationen, ihre Kunden, Mandanten,
  Standorte und die Bezeichnungen der Quellsysteme. Alles andere – auch neue Endpunkte –
  ergibt 403: Quellsysteme, Agenten, Läufe, NetBox-Sync, Abgleich, Routing & Wege, Inventar,
  System, Verwaltung.
- **Einschränkung je Endpunkt:** Listen und Zähler auf den Ausschnitt; Details ausserhalb
  ergeben 404 (Regel, NAT, Objekt, Portgruppe, System, Applikation, Finding). Regel-Zahlen
  und Regel-Listen bei Objekten, NAT und in der Doku nennen nur die eigenen Regeln, Findings
  ohne Namen fremder Regeln (ADR 0018: Gegenstellen sichtbar, aber nur die eigenen Regeln).
- **Geteilter Teil präzisiert** (gilt auch für den Kunden-Filter der Betreuer): EPGs, Netze und
  Objekte des geteilten Teils zählen für alle Kunden; Regeln, NAT, Findings und VMs nur aus
  den eigenen Kunden – eine Regel von Kunde B zum geteilten DNS betrifft Kunde A nicht.
- **`GET /systems/labels`:** nur Name, Plattform, VDOMs und Verbund der Quellsysteme; für
  Kunden-Benutzer nur die Systeme ihrer Regeln, NAT bzw. Objekte und nur ihre VDOMs.
- **Lese-Audit** für Kunden-Benutzer immer an (`read.*` mit den Kunden-IDs), auch ohne
  `audit_reads` des Mandanten; neu auch für Objekte, VMs/Portgruppen und Kunden.
- **Web-UI:** Kunden-Benutzer sehen Übersicht (ohne Betriebszustand und Läufe), Regeln,
  Objekte (ohne Netzinventar), NAT, Applikationen (ohne Pflege) und Findings; keine
  Administration, kein Routing. Rolle „Kunde“ im Benutzermenü. Im dev-Modus lässt sich ein
  Kunden-Benutzer vorführen (`localStorage automato_dev_customer = "<Mandant>:<Kunden>"`).
- **Keycloak:** Rolle `customer` und Mapper `customer_ids` in `realm-automato.json`; für
  SAML-Broker Kunden-IDs und Rolle fest je IdP (`saml-mappers.example.json`), nie aus der
  Assertion. Anleitung in `deploy/keycloak/PROD-INSTALL.md` (9a/9b).
- Tests: Ausschnitt je Endpunkt (Listen, Details, Zähler, Triage), ohne Kunden/fremder
  Kunde/Admin-Rolle, **jeder Endpunkt der App** gegen die Freigabeliste, Audit, OIDC-Claims,
  UI als Kunden-Benutzer.

## 0.32.0 – 2026-10-22

Kunden-Filter in den Ansichten (ADR 0018, Schritt 2). Ohne Migration.

- **Umschalter „Kunde“** in der Kopfzeile (sobald die Umgebung Kunden hat): „alle“ bzw. ein
  Kunde, je Umgebung gemerkt. Gefiltert werden Übersicht und Navigation, Regeln & Contracts,
  Objekte, NAT, Findings, VMs und Portgruppen, Doku je System und Applikationen.
- Sichtbar ist der Ausschnitt des Kunden **samt geteiltem Teil** (z. B. Tenant common).
- Applikationen gehören über ihre Mitglieder dazu (AP mit einer EPG des Kunden, EPG bzw.
  Server des Kunden); Firewall-Objekte über ihre Werte bzw. die Regeln des Kunden.
- **API:** `customer_id` an `GET /rules`, `/rules/summary`, `/findings`, `/findings/summary`,
  `/nat`, `/objects`, `/vms`, `/portgroups`, `/appdoc/systems`, `/applications`; ein Kunde
  eines anderen Mandanten ergibt 404. Gefiltert wird per SQL (`store/customer_scope.py`).
- Tests (jede Liste mit Kunde und geteiltem Teil, Applikationen, fremder Kunde, UI).

## 0.31.0 – 2026-10-22

Kunden: Merkmale aus dem IPAM, NAT und Findings (ADR 0018). Ohne Migration.

- **Selektoren aus dem IPAM:** IPAM-Tenant (NetBox-Tenant), IPAM-VRF bzw. DDI-Netzansicht,
  IPAM-Tag bzw. Extensible Attribute (`key=value`). Netze, Bereiche und Adressen des
  Referenzstands mit diesem Merkmal zählen als Netze des Kunden – ein- und
  ausschliessend. Gelesen wird nur, was die IPAM-Adapter schon liefern (ADR 0002).
- **NAT:** VIPs bzw. SNAT-Pools, deren interne oder externe IP in einem Netz des Kunden
  liegt, gehören zum Kunden.
- **Findings** gehören dem Kunden über ihr Subjekt: Regel, Werte- bzw. Firewall-Objekt, VM,
  Portgruppe (Tenant im Namen) bzw. Subnetz des Kunden. Nach „Abgleich neu berechnen“ wird
  die Zugehörigkeit ebenfalls neu abgeleitet.
- Ansicht Kunden: Spalten NAT und Findings, Zugehörigkeit je Art.
- Tests (Tenant, VRF ohne Gross/klein, Tag, Attribut, Ausschluss, NAT über die interne IP,
  Finding über die Regel bzw. das Objekt).

## 0.30.0 – 2026-10-22

Kunden als Ausschnitt der Umgebung (ADR 0018, Schritt 1). Alembic `0044`.

- **Kunden mit Selektoren** (Administration → Kunden, Operator): statt Listen „gehört dazu,
  wenn …“.
  - ACI-Tenant, VRF, Application Profile, EPG, Bridge Domain; VDOM, Zone, Interface (je
    Firewall); Netz; Kürzel aus der Namenskonvention.
  - Je Selektor einschliessen oder ausschliessen, z. B. „Tenant TE_ka ohne AP TE_ka/AP_900“
    bzw. ein Netz ohne ein Teilnetz.
  - Ein Kunde kann der **geteilte Teil** der Umgebung sein (z. B. Tenant common).
- **Zugehörigkeit abgeleitet** – nach jedem Lauf, beim Speichern und nach dem Löschen eines
  Quellsystems, nie von Hand:
  - EPGs; Netze (Präfixe, Subnetze der EPGs, Netze an zugeordneten Interfaces);
    Werte-Objekte in diesen Netzen;
  - Regeln (Contracts mit einer EPG des Kunden; Firewall-Regeln im VDOM, an Zone bzw.
    Interface oder mit Quelle bzw. Ziel im Netz des Kunden, ohne `any`);
  - VMs (Portgruppe einer EPG, IP im Netz, Kürzel im Namen).
  - Ein Element darf mehreren Kunden gehören. Neue EPGs bzw. Netze gehören nach dem nächsten
    Lauf ohne Pflege dazu.
- **Vorschau** beim Bearbeiten (Anzahl und Beispiele je Art), **Zugehörigkeit je Art** mit
  Grund, **Vorschläge** je ACI-Tenant (Kürzel aus `TE_<kürzel>`, `common` als geteilter
  Teil) und **keinem Kunden zugeordnet** (EPGs, Regeln, VMs).
- **API:** `/customers` (CRUD, `preview`, `suggestions`, `unassigned`, `{id}/members`), Audit
  `customer.created|updated|deleted`.
- Messskript: Ableitung bei 3000 Regeln und 9500 Objekten rund 0,7 s.
- Tests (Selektoren, Ausschluss, Kürzel, neuer Lauf, Löschen, Rechte, UI).
- Noch nicht: Kunden-Filter in den Ansichten (Schritt 2), Kunden-Benutzer (Schritt 3).

## 0.29.0 – 2026-10-22

Verbund bzw. Session-Sync-Paar als ein Hop im Weg (ADR 0019 Punkt 4/5). Ohne Migration.

- **Ein Weg statt mehrerer:** Wege, die sich nur in den Firewalls eines Verbunds
  unterscheiden, sind ein Weg. Bisher standen sie als „redundant · Verbund“ nebeneinander.
  - Der Verbund ist darin ein Hop „Verbund Nord“. Je Firewall steht eine Zeile mit Eingang,
    Ausgang mit Next-Hop und Regeln.
  - Der Hop davor zeigt die Next-Hops zu beiden Firewalls; das Ende nennt beide („… bzw. …“).
  - ECMP über verschiedene Router bleibt je Weg; Backup-Wege werden genauso zusammengefasst.
- **Regeln:** Urteilen die Firewalls des Verbunds gleich, gilt das Urteil über alle. Sonst
  ist der Weg „abhängig“ mit „im Verbund verschieden – fw-a: …; fw-b: …“.
- **API:** `GET /routing/trace` mit `hop.parts` (je Firewall bzw. Variante) und
  `end.also`.
- Tests (ECMP in einen Verbund, vier WAN-Router an einem Verbund, verschiedene Urteile, UI),
  Beispieldaten der UI-Tests mit Verbund „Süd“.

## 0.28.0 – 2026-10-22

ACI-Contracts über externe EPGs nach der Zuordnung im ACI (ADR 0019 Punkt 6). Alembic `0043`.

- **Systeme ausserhalb des Fabrics** (z. B. hinter der Firewall) bekommen in der Doku je
  System und in der Applikations-Doku die Contracts der externen EPG, der das ACI ihre IP
  zuordnet. Bezug: „externe EPG L3O_fw|EN_srv (10.20.0.0/24)“.
- **Zuordnung wie im ACI:**
  - Es zählen Subnetze externer EPGs mit Scope `import-security` (APIC-Standard).
    Subnetze, die nur das Routing steuern (`export-rtctrl` …), ordnen nichts zu.
  - Je VRF gewinnt das längste passende Subnetz – über alle L3Outs. Contracts weniger
    genauer externer EPGs (z. B. 0.0.0.0/0) sind für diese IP „nicht möglich“, mit
    Begründung.
  - Eine IP im Fabric der VRF (BD-/EPG-Subnetz, Endpunkt) gehört zu ihrer EPG: Externe EPGs
    fehlen in ihrer Doku; in der Regel-Liste ist diese Seite „nicht möglich“. Liegt die IP auf
    der anderen Seite in einer EPG, bleibt der Contract sichtbar („unbekannt“).
  - 0.0.0.0/0 in mehreren L3Outs: möglich ist die EPG des L3Outs, über den die VRF die IP
    leitet. Die übrigen bleiben sichtbar („unbekannt“), nicht ausgeschlossen.
- **Regel-Liste:** Bei der Suche nach einer IP tragen auch Contracts die Relevanz
  („für <IP> nicht möglich“, ausblendbar wie in 0.26.0).
- **Collector (APIC):** liest `l3extSubnet.scope` mit (nur lesend, dieselben Objekte).
  Gespeichert in `epg_subnet.scope`; Regel-Detail zeigt „nur Routing“.
- Tests (Zuordnung, Gleichstand nach Routing, Fabric, Regel-Liste, Collector, UI), Doku
  (ADR 0019, BETRIEB, Lesevertrag).

## 0.27.0 – 2026-10-22

Inter-VDOM-Links im Weg (ADR 0019 Stufe 3). Ohne Migration.

- **Weg über VDOM-Links:** Geht eine Route ohne Next-Hop-IP über einen Inter-VDOM-Link,
  führt der Weg im VDOM am anderen Ende weiter („über VDOM-Link vl_root_dmz0 ↔
  vl_root_dmz1“). Bisher endete er dort als „angeschlossen“.
  - Paare: `<name>0` ↔ `<name>1` derselben FortiGate (vdom-link bzw. NPU-vlink), Enden in
    verschiedenen VDOMs. VLAN-Subinterfaces auf den Enden paaren sich über Parent und VLAN-ID.
  - Physische Ports in verschiedenen VDOMs sind keine Kante.
  - Hat der Link IPs, gilt wie bisher der Besitzer der Next-Hop-IP.
  - Regeln auf dem Weg prüfen den Link als Aus- bzw. Eingang der VDOMs.
- **Collector:** FortiGate-Interfaces vom Typ `vdom-link` tragen die Art `vdom-link` (bisher
  `virtual`).
- Doku: veralteter Hinweis „Transfernetz (vermutet)“ in BETRIEB korrigiert (gilt seit 0.17.1
  nicht mehr).

## 0.26.0 – 2026-10-22

Relevanz in der Regel-Liste (ADR 0019 Punkt 6). Ohne Migration.

- **Suche nach einer IP:** Ist die Suche in „Regeln & Contracts“ genau eine IP, prüft Automato
  jede gefundene Firewall-Regel gegen das Routing ihrer Firewall, wie in der Doku (0.24.0).
  - Ziel: Leitet die Firewall die IP über den Ausgang der Regel?
  - Quelle: Kommt Verkehr von der IP über den Eingang der Regel an?
  - Regeln, die die IP nicht sehen können, tragen „für <IP> nicht möglich“ mit Begründung,
    z. B. eine Regel für das DMZ-Netz am Ausgang internet.
  - „N routingtechnisch unmögliche ausblenden“ blendet sie aus (über alle Regeln, nicht nur
    die geladene Seite).
- **API:** `GET /rules?q=<IP>` mit `relevance` je Regel und `X-Impossible-Count`;
  `routable=true` blendet aus.
- Test-Firewall um eine Regel mit falschem Ausgang ergänzt.

## 0.25.0 – 2026-10-22

Gegenstelle `any` in der Doku (ADR 0019 Punkt 6). Konzeptbefund: `any` aus dem Internet wirkte
wie eine Liste aller Gegenstellen. Ohne Migration.

- **Statt „any“** nennen Doku je System und Applikations-Doku, was hinter Interface bzw. Zone
  der Regel auf der Seite der Gegenstelle liegt – nach dem Routing ihrer Firewall:
  - eine erfasste **Anbindung**, z. B. „all → Internet Provider (internet)“;
  - sonst die **Default-Route** über dieses Interface: „all → extern (Default-Route über
    wan)“;
  - sonst die **Netze dahinter** (angeschlossen und geroutet), z. B. „all → Netze hinter lan:
    10.10.0.0/24“ (die ersten drei, dann „+n“).
- **Kategorie:** Liegt hinter der Gegenstelle eine Internet-Anbindung, steht eine eingehende
  Regel unter „Zugriff aus dem Internet“ (2) und eine ausgehende unter „Internet Zugriff“ (1).
  Eingehend gilt das jetzt auch für Gegenstellen, die im IPAM als extern bekannt sind.
- Ohne gelesenes Routing bzw. bei `any` als Interface bleibt es bei „any“.

## 0.24.0 – 2026-10-22

Regeln nur, wo der Verkehr durchkommt (ADR 0019 Punkt 6, Relevanz in der Doku). Konzeptbefund:
Ohne Routing zeigte die Doku Zugriffe, die routingtechnisch nicht möglich sind. Ohne Migration.

- **Relevanz je Regel und System:** Für jede Firewall-Regel, die ein System erfasst – direkt,
  über ein Netz, eine Gruppe oder `any` –, prüft Automato das Routing ihrer Firewall bzw. ihres
  VDOMs.
  - *Ziel:* Leitet die Firewall den Server (bzw. das EPG-Subnetz) über den Ausgang der Regel
    (`dstintf`, Interface oder Zone)?
  - *Quelle:* Kommt Verkehr vom Server über den Eingang der Regel an (`srcintf`;
    symmetrisches Routing)?
  - Passt keines, ist der Treffer **routingtechnisch nicht möglich**, mit Begründung, z. B.
    „fw leitet 10.30.0.10 über dmz (Zone dmz) – die Regel gilt nur für den Ausgang internet“.
  - Im Zweifel bleibt er sichtbar (**unbekannt**): Routing nicht gelesen, keine Route,
    Interface bzw. Zone unbekannt, Policy-Route bzw. SD-WAN, die anders leiten kann. Regeln
    mit `any` als Interface sind immer möglich.
- **Doku je System:** Unmögliche Treffer fehlen in den 10 Kategorien und im Export. Sie stehen
  oben unter „Routingtechnisch nicht möglich“ (Regel, als Quelle bzw. Ziel, Begründung).
- **Applikations-Doku:**
  - Unmögliche Treffer sind ausgeblendet; „N routingtechnisch unmögliche einblenden“ zeigt
    sie, mit „nicht möglich“ in der Spalte Bezug und dem Grund in der Erklärung.
  - Kennzahlen, Kategorien und Export zählen nur Mögliche bzw. Unbekannte. Die Übersicht nennt
    „N nicht möglich“.
- **API:**
  - `GET /appdoc/systems/{name}` mit `impossible`;
  - Applikations-Doku mit `relevance`/`relevance_text` je Treffer und `summary.impossible`.
- **Beispieldaten der UI-Tests:** zusätzlich fw-08 (LAN, DMZ, Internet mit Routing).

## 0.23.0 – 2026-10-22

NAT auf dem Weg (ADR 0019 Stufe 3). Ohne Migration.

- **DNAT (VIP):** Ist das Ziel die externe IP einer VIP einer FortiGate auf dem Weg, geht der
  Weg ab diesem Hop zur **internen IP** weiter.
  - Erkannt werden einzelne IPs und Bereiche (Bereich auf Bereich mit gleichem Abstand) sowie
    das Interface der VIP (`extintf`, sonst jedes).
  - Bei einem Virtual Server stehen alle Real Server am Hop; der Weg folgt dem ersten.
  - Am Hop steht „DNAT VIP_x: extern → intern“, bei Port-Weiterleitung mit den Ports. Der
    nächste Hop zeigt das Ziel nach NAT; die Übersicht markiert den Hop mit „DNAT“.
- **Regeln am NAT-Hop:**
  - Für die Policy gilt das ursprüngliche Ziel, also die VIP.
  - Bei Port-Weiterleitung gilt der interne Port, wie auf der FortiGate. Ab dort ist auch der
    Dienst für die weiteren Firewalls übersetzt.
- **SNAT:** Erlaubt eine Policy mit NAT, ist die neue Quelle die erste Adresse des IP-Pools
  bzw. die IP des Ausgangs-Interfaces. Sie gilt für die Firewalls danach („SNAT → …“ in der
  Spalte „Regeln“). Ist die Adresse unbekannt, sind die Quellen danach nur „möglich“.
- **API:** `GET /routing/trace` mit `nat`, `dst` und `snat` je Hop.

## 0.22.0 – 2026-10-22

Regeln auf dem Weg (ADR 0019 Stufe 3, Regel-Relevanz je Hop, erster Teil). Ohne Migration.

- **Weg für IP** prüft je Firewall bzw. FortiGate-VDOM auf dem Weg die Policies – nur aus
  den gelesenen Daten.
  - Geprüft werden Eingang und Ausgang des Hops (Interface bzw. Zone, `any`), Quelle, Ziel und
    optional der **Dienst** (neues Feld, z. B. `tcp/443`, `udp/53`, `tcp`, `icmp`).
  - Die Reihenfolge ist die der Firewall: Die erste Policy, die sicher passt, entscheidet
    (**erlaubt** bzw. **gesperrt**). Passt keine, ist der Verkehr **implizit gesperrt**.
  - **Möglich** ist eine Policy, wenn keine Bedingung verletzt, aber eine nicht prüfbar ist:
    Quelle oder Interface unbekannt (Start gewählt), FQDN/ISDB/Geo, Benutzer/Gruppen,
    Zeitplan, Dienst nicht angegeben. Hat eine mögliche Policy davor eine andere Aktion, ist
    der Hop **abhängig** – mit dem Grund.
  - Abgeschaltete Policies zählen nicht; negierte Quelle/Ziel/Dienst werden umgekehrt.
- **Anzeige:**
  - Je Hop die Spalte „Regeln“ mit Urteil, entscheidender Policy und den möglichen davor;
    „Policy n“ öffnet die Regel.
  - Je Weg „Regeln: erlaubt / gesperrt an fw-x / abhängig an …“ in der Karte und in der
    Übersicht.
  - Ohne gelesene Policies heisst es „unbekannt“.
- **Grenzen:**
  - NAT (VIP, SNAT) ändert die Adressen auf dem Weg nicht.
  - ACI-Contracts werden auf dem Weg noch nicht geprüft.
- **API:** `GET /routing/trace?service=` mit `rules` je Hop und `policy` je Weg.

## 0.21.0 – 2026-10-22

Erkannte Anbindungen ausblenden (Alembic `0042`). Dev-Befunde 0.20.0.

- **Ausblenden:** Jeder Vorschlag unter „Erkannte Anbindungen“ hat neben „übernehmen“ den
  Knopf **ausblenden** (Operator, im Audit), wenn es keine Anbindung ist.
  - Ausgeblendet werden die offenen Router des Vorschlags, für alle Benutzer.
  - Kommt später ein neuer Router dazu, erscheint der Vorschlag wieder.
  - „N ausgeblendete Vorschläge“ zeigt sie; **wieder anzeigen** hebt das Ausblenden auf.
  - Die Wege ändern sich dadurch nicht.
- **Reihenfolge:** „Erkannte Anbindungen“ steht jetzt zu unterst, unter den erfassten
  Anbindungen.
- **Sprung zum Formular:** „übernehmen“, „ergänzen“, „ändern“, „erkannte Router übernehmen“
  und „Als Anbindung erfassen“ springen hoch zum Formular, der Name ist bereit zur Eingabe.
- **API:**
  - `POST /routing/exits/dismiss` und `/routing/exits/dismiss/undo` mit `ips`;
  - `GET /routing/exits/suggestions?dismissed=` mit `dismissed` je Vorschlag und Router.

## 0.20.0 – 2026-10-22

Erkannte Anbindungen nach aussen mit Redundanz, Anbindung für mehrere Firewalls, Länge der
Wege im Vergleich (Alembic `0041`). Dev-Befunde 0.19.0: Bei vier WAN-Routern mit BGP zeigte die
Anbindung nur den ersten Router; eine Anbindung kann auch den Session-Sync-Partner betreffen.

- **Erkannte Anbindungen** (Routing & Wege → Anbindungen): Automato sucht die Gegenstellen
  selbst, nur aus den gelesenen Daten.
  - Gegenstellen sind Next-Hops aus der Routing-Tabelle, aus BGP-Pfaden und aus eingeschalteten
    statischen Routen sowie BGP-Nachbarn, die keinem gelesenen Gerät gehören. Die
    Management-Tabelle zählt nicht mit.
  - Je Router: **aktiv** (Präfixe in der Routing-Tabelle), **Backup** (nur als BGP-Pfad bzw.
    statische Route) oder nur BGP-Nachbar; dazu AS und Zustand der BGP-Sitzung, Default-Route,
    Präfixe und je Firewall Interface und Zone. Namen kommen aus dem IPAM.
  - **Zusammengefasst** wird zu einem Vorschlag:
    - Router, die sich Firewalls teilen;
    - Router derselben aktiven Route (ECMP);
    - Router am selben Interface bzw. an derselben Zone;
    - BGP-Nachbarn mit derselben AS – je Firewall bzw. je Verbund und Session-Sync-Partner
      (FGSP).
  - Beispiel: vier WAN-Router mit BGP an beiden Firewalls eines Verbunds ergeben einen
    Vorschlag mit vier Routern, „2 aktiv (ECMP ×2) · 2 Backup · 2 Firewalls“.
  - **übernehmen** füllt das Formular mit allen Routern, Firewalls und Zonen sowie Name und Art
    (geraten aus Zone, Namen, Default-Route bzw. BGP). Deckt eine Anbindung schon einen Teil ab,
    heisst der Knopf **ergänzen** und erweitert diese.
  - **Als Anbindung erfassen** im Weg nimmt ebenfalls die ganze erkannte Anbindung, nicht mehr
    nur den einen Router.
- **Anbindung für mehrere Firewalls:** Statt einer Firewall sind mehrere wählbar, z. B. beide
  Session-Sync-Partner; ohne Wahl gilt sie für alle. Wird eine Firewall gelöscht, fällt sie
  aus ihren Anbindungen; galt eine Anbindung nur für sie, entfällt die Anbindung.
- **Formular:** Unter „Erkannte Router“ stehen alle Router der Anbindung mit Zustand zum
  Ankreuzen, dazu „alle Router übernehmen“.
- **Liste der Anbindungen:** Die neue Spalte „Erkannte Router · Redundanz“ zeigt alle Router,
  die die Anbindung abdeckt (über Next-Hop bzw. Interface/Zone, wie im Weg).
  - Router, die erkannt, aber nicht eingetragen sind, tragen den Hinweis „nicht eingetragen“;
    „N erkannte Router übernehmen“ trägt sie ein.
  - Eingetragene Router, die in keiner Route stehen, erscheinen als „nicht erkannt“.
- **Weg:** Endet ein Weg an einer Anbindung mit mehreren Routern, nennt das Ende den Router,
  z. B. „weiter über WAN Nord – Router 2 von 4“.
- **Länge der Wege:** Die Übersicht hat eine Spalte „Länge“ mit „gleich lang“ bzw. „+1 Hop“
  gegenüber dem kürzesten Weg mit Ziel; jede Karte trägt denselben Hinweis.
- **API:**
  - `GET /routing/exits/suggestions?covered=`;
  - `GET /routing/exits` mit `status` (Router, `missing`, `unseen`, `redundancy`);
  - `source_system_ids` statt `source_system_id`;
  - `GET /routing/trace` mit `extra` je Weg.

## 0.19.0 – 2026-10-22

Verbund ablehnen und redundante Wege (Alembic `0040`). Dev-Befunde 0.18.0; abgenommen:
Regeln paralleler Firewalls korrekt und nur einmal, keine Schleifen mehr.

- **Verbund ablehnen:** Neben „zusammenfassen“ gibt es **Ablehnen**: Die Firewalls sind nicht
  parallel und werden nicht mehr vorgeschlagen.
  - Im Regel-Detail löst **Verbund auflösen** einen Verbund wieder auf (Regeln wieder einzeln),
    ebenfalls ohne neuen Vorschlag.
  - „N abgelehnte Vorschläge“ zeigt die Ablehnungen; **wieder vorschlagen** hebt eine auf.
  - Ein von Hand gesetzter Verbund hebt die Ablehnung seiner Firewalls ebenfalls auf.
  - Alles nur Admin, im Audit.
- **Redundante Wege** (Weg für IP): neben den aktiven Wegen (ECMP, parallele Firewalls eines
  Verbunds) die **Backup-Wege** mit gleicher oder fast gleicher Länge.
  - Backup-Weg: eine Abweichung an einem Hop über einen BGP-Pfad bzw. eine eingeschaltete
    statische Route für dasselbe Präfix mit anderem Next-Hop, z. B. zweiter Provider oder
    schwebende Route mit höherer Distanz.
  - Länge: Auswahl „Backup: gleich lang / bis +1 Hop (Standard) / bis +2 Hops / ausblenden“.
    Längere Backup-Wege und solche ohne Ziel nennt ein Hinweis.
  - Neue **Übersicht** „Wege im Überblick“: Art (aktiv · ECMP, redundant · Verbund, Backup ab
    Knoten mit Grund), Knoten nacheinander, Next-Hops, Anzahl Hops, Ende. Dazu je Weg eine
    Karte, am abweichenden Hop „Backup“ mit Präfix und Grund.
- **API:**
  - `POST /systems/parallel-reject` (Firewalls bzw. `group`) und `/systems/parallel-reject/undo`;
  - `GET /systems/parallel-suggestions?include_rejected=`;
  - `GET /routing/trace?slack=` mit `kind` (active/backup), `deviation`, `parallel` je Weg.

## 0.18.0 – 2026-10-22

Parallele Firewalls und Anbindungen nach aussen (Alembic `0039`, ADR 0020, ADR 0019 Stufe 4
erster Teil). Dev-Befunde 0.17.0.

- **Parallele Firewalls (Verbund):** Firewalls mit denselben Regeln (z. B. zwei Cluster mit
  Session-Sync) bilden einen Verbund.
  - Gleiche Policies stehen nur einmal in Liste, Suche, Kennzahlen und Applikations-Doku. Führend
    ist die Firewall mit dem kleinsten Namen, dazu „+ fw-b“.
  - Gleich heisst: gleiche Policy-ID im selben VDOM (sonst gleicher Name) und gleicher Inhalt.
    Verglichen werden Quelle, Ziel, Dienst, Aktion, Interfaces, Status, Zeitplan, NAT,
    Schutzprofile, Logging und Name; uuid und Kommentar nicht.
  - **Findings** (Kategorie „Parallele Firewalls“): Policy abweichend (mit Feldern und Werten),
    Policy fehlt auf einer Firewall, gleichnamiges Objekt anders definiert.
  - Verbund setzen:
    - Vorschlag oben in „Regeln & Contracts“ bei Session-Sync (FGSP) bzw. wenn mindestens die
      Hälfte der Policies gleich ist; „zusammenfassen“ ist Admin.
    - Sonst im Quellsystem-Dialog (Feld „Verbund“).
  - Filter „Verbund: abweichend / fehlt“; mit Filter auf eine Firewall erscheinen deren Regeln
    vollständig. Im Regel-Detail stehen der Verbund und die Firewalls, auf denen die Regel gleich
    steht.
- **Anbindungen nach aussen** (Routing & Wege, Reiter „Anbindungen“, Operator): Internet, MPLS,
  WAN oder Partner von Hand erfassen.
  - Felder: über Next-Hop-IPs und/oder Interfaces/Zonen (für eine Firewall oder alle), Netze
    dahinter, manuelle Routen (Präfix → Firewall bzw. VRF eines anderen Standorts).
  - Der Weg endet an der Anbindung („weiter über Internet“) statt an „Next-Hop gehört keinem
    gelesenen Gerät“. Mit einer manuellen Route geht er weiter, z. B. über MPLS zur Firewall des
    anderen Standorts.
  - Endet ein Weg an einem unbekannten Next-Hop, gibt es „Als Anbindung erfassen“ mit Firewall,
    Interface und Next-Hop vorausgefüllt.
- **API:**
  - `GET /systems/parallel-suggestions`, `POST /systems/parallel-group`, `parallel_group` an
    `/systems`;
  - `GET /rules?parallel=differs|missing&twins=`, `parallel` an der Regel, `twins` im Detail;
  - `GET/POST/PUT/DELETE /routing/exits`.
- Nach dem Update ist nichts neu einzulesen. Der Verbund wirkt sofort, sobald er gesetzt ist.

## 0.17.1 – 2026-10-22

Korrekturen aus dem Test von 0.17.0 (keine Migration). Die parallelen Firewalls (Regeln
zusammenfassen, Abweichungen als Findings) und Anbindungen nach aussen (Internet, MPLS, WAN)
folgen mit 0.18.0.

- **Läufe robuster** (Dev-Befund: „nicht erreichbar … ConnectTimeout“, „Ergebnis-Upload
  fehlgeschlagen: … ReadTimeout“):
  - Lesen von FortiGate, APIC, DDI und NetBox wird bei Timeouts, abgebrochenen Verbindungen und
    502/503/504 bis zu dreimal wiederholt (nach 5, 15 und 30 s). Das ist gefahrlos, es wird nur
    gelesen.
  - Der Agent wartet bis zu 10 Minuten auf die Quittung des Ergebnis-Uploads (bisher 30 s;
    das Einspielen dauert mit mehreren Firewalls länger) und sendet bei Netzproblemen erneut.
  - Der Orchestrator spielt ein Ergebnis für einen schon abgeschlossenen Lauf nicht noch einmal
    ein, er quittiert dessen Stand. Ein zweiter Upload wartet, bis der erste fertig ist. Bisher
    konnte die Fehlermeldung „Ergebnis-Upload fehlgeschlagen“ einen eingespielten Lauf auf
    „failed“ setzen.
  - Teil-Uploads der Referenzquellen werden nur wiederholt, wenn sie den Orchestrator nicht
    erreicht haben (sonst gäbe es Doppelte).
- **VRF der FortiGate** (Dev-Befund: Default-Route in der Management-VRF): Routen gelten je VRF.
  Bisher ergaben die Default-Routen beider VRFs zusammen ein ECMP.
  - Die Weg-Suche folgt der VRF des Eingangs; ohne Eingang der Standard-VRF 0.
  - Routen über ein dediziertes Management-Interface sind eine eigene Tabelle und nie ein Weg
    für den Datenverkehr.
  - „Weg für IP“ (nur Ziel) zeigt weitere VRFs als eigene Zeile, in der Routen-Liste steht die
    VRF unter „Weitere Angaben“.
- **Keine Schleifen mehr über geratene Transfernetze:** Gehört der Next-Hop keinem gelesenen
  Gerät (z. B. dem Aussenrouter), endet der Weg dort. Bisher folgte er einer anderen Firewall
  am selben Netz, und zwei Firewalls am selben Aussenrouter ergaben „Schleife“. Sekundär-IPs
  der FortiGate-Interfaces zählen jetzt als Adressen der Firewall.
- Nach dem Update FortiGates neu einlesen (VRF, Sekundär-IPs).

## 0.17.0 – 2026-10-22

Weg Hop für Hop mit Startpunkt (ADR 0019 Stufe 3, erste Fassung), Bezeichnungen und Namen aus
dem IPAM (keine Migration). Dev-Befunde 0.16.0.

- **Weg für IP mit Quelle und Startpunkt:** Quelle (IP) und/oder Startpunkt (Firewall, VDOM
  bzw. ACI-VRF), dazu das Ziel. Der Weg beginnt am gewählten Startpunkt bzw. dort, wo die
  Quelle angeschlossen ist (angeschlossenes Netz, sonst EPG-/BD-Subnetz im ACI), und folgt Hop
  für Hop:
  - Eine Policy-Route bzw. SD-WAN-Regel, die sicher passt (Quelle, Ziel, Eingang als Subnetz
    bzw. Interface), geht vor. Über Adressobjekte bestimmte steht als „möglich“ dabei.
  - Sonst gilt das längste Präfix der Routing-Tabelle; ECMP verzweigt in mehrere Wege.
  - Der nächste Knoten ist der, dem der Next-Hop gehört (Interface-IP der FortiGate,
    SVI/Interface eines L3Outs). Sonst ist es ein Knoten mit diesem Transfernetz („vermutet“).
  - Alle Border-Leafs einer VRF sind ein Knoten (vier Anbindungen = ein Hop).
  - Je Hop: Eingang und Ausgang mit Zone, Route bzw. Policy-Route und Next-Hop mit Namen aus
    dem IPAM.
  - Ende: Ziel angeschlossen, Ziel im Fabric (EPG/BD), extern (Default-Route ohne gelesenen
    Nachbarn), Next-Hop ohne gelesenes Gerät, keine Route, Schleife.
  - Ohne Quelle und Start bleibt es bei der bisherigen Sicht: je Kontext die wirksame Route.
- **Bezeichnungen:** Eine FortiGate nur mit `root` erscheint als **Firewall** (ihr Name),
  sonst als „fw · VDOM x“. ACI erscheint als „leaf · VRF Tenant:VRF“. Das gilt für alle
  Routing-Reiter und den Kontext-Filter der Routen (jetzt mit dem Namen der Firewall; ACI-VRF
  über alle Border-Leafs). Auch Regeln, Objekte, NAT und Ports zeigen „VDOM root“ nur noch
  bei Firewalls mit weiteren VDOMs (`vdoms` je Quellsystem in `GET /systems`). Bisher reichte
  dafür irgendeine Firewall mit VDOMs auf der Seite.
- **Nachbarn mit Namen aus dem IPAM:** BGP-Nachbarn, Next-Hops, Policy-Routen-Gateways,
  Session-Sync-Peers, L3Out-Peers und die Next-Hops statischer L3Out-Routen zeigen den Namen
  aus DDI/NetBox. Ohne Namen zur IP erscheint das IPAM-Netz mit Beschreibung (z. B.
  „Transfer ISP B“). Dazu kommt, falls gelesen, der Knoten hinter der Adresse (z. B. die ACI-VRF
  hinter dem BGP-Nachbarn der Firewall). L3Out-Border-Leafs tragen ihren Namen statt
  `node-101`.
- **Korrektur:** SD-WAN-Regeln haben bei Quelle/Ziel Adressobjekte, keine Subnetze. Bisher
  fielen sie bei „Weg für IP“ deshalb weg. Jetzt stehen sie als „möglich“ dabei.
- **API:** `GET /routing/trace?dst=&src=&start=`. Die Routing-Antworten tragen `label` bzw.
  `display` und die IPAM-Namen (`names`, `network`). `GET /routing/summary` liefert die
  Knoten für den Startpunkt (`nodes`).

## 0.16.1 – 2026-10-22

Korrektur des Selbst-Updates (keine Migration). Dev-Befund 0.16.0: Das Update von 0.15.0 auf
0.16.0 brach mit „Startpruefung fehlgeschlagen … cannot import name 'selfupdate'“ ab. Nach
pip fehlte eine Datei im venv (vermutlich zwei Agent-Prozesse gleichzeitig – geplante
Aufgabe und ein Start von Hand – oder der Virenschutz). Der Agent lief mit der alten Version
weiter, wäre beim nächsten Start aber nicht mehr hochgekommen.

- **Nur ein Update gleichzeitig** je Installation (`update.lock`); eine Sperre älter als 30 min
  (Abbruch) wird übernommen.
- **Nach pip prüfen, ob alle Dateien der Wheels im venv stehen**; fehlt eine, einmal neu
  installieren. Fehlt sie danach immer noch, nennt die Meldung die Datei und rät, Virenschutz
  bzw. zweiten Agent-Prozess zu prüfen.
- **Rückfall:** Scheitert das Update, installiert der Agent die zuletzt guten Pakete wieder
  (`packages\` im Installationsordner, abgelegt von `install.ps1` und jedem erfolgreichen
  Update).
- **Selbstheilung beim Start:** `run-agent.ps1` prüft vor dem Start, ob der Agent importierbar
  ist, und installiert sonst die Pakete aus `packages\` neu.
- **Einmalig von Hand:** Ein Agent, dessen Update auf 0.16.0 so abgebrochen ist, startet nach
  dem Ende des laufenden Prozesses nicht mehr. Das Agent-Paket im Web-UI neu herunterladen und
  `install.ps1` in einer administrativen PowerShell ausführen (BETRIEB, Fehlerbehebung).

## 0.16.0 – 2026-10-22

Routing, HA und L3Out lesen (ADR 0019 Stufe 2, Alembic `0038`); Agent aktualisiert sich vor
dem Lauf. Der Test gegen die echten Systeme steht noch aus.

- **Erster Lauf nach einem Update mit der neuen Version** (Dev-Befund 0.15.0: es brauchte zwei
  Läufe): Ein Agent mit alter Version bekommt neue Läufe erst nach seinem Update. Ab dieser
  Version aktualisiert er sich dann sofort statt nach bis zu 5 Minuten. Wartet ein Lauf
  länger als 10 Minuten (Update scheitert, Selbst-Update aus), holt ihn auch die alte Version.
- **FortiGate je VDOM** (nur lesend):
  - wirksame Routing-Tabelle IPv4/IPv6, ECMP als mehrere Next-Hops;
  - alle BGP-Pfade, auch nicht beste (Backup-Wege), und die BGP-Nachbarn mit Zustand;
  - statische Routen, auch inaktive bzw. mit höherer Distanz;
  - Policy-Routen und SD-WAN-Regeln;
  - global: HA-Cluster (Modus, Gruppe, Mitglieder mit Rolle, Session-Pickup) und
    Session-Sync zu anderen Clustern (FGSP).
- **ACI:** L3Outs mit Border-Leafs (Router-ID, statische Routen), Interfaces bzw. SVIs (vPC-Seiten,
  Encap/VLAN, Adressen), BGP-Peers und externen EPGs; je Border-Leaf und VRF die
  Routing-Tabelle und die BGP-Nachbarn.
- **Keine Secrets:** Felder werden per Positivliste übernommen – das HA-Passwort, der
  FGSP-Schlüssel und das Passwort der ACI-BGP-Peers kommen nie mit. Die BGP-Konfiguration der
  FortiGate wird nicht gelesen (nur der Monitor).
- **Was nicht lesbar ist** (Profil ohne Recht), steht in der Lauf-Meldung („Routing nicht
  lesbar: …“) und je Kontext in der neuen Ansicht.
- **Neue Ansicht „Routing & Wege“:**
  - gelesene Kontexte mit Anzahl Routen und dem, was fehlt;
  - **Weg für IP:** je Kontext die wirksame Route (längstes Präfix) mit Next-Hops,
    Interface und Zone, dazu Policy-Routen bzw. SD-WAN-Regeln, die vorgehen (sicher bzw.
    möglich);
  - Routen (wirksam, BGP-Pfade, statisch; Suche, „enthält IP“), BGP-Nachbarn, HA &
    Session-Sync, Policy-Routen & SD-WAN, L3Outs.
- **API:** `GET /routing/summary`, `/routing/routes`, `/routing/lookup?ip=`, `/routing/peers`,
  `/routing/ha`, `/routing/policy-routes`, `/routing/l3outs`.
- Nach dem Update FortiGates und APIC einmal neu einlesen.

## 0.15.0 – 2026-10-22

VDOM-Kontext der FortiGate (ADR 0019 Stufe 1, Alembic `0037`). Der Test gegen die echten
VDOMs steht noch aus.

- **Collector liest alle VDOMs:** zuerst die Liste (`system/vdom`), dann jede Tabelle je VDOM
  (`vdom=`). Bisher las er nur den Standard-VDOM des Kontos.
  - Ohne Recht auf die Liste (VDOM-Admin) bleibt es beim Standard-VDOM; sein Name kommt aus
    der Antwort.
  - Ein VDOM ohne Leserecht fehlt und steht in der Lauf-Meldung („VDOM nicht lesbar: …“).
  - Interne VDOMs (`vsys_*`) werden nicht gelesen.
- **Regeln, Objekte, VIPs/IP-Pools, Interfaces und Subnetze tragen ihren VDOM.** Policy-ID,
  Objekt- und VIP-Name gelten je VDOM; gleiche Namen in zwei VDOMs sind zwei Objekte mit
  eigener Regel-Zahl. Verweise, Gruppen, VIP-Ziele, NAT-Nutzung und Server hinter einer VIP
  bleiben im VDOM der Regel.
- **IPv4/IPv6-Paare** nur innerhalb eines VDOMs. Entscheidungen von vor 0.15.0 (ohne VDOM)
  gelten weiter für jeden VDOM der Firewall; eine neue Entscheidung gilt für den VDOM des
  Paares.
- **Findings:** doppelte Werte je VDOM; Objekte ausserhalb von `root` tragen den VDOM im Namen
  des Befunds („H_x (VDOM kunde-a)“) und im Satz („auf fw-01 (VDOM kunde-a)“).
- **Gleicher VIP-Name, andere externe IP** (zwei VDOMs bzw. Firewalls): eigenes Werte-Objekt je
  IP. Bisher zeigte eine der Regeln die externe IP der anderen.
- **Oberfläche:**
  - Regeln: VDOM unter dem System und Filter „VDOM“ (sobald es mehrere gibt); Suche nach dem
    VDOM-Namen.
  - Regel-Detail: VDOM mit Policy-ID.
  - Objekte, NAT / VIPs und Port-Detail: VDOM.
  - Applikations-Doku: Firewall-Objekte mit VDOM.
  - Der Standard-VDOM `root` erscheint nur, wenn es mehrere VDOMs gibt.
- **API:** `vdom` in Regeln, Objekten, NAT und Interfaces; `GET /rules?vdom=`; Kennzahl
  `by_vdom` in `/rules/summary`.
- Nach dem Update **jede FortiGate einmal neu einlesen**; erst dann haben Regeln und Objekte
  ihren VDOM.

## 0.14.1 – 2026-10-22

Korrekturen aus dem Betrieb (keine Migration).

- **NAT / VIPs zu breit, kein Scrollbalken:** lange Listen (NAT / VIPs, Regeln & Contracts,
  Regeln der Applikation) scrollen in ihrer Karte mit fester Kopfzeile – der waagrechte
  Scrollbalken ist immer im Bild. Server im NAT je Zeile statt mit Komma.
- **Applikation → Systeme → Firewall-Objekt** je Name zusammengefasst: darunter die
  Firewalls, auf denen das Objekt steht (Klick öffnet es), und ob der Name zum DNS-Namen im
  IPAM passt (✓ bzw. „Name ≠ DNS“).
- **Regeln der Applikation zeigen das Objekt der Regel:** Quelle und Ziel wie in der Regel, darunter je
  Objekt, über das die Regel Systeme der Applikation erfasst (`H_web01 10.10.10.5`,
  `N_srv16 10.20.0.0/16`, `any`, `EPG Tenant/AP/EPG`), die Systeme darin. So ist sichtbar, ob
  die Regel für die VM steht oder für ein Subnetz bzw. any.
- **Keine Mehrfach-Zeilen mehr über any bzw. ein Netz:** In den 10 Kategorien steht eine Regel je
  Seite und Objekt einmal (`any: web01, db01, …` statt einer Zeile je System). Auch das
  Regel-Detail fasst so zusammen.

## 0.14.0 – 2026-10-22

Applikationen mit der Doku im Mittelpunkt, Prüfung in NAT / VIPs, VM-Angaben in den
Detailansichten (Alembic `0036`).

- **Applikationen** (ADR 0012, Ergänzung 0.14.0):
  - **Klick auf eine Applikation öffnet ihre Doku** (`#apps?app=<id>`). Mitglieder und
    Stammdaten werden von dort gepflegt.
  - Neue Felder: **externer Name**, **interner Name**, **Verwendungszweck** (Dev, Test, Prod).
  - **Übersicht** oben: VMs, EPGs, Regeln **spezifisch** bzw. **unspezifisch** (Subnetz, any,
    vzAny, Preferred Group) und offene Findings; Klick filtert die Regeln.
  - **Systeme = alle VMs der Applikation** (Portgruppen ihrer EPGs, VMs hinter ihren Servern)
    und Server ohne VM, mit IPs/DNS, EPG, Portgruppe, Cluster/Host, Firewall-Objekt (Link),
    Standort und dem Grund, warum sie dazugehören. Darunter die **EPGs im ACI** (BD, VRF,
    Subnetze, VMs, Endpunkte).
  - **Regeln & Contracts** wie in der Regel-Ansicht: Aktion (allow/deny, auch bei ACI und
    L3Outs), **Herkunft** (Firewall bzw. ACI mit Quellsystem), Richtung, System der
    Applikation, Gegenstelle, Dienste, Bezug, Kategorie. Filter **System/VM**, **EPG**,
    **Richtung aus Sicht des Systems** (eingehend/ausgehend), Art, Bezug, Suche.
  - **Regel-Detail mit Erklärung**: je System Rolle (Ziel/Quelle bzw. Provider/Consumer),
    Bezug, Kategorie und über welches Mitglied bzw. AP es zur Applikation gehört; Name des
    Contracts bzw. der Firewall-Regel mit Link, „Findings dazu“.
  - **Ganzer ACI-Pfad** `Tenant/AP/EPG` statt nur `EG_…` – als System, Gegenstelle und in den
    10 Kategorien (auch in der Doku je System).
  - Findings der Applikation (Regeln, Server, VMs, Portgruppen, Subnetze der EPGs) mit Links.
  - Nennt eine Regel das System auf einer Seite gezielt, gilt any auf der Gegenseite nicht
    mehr als eigener Zugriff (Server → any erscheint nur noch ausgehend).
- **NAT / VIPs: Korrektheitsprüfung** (Spalte „Prüfung“, Schalter „nur Abweichungen“,
  `GET /nat?issues_only=`): Befunde des VIP- bzw. Pool-Objekts wie in den Objekten
  (Namenskonvention, unbenutzt, nicht im IPAM) und „Ziel nicht im IPAM“ (Mapped-IP bzw. Real
  Server). Im Detail „Objekt öffnen“ und die VM hinter dem internen Ziel.
- **VM-Angaben in den Detailansichten:** Regel-, Objekt- und NAT-Detail zeigen je VM Status,
  Gast-OS, Hostname, alle IPs, Netzwerkkarte/MAC, Portgruppe → EPG, Cluster/Host,
  Datacenter, vCPU/RAM/Disk und Notiz.
- **Läufe** stehen jetzt unter Administration.
- Direktlinks: `#objekte?object_id=<id>` öffnet das Objekt-Detail, `#findings?q=<Text>` sucht
  in den Findings.

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
