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
- Noch offen: Regeln direkt einer Applikation zuordnen (heute nur über ihre Systeme),
  Applikations-Abhängigkeiten (Applikation ↔ Applikation) und ein Export nach NetBox
  (Tag/Custom Field, nur mit eigener Freigabe gemäss ADR 0010).
