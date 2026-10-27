# ADR 0029 — Datenqualität: wo Ergebnisse unsicher sind, mit Ursache und Link (erste Fassung)

## Status

Entwurf (2026-10-27), erste Fassung umgesetzt in 0.91.0. Roadmap Schritt 6 „Daten verschärfen“ (Umplanung
des Anwenders vom 2026-10-26); der Inhalt dieser ersten Fassung ist **von uns vorgeschlagen** und
wird mit dem Anwender abgestimmt (offene Punkte unten).

## Kontext

Doku, Weg-Suche, Zonen und Kunden sind nur so gut wie die gelesenen Daten. Bisher zeigt Automato
Unsicherheit verstreut: „Relevanz unbekannt“ je Regel in der Doku bzw. der Regel-Liste,
„nicht lesbar“ an Routing-Kontexten, Teilfehler nur im Text der Laufmeldung (z. B. Switches einer
Gruppe nicht erreicht), „veraltet“ nur im Betriebszustand mit fester Grenze von 7 Tagen, dazu
einzelne Befunde (nicht im IPAM, VM-IP nicht im IPAM, EPG gelernt ≠ konfiguriert …). Ein
Anwender sieht nicht auf einen Blick, **wo** Ergebnisse unsicher sind, **warum** und **wo** er
nachbessern kann.

Vorgaben: nur lesend (kein neuer Schreibweg), keine Wegberechnung je Regel (grosse Regelwerke,
ADR 0016), zweisprachig (ADR 0028), Kunden-Benutzer nur über die Freigabeliste (ADR 0018).

## Entscheidung

1. **Auswertung je Mandant** (`store/data_quality.report`, `GET /data-quality?tenant_id=&customer_id=`,
   viewer): fünf **Bereiche** mit Ampel `ok`/`warn`/`crit`, Kennzahl und Einträgen. Jeder Eintrag
   hat Status, Titel, Anzahl, Ursache (was fehlt bzw. sich widerspricht), Hinweis (was tun),
   Beispiele und einen **Link** in die Ansicht zum Nachbessern (`{view, params}`, z. B. Läufe
   eines Quellsystems, Routing-Kontext, Regel, Findings eines Typs). Ampel eines Bereichs = der
   schlechteste Eintrag (`info` zählt wie `ok`); Anteil-Einträge werden ab 50 % rot.
2. **Gesamtnote** = Anteil der Bereiche ohne Warnung an den Bereichen mit Daten (z. B. ohne VMs
   zählt der Bereich VMs nicht). Kachel in der Übersicht, Reiter „Datenqualität“ in der Ansicht
   Abgleich (`#abgleich?tab=datenqualitaet`).
3. **Nur Zähler und Abfragen**: bestehende Befunde werden gezählt, nicht neu gerechnet. Einzige
   Ausnahme sind die Ursachen „Relevanz unbekannt“, die vom Routing abhängen: eine
   **Stichprobe** von höchstens 300 Firewall-Regeln (gleichmässig über alle verteilt), je Seite
   die erste Adresse der Regel gegen das Routing ihrer Firewall (`relevance.Relevance.check` –
   nur Routen nachschlagen, keine Wege). Das Ergebnis nennt die Grösse (`sample`).
   `relevance.check` liefert dafür neben dem Text die **Ursache als Schlüssel** (`cause`:
   `no_ip`, `no_routing`, `no_route`, `route_interface`, `policy_route`, `interface_unknown`,
   `other_interface`).
4. **Neue Befunde** (ohne Migration, `native_enum=False`), erzeugt im Abgleich
   (`reconcile._source_issues`, `@german_only`), Gegenstand `SubjectKind.SOURCE` (Name des
   Quellsystems), Gruppe „Datenqualität“ in den Findings:
   - `source_stale`: letzter erfolgreicher Lauf älter als die Grenze – `AUTOMATO_STALE_DAYS`
     (Standard 7, `0` = nie), mit Zeitplan mindestens der Abstand zweier Termine plus ein Tag
     (wöchentlich = 8 Tage, monatlich ≈ 32 Tage). Nie eingelesen zählt erst, wenn das
     Quellsystem länger als die Grenze besteht. Referenzquellen: Stand = übernommener
     Referenzlauf. Dieselbe Grenze gilt im Betriebszustand.
   - `source_partial`: der Lauf des aktuellen Stands nur teilweise – Teilfehler aus der
     Laufmeldung (`nicht gelesen:` = Geräte einer Gruppe nicht erreicht, `nicht lesbar` = VDOM,
     Routing bzw. Objekte ohne Leserecht, `Hinweise:` = Befehle ohne auswertbare Ausgabe, dann nur
     Hinweis) und Routing-Kontexte mit „nicht lesbar“ (`topo_context.missing`).
   Gelingt kein Lauf mehr, läuft kein Abgleich: der **Zeitplan** rechnet diese Befunde stündlich
   nach (`data_quality.maybe_refresh`; ein Mandant, der gerade eingespielt wird, kommt beim
   nächsten Mal dran – `tenant_lock.try_hold`).
5. **Kunde oben gewählt**: Regeln, VMs, Netze und Befunde nur aus seinem Ausschnitt
   (`customer_scope.View`); Quellsysteme und IPAM gelten für alle Kunden (als „geteilt“
   markiert). **Kunden-Benutzer** erhalten die Auswertung nicht (nicht in der Freigabeliste,
   403) – sie zeigt Quellsysteme, Läufe und die ganze Umgebung.

## Bereiche (erste Fassung)

| Bereich | Einträge | Link |
|---|---|---|
| Quellsysteme | je Quellsystem: nie eingelesen (rot), Stand veraltet (gelb, ab vierfacher Grenze rot), letzter Lauf fehlgeschlagen (gelb, es gilt der ältere Stand), nur teilweise gelesen (Teilfehler, Routing-Kontexte „nicht lesbar“); ausgeschaltete mit Stand als Hinweis | Läufe des Quellsystems bzw. Routing-Kontext |
| Regeln | Firewall-Regeln mit Interfaces, deren Firewall/VDOM kein gelesenes Routing hat; Interface bzw. Zone der Regel nicht im Inventar; Stichprobe der Relevanz (Ursachen gruppiert) und eigene Adresse nicht über das Interface der Regel geroutet (Hinweis); Regeln mit Objekten ohne Wert; externe EPGs in Contracts ohne Subnetz, Subnetze ohne gelesenen Scope (Hinweis), gleiches Subnetz in mehreren L3Outs einer VRF ohne gelesenes Routing; Befunde nicht im IPAM, Regel nicht wirksam, SNAT-Pool fehlt | Regel, Regeln mit Objekt, Findings, Routing |
| VMs | eingeschaltete VMs ohne IP, ausgeschaltete ohne IP (Hinweis), ohne Portgruppe, an Portgruppen ohne EPG (Hinweis, nur mit ACI); Befunde VM-IP nicht im IPAM, EPG gelernt ≠ konfiguriert, Portgruppe ohne EPG bzw. Name ≠ EPG | Objekte → VMs/Portgruppen, Findings |
| IPAM | keine führende Referenzquelle (rot), führendes IPAM nicht eingelesen (rot) bzw. veraltet; Befunde Subnetz fehlt im IPAM, IP mehrdeutig, VLAN weicht ab | Quellsysteme, Läufe, Findings |
| Zonen & Kunden | gelesene Netze (Inventar, EPG-Subnetze) ohne logische Zone; EPGs, Regeln und VMs ohne Kunde (`customers.unassigned`); Befund Netz in mehreren Zonen; ohne Zonen bzw. Kunden nur Hinweis | Netzwerkdesign, Kunden, Findings |

## Grenzen

- Die Stichprobe prüft je Seite nur die **erste Adresse** der Regel – ein Netz, das teils anders
  geroutet wird, kann „möglich“ erscheinen. Für die Doku eines Systems gilt weiter die Prüfung je
  IP (`relevance`).
- Teilfehler werden aus dem **Text der Laufmeldung** erkannt (deutsche Merkmale der Collectors).
  Ändert ein Collector seine Meldung, muss `data_quality.PARTIAL` folgen; strukturierte
  Teilfehler im Ergebnis des Collectors wären robuster (offen).
- „Netze ohne Zone“ zählt die gelesenen Netze der Geräte, nicht die Netze des IPAMs und nicht den
  Zonenplan (der zusätzlich Knoten ohne Zone kennt).
- Die Auswertung rechnet bei jedem Abruf neu (kein Zwischenspeicher); grosse Mandanten brauchen
  vor allem für die Stichprobe (Routen, IPAM-Namen) etwas Zeit.

## Offene Punkte für die Abstimmung mit dem Anwender

1. **Schwellen**: Grenze „veraltet“ je Plattform (z. B. IPAM täglich, Switches wöchentlich) statt
   einer für alle; Anteil für Rot (heute 50 %); welche Einträge die Gesamtnote tragen.
2. **Teilfehler strukturiert** im Ergebnis der Collectors (Gerät, Befehl/Objekt, Grund) statt aus
   der Meldung; dazu fehlende Leserechte je Objektklasse sichtbar machen.
3. **Relevanz vollständig statt Stichprobe**: Ergebnis je Regel im Regelindex ablegen (beim
   Einlesen berechnet) – dann auch Filter „Relevanz unbekannt“ in der Regel-Liste.
4. **Widersprüche zwischen Quellen**: IP im IPAM anders als auf der Firewall bzw. im vCenter,
   VLAN der Portgruppe ≠ VLAN der EPG, gleiche IP an zwei VMs, Interface-Netz ≠ IPAM-Prefix.
5. **Verlauf der Gesamtnote** (je Tag gespeichert) und Benachrichtigung bei Verschlechterung.
6. **Kunden-Benutzer**: eingeschränkte Fassung nur mit ihren Regeln/VMs/Netzen ohne Quellsysteme?
7. **Datenqualität in den Exporten** (Applikations-Doku, Zonenplan): Hinweis, wenn Teile auf
   unsicheren Daten beruhen.
8. **Befunde für Bereiche** (heute nur Quellsysteme): z. B. „Firewall ohne Routing“ oder
   „Interface nicht im Inventar“ als eigener Befund mit Triage?

## Folgen

- Neue Einstellung `AUTOMATO_STALE_DAYS` (Standard 7); der Betriebszustand nutzt sie ebenfalls.
- Die Findings-Übersicht hat eine Gruppe mehr („Datenqualität“), sobald ein Quellsystem veraltet
  oder nur teilweise gelesen ist (UI-Test „Findings: Übersicht“ zählt 10 Gruppen).
- Keine Migration, kein neuer Schreibweg, keine neue Abhängigkeit.
