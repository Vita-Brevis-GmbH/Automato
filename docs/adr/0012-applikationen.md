# ADR 0012 — Applikation als eigenes Objekt

## Status

Angenommen (2026-10-20).

**Ergänzung 0.10.0 (Dev-Befund „in den Applikationen werden teils zu wenige Regeln angezeigt“):**
Die Doku je System – und damit je Applikation – erfasst alle Regeln, gleichwertig angezeigt.
- **Server:** Firewall-Regeln direkt und alle, deren Quelle oder Ziel die IP enthält – über
  Netze, Bereiche, Gruppen und any (Regelindex, ADR 0016). Dazu die ACI-Contracts der EPG,
  in der der Server als Endpunkt gelernt ist.
- **EPG:** Contracts direkt, über vzAny der VRF (auch im Tenant `common`) und über ESGs mit
  EPG-Selektor. Dazu alle Firewall-Regeln, die sich mit den Subnetzen bzw. Endpunkten der
  EPG überschneiden.
- Die Spalte „Bezug“ nennt, wie die Regel greift (direkt, Objekt/Netz, any, vzAny, ESG, EPG).
  Ist ein System Quelle und Ziel (z. B. any → any), erscheint die Regel ein- und ausgehend.

**Ergänzung 0.14.0 (Dev-Befund „die Doku ist im Mittelpunkt“):**
- **Klick auf eine Applikation öffnet ihre Doku** (`#apps?app=<id>`); Mitglieder und
  Stammdaten werden von dort gepflegt. Neue Stammdaten ohne Funktionsrelevanz: externer und
  interner Name, Verwendungszweck (Dev, Test, Prod; Alembic `0036`).
- **Systeme = alle VMs der Applikation:** VMs an den Portgruppen ihrer EPGs und VMs hinter
  ihren Servern (gleiche IP), dazu Server ohne VM. Je System steht, **warum** es dazugehört
  (Mitglied, Portgruppe → EPG, IP = Server). Darunter die **EPGs im ACI** mit ganzem Pfad
  `Tenant/AP/EPG` (auch als Server Name bzw. Gegenstelle in den Tabellen, statt nur `EG_…`).
- **Treffer je Regel, System und Seite** mit Richtung aus Sicht des Systems (eingehend = Ziel
  bzw. Provider, ausgehend = Quelle bzw. Consumer), Bezug und **Art des Bezugs**:
  - *spezifisch*: das System bzw. die EPG selbst – direkt, über eine Gruppe, die EPG, ein ESG
    oder ein Netz innerhalb des EPG-Subnetzes;
  - *unspezifisch*: über ein grösseres Netz bzw. einen Bereich (Subnetz), any, vzAny der VRF
    oder die Preferred Group.
  Eine Regel zählt in der Übersicht mit ihrem engsten Bezug. Nennt eine Regel das System auf
  einer Seite gezielt, gilt any auf der Gegenseite nicht als eigener Zugriff.
- Die Regeln erscheinen wie in „Regeln & Contracts“ (Aktion, Herkunft Firewall/ACI mit
  Quellsystem, Quelle, Ziel, Dienste, NAT), filterbar nach System/VM, EPG, Richtung, Art und
  Bezug. Das Regel-Detail erklärt je Treffer, warum die Regel für die Applikation gilt, und
  verlinkt Regel, Objekte und Findings. Die 10 Kategorien bleiben für Export und Druck.
- Dazu die offenen Befunde der Applikation (Regeln, Server, VMs, Portgruppen, Subnetze der
  EPGs). Alles rein lesend.

**Ergänzung 0.60.0 (Applikationen ausbauen, ROADMAP):**
- **Regeln direkt zuordnen bzw. ausschliessen.** Zwei neue Arten von Mitgliedern: `rule`
  (Regel gehört zur Applikation, auch wenn sie nur über any, ein grosses Netz oder gar nicht
  trifft) und `norule` (Regel gehört trotz Treffer nicht dazu). Der Verweis ist stabil über
  Läufe hinweg: `<Quellsystem-ID>|<VDOM>|<externe ID>` (Policy-ID bzw. Kennung des Contracts)
  statt der Regel-ID, die bei jedem Lauf neu vergeben werden kann. Fällt die Regel weg,
  erscheint das Mitglied als „nicht mehr gelesen“ wie ein umbenannter Server.
- Eine zugeordnete Regel steht in der Doku mit der Art **„direkt zugeordnet“** (`assigned`,
  spezifisch – sie zählt in der Übersicht wie eine spezifische Regel) und dem Bezug „direkt
  zugeordnet“; eine ausgeschlossene fällt aus Doku, Kategorien, Verbindungen und Kennzahlen.
  Zuordnen ersetzt einen Ausschluss derselben Regel und umgekehrt. Pflege im Regel-Detail der
  Applikations-Doku (zuordnen/ausschliessen/aufheben) und in „Regeln & Contracts“ (Regel einer
  Applikation zuordnen); nur Operator, Audit `application.member_added`. Die Liste der
  zugeordneten und ausgeschlossenen Regeln steht über der Regel-Tabelle.
- **Abhängigkeiten zwischen Applikationen** werden abgeleitet, nicht erfasst: Eine erlaubende
  Regel mit spezifischem Treffer bei der Applikation, deren Gegenseite den Fussabdruck einer
  anderen Applikation trifft (ihre EPGs; ihre Server bzw. ein Netz im Subnetz ihrer EPGs),
  ergibt „nutzt“ (Applikation ist Quelle bzw. Consumer) bzw. „wird genutzt von“ (Ziel bzw.
  Provider). Trifft die Gegenseite auch die eigene Applikation, zählt die Regel nicht (interne
  Verbindung). Je Abhängigkeit die Regeln mit Bezug und Diensten; Kunden-Benutzer sehen nur
  Applikationen ihres Ausschnitts. Im Export (Markdown) als Abschnitt „Abhängigkeiten“.

**Ergänzung 0.69.0 (Anwender: irrelevante Firewall-Regeln in den Kategorien ausblenden, mit
Begründung und Audit):**
- In der „Doku nach Kategorien“ (1–10, Grundlage für Export und Druck) lässt sich eine Zeile
  ausblenden, wenn ihre Regel für die Applikation nicht relevant ist (z. B. Grundregel des
  Rechenzentrums über `any`). **Begründung ist Pflicht** (5–1000 Zeichen); gilt nur in dieser
  Kategorie oder in allen. Tabelle `application_rule_hide` (Migration `0056`): Applikation,
  Regel über den stabilen Verweis wie bei `rule`/`norule` (`<Quellsystem-ID>|<VDOM>|<externe
  ID>`), Kategorie (leer = alle), Begründung, Bezeichnung der Regel, wer und wann. Bei einem
  IPv4/IPv6-Paar je Regel ein Eintrag; die Zeile fällt weg, sobald eine ihrer Regeln
  ausgeblendet ist.
- Anders als der Ausschluss (`norule`) bleibt die Regel in der Doku (Regel-Liste, Treffer,
  Verbindungen Ende zu Ende, Kennzahlen); nur die Zeile der Kategorie fällt weg und steht
  unter „N ausgeblendet“ mit Begründung, wer, wann – im Export (Markdown) als Abschnitt
  „Ausgeblendete Regeln“. Wieder einblenden löscht den Eintrag.
- Nur Operator (`POST /applications/{id}/hidden` mit `rule_ids`, `category`, `reason`;
  `DELETE /applications/{id}/hidden/{hide_id}`), Audit `application.rule_hidden` bzw.
  `application.rule_shown` mit Regel-Verweis, Kategorie und Begründung. Kunden-Benutzer sehen
  die ausgeblendeten Zeilen ihrer Regeln, ändern aber nichts (Freigabeliste).

**Ergänzung 0.88.0 (Anwender: Abhängigkeiten wieder einblenden):**
- **Infrastruktur getrennt:** neues Stammdatum `application.infrastructure` (Migration `0057`,
  Formular „Infrastruktur (Monitoring, Backup, DNS …)“, Audit `application.updated`). Die
  Ableitung bleibt wie in 0.60.0; je Eintrag `infrastructure` der anderen Applikation. In der
  Doku stehen die fachlichen Abhängigkeiten oben, die zu Infrastruktur-Applikationen
  eingeklappt in einem eigenen Teil – damit beantwortet der Bereich die Frage des Anwenders
  (Monitoring ist keine fachliche Abhängigkeit), ohne Verbindungen zu verstecken.
- **Erst auf Klick:** der Bereich ist zugeklappt und lädt beim Aufklappen über
  `GET /applications/{id}/dependencies` (auch Kunden-Benutzer, nur Applikationen ihres
  Ausschnitts). Export-Abschnitt `dependencies` für Markdown, Word und Druck
  (`word_export.SECTIONS`). Das Kennzeichen steuert nur die Anzeige, keine Regel und keinen
  Befund; der CSV-Import der Stammdaten bleibt bei seinen Feldern.

**Ergänzung 0.73.0 (Anwender: mehr Übersicht in der Applikations-Doku):**
- **Abhängigkeiten vorerst entfernt:** Die Ableitung aus spezifischen Regeln zeigte z. B. einen
  Monitoring-Contract als Abhängigkeit – technisch eine Verbindung, fachlich keine Aussage über
  die Produktion. Bis eine bessere Bewertung nötig wird (z. B. Art der Gegenstelle, Dienste,
  von Hand bestätigt), keine Karte und kein Export; `GET /applications/{id}/doc` rechnet sie
  nur noch mit `dependencies=true` (spart die Zeit), die Ableitung `applications.dependencies`
  bleibt für einen späteren Ausbau.
- **Übersicht:** Bereiche der Doku zuklappbar (Zustand im Browser), Systeme kompakt mit Detail
  auf Klick. Export (Markdown, Word bzw. Word + draw.io) und Druck mit wählbaren Abschnitten
  (`word_export.SECTIONS`: `overview`, `systems`, `epgs`, `connections`, `plan`, `rules`,
  `findings`, `categories`, `hidden`; `GET /applications/{id}/export?sections=…`, der
  Steckbrief steht immer da). Reine Darstellung, keine Änderung an den Daten.

**Ergänzung 0.78.2 (Anwender: Kategorien Richtung L3Out und Kategorie 10):**
- **Die Firewall ist führend:** Ein Contract, dessen Gegenstelle nur externe EPGs (L3Out) sind,
  steht nicht separat in den Kategorien (1–6, 9, 10 nach der L3Out-Zone), solange er die
  Dienste der erlaubenden Firewall-Regeln derselben Kategorie und Richtung des Systems abdeckt
  (`appdoc.mark_categories`, Vergleich wie bei der Absicht je Dienst: `zone_services`).
  Gibt es dort keine Firewall-Regel, öffnet die Firewall nichts – der Contract steht ebenfalls
  nicht da. Öffnet er weniger (Dienste der Firewall fehlen, „nur Antworten“), steht er da mit
  „öffnet weniger als die Firewall-Regeln, es fehlt …“ und ist ein Befund
  `app_contract_narrower` (Warnung, je Contract und Applikation, mit Systemen, Kategorien,
  fehlenden Diensten und den Firewall-Regeln; neu mit den übrigen Befunden der Applikationen).
  Sperrende Contracts (Taboo) bleiben stehen. Ohne Firewall im Mandanten (nur ACI) bleibt alles
  wie bisher. Treffer, Regeln & Contracts und Verbindungen behalten den Contract.
- **Kategorie 10 „Unspezifische Zugriffe auf die Applikation“** zeigt nur Zugriffe auf die
  Applikation (eingehend), nicht ihre Zugriffe auf andere Systeme bzw. Adressen ohne
  Zonensignal (z. B. „any → Tool“, „Server → Monitoring“); als Treffer bleiben sie.
- Gleich in der Doku je System, im Export (Markdown, Word) und im Druck.

**Ergänzung 0.84.0 (Anwender: „heute steht alles in Server Name“ – VIPs, Load Balancer und der
Weg zum Server in die Spalten verteilen):**
- **Source und Destination in Verkehrsrichtung** statt „Server Name“ und „Target Systems“:
  eingehend Gegenstelle → System, ausgehend System → Gegenstelle; beide mit Name und IP, mehrere
  Einträge je auf einer Zeile. Die Seite der Applikation trägt die Zeile intern weiter
  (`_app`/`_peer`, `_app_col`), Treffer und Ausblenden bleiben gleich.
- **Kategorie 2** mit der Kette in eigenen Spalten: Externer DNS Name, VIP (Firewall), Load
  Balancer (nur Virtual Services mit einem Port der Regel), Destination = Server mit dem Port im
  Pool. „Externe IP Adressen“ entfällt (steht in der VIP).
- **NAT** als eigene Spalte in den übrigen Kategorien (SNAT der Regel); optionale Spalten ohne
  Wert fallen je Kategorie weg (Ansicht, Markdown, Word, Druck). Die Spezifikation
  `docs/usecases/uc1-applikations-doku-spec.md` beschreibt die Spalten.

## Kontext

ADR 0004 (Ergänzung E4) beschreibt die Applikation als System mit AP-/Projekt-Klammer:
Gruppierung über `AP_<projektnr>` (ACI) bzw. die 3-stellige Projektnummer im Namen (Firewall),
rund 90 % automatisch, der Rest von Hand. Bisher gab es dafür kein Objekt:

- `/applications` lieferte publizierte Dienste (FortiGate-VIPs) – keine Applikationen.
- Die Applikations-Doku war rein **systemzentriert** (ein Server bzw. eine EPG je Doku).

Mit den ACI-Contracts (Lage von Consumer/Provider, 0.4.x) wollte der Kunde Contracts und
Regeln „den Applikationen zuweisen“. Eine Applikation umfasst typischerweise ein Application
Profile mit seinen EPGs, dazu einzelne Server hinter der Firewall; gemeinsam genutzte Server
gehören zu mehreren Applikationen.

## Entscheidung

- **Umbenennung:** Die VIP-Sicht heisst jetzt **publizierte Dienste** (`/published-services`,
  Lese-Audit `read.published_services`). `/applications` gehört der neuen Applikation.
- **Modell** (Alembic `0024`):
  - `application`: Mandant, Name (je Mandant eindeutig), Kürzel (meist die Projektnummer),
    Verantwortliche, Kritikalität (`low|medium|high|critical`), Standort, Beschreibung.
    Stammdaten ohne Funktionsrelevanz.
  - `application_member`: Art + **Text-Verweis** statt Fremdschlüssel, plus Herkunft
    (`manual` | `suggestion`):
    - `ap` → `<ACI-Tenant>/<AP>` (umfasst alle EPGs des Application Profiles),
    - `epg` → DN der EPG,
    - `host` → Name des Host-Objekts.

    EPGs und Objekte werden je Lauf ersetzt; die Zuordnung überlebt das, weil der Verweis
    stabil ist. Was nicht mehr gelesen wird, bleibt als Mitglied stehen und wird als „nicht
    mehr gelesen“ angezeigt. Hinzufügen ist nur für aktuell gelesene Verweise möglich (422).
- **Vorschläge:** noch nicht zugeordnete APs und Server, gruppiert nach der Projektnummer aus
  `AP_<nr>` bzw. `H_<funktion+nr>-<nr>-<firma>.<domain>`; `000` (Standort/extern) zählt nicht.
  - Der Name kommt aus dem optionalen Wörterbuch `project` des Namensprofils (Projektnummer →
    Name), sonst „Projekt <nr>“. Ohne gepflegtes Wörterbuch funktioniert alles; das Wörterbuch
    ist reine Beschriftung.
  - Übernehmen legt eine Applikation mit Kürzel = Projektnummer an. Gibt es schon eine mit
    diesem Kürzel, ergänzt der Vorschlag deren Mitglieder.
  - Der Vorschlag wird beim Übernehmen serverseitig neu berechnet; der Client schickt nur die
    Projektnummer.
  - **Ergänzung 0.74.1 (Anwender: „alle AP_001 von allen Tenants, die haben ausser der Nummer
    nichts gemeinsam“):** gruppiert wird nach **ACI-Tenant und Nummer**. Kommt eine Nummer in
    mehreren Tenants vor, heisst der Vorschlag „<Name> · <Tenant>“; Server zur Nummer gehen zum
    einzigen Tenant mit dieser Nummer, sonst in einen eigenen Vorschlag ohne Tenant. Schlüssel
    `<Tenant>|<Nummer>` (`key`); die Nummer allein wird nur angenommen, wenn sie eindeutig ist.
    Eine bestehende Applikation wird ergänzt, wenn ihr Name passt bzw. ihre APs im selben
    Tenant liegen.
- **Ergänzung 0.75.0 – Stammdaten aus CSV** (Entscheid des Anwenders 2026-10-26, einzige
  Ausnahme vom Grundsatz „kein CSV-Import“): Name, externer/interner Name, Zweck, Kürzel,
  Verantwortlich, Kritikalität, Standort und Beschreibung aus einer CSV-Datei
  (`POST /applications/import`, ohne `apply` Vorschau, mit `apply=true` übernehmen; Vorlage
  `GET /applications/import/template`; `store/app_import.py`). Kopfzeile deutsch oder englisch,
  Werte auch deutsch (Produktion, hoch); gleicher Name (ohne Gross-/Kleinschreibung) ändert die
  bestehende Applikation nur in ausgefüllten Zellen; fehlerhafte Zeilen werden ausgelassen.
  Mitglieder bleiben in der Oberfläche – die Stammdaten haben keine Funktionsrelevanz. Dazu
  `vm_count` je Applikation in der Liste und `POST /applications/delete` (mehrere löschen,
  Audit je Applikation).
- **Doku je Applikation:** die Tabellen 1–10 aller Systeme der Applikation (Server →
  Firewall-Tabellen, EPGs → ACI-Tabellen), gleiche Zeilen einmal. Regeln, Objekte und NAT
  werden je Mandant einmal geladen. Export als Markdown sowie als Druckansicht (PDF über den
  Browser). Die systemzentrierte Doku bleibt als „Doku je System“.
- **Rechte:** Lesen `viewer`, Schreiben `operator`, mandantsgescoped. Auditiert werden
  `application.created|updated|deleted|member_added|member_removed`; im Audit stehen nur
  Stammdaten-Felder sowie Art und Verweis.
- **Löschen:** Ein Mandant nimmt seine Applikationen mit. Wird ein Standort gelöscht, verliert
  die Applikation ihren Standort.
- **EPG-Lage physisch:** Die Lage einer EPG im Regel-Detail zeigt zusätzlich die Leaf-Ports
  (statische Pfade, LLDP/CDP-Nachbar) und VLANs aus dem Netzinventar des APICs.

## Konsequenzen

- Contracts und Regeln lassen sich über ihre Systeme Applikationen zuordnen, ohne Daten neu zu
  erfassen. Ein Wechsel der Quelle (anderer APIC, neue Firewall) behält die Zuordnung, solange
  Namen bzw. DNs gleich bleiben.
- Benennt jemand ein AP, eine EPG oder einen Server um, verliert das Mitglied den Bezug und
  erscheint als „nicht mehr gelesen“. Der Vorschlag bietet das umbenannte Objekt wieder an.
- Regeln direkt zuordnen bzw. ausschliessen und Abhängigkeiten zwischen Applikationen seit
  0.60.0 (Ergänzung oben; Abhängigkeiten seit 0.73.0 vorerst nicht angezeigt, seit 0.88.0
  wieder mit Infrastruktur getrennt). Export nach NetBox seit 0.93.0 als Abschnitt
  *Applikationen* des NetBox-Syncs (Freigabe 2026-10-27, **ADR 0031**): je Applikation ein Tag
  `automato-app-…` an den VMs und IP-Adressen ihrer Systeme, ohne Custom Fields.
