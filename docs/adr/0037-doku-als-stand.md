# ADR 0037 — Doku je Applikation als Stand im Hintergrund

## Status

**Entwurf** (2026-10-28), umgesetzt in 0.108.0 – zur Abnahme durch den Anwender.

Rückmeldung des Anwenders vom 2026-10-28 (Planung 0.108 in der ROADMAP, „108 go“): „das Speichern
dauert zu lange“; Doku im Hintergrund rechnen.

Ergänzt ADR 0012 (Applikationen), ADR 0036 (Module: die Doku gehört zum Modul Doku, die
Verbindungen zum Wegsucher) und 0.74.1 (Befunde nach dem Speichern im Hintergrund). Migration
`0067` (`doc_snapshot`).

## Kontext

- Die Doku einer Applikation (`applications.build_doc`) und die Verbindungen Ende zu Ende mit dem
  Netzwerkplan (`app_paths.build`) wurden bei jedem Aufruf neu gerechnet – im grossen Testbestand
  (340 000 IPAM-Einträge, viele Regeln) 9,5 s bzw. 4,7 s je Aufruf, nach jedem Speichern erneut.
- Speichern einer Zone rechnete die Befunde der Zonen in der Anfrage – im grossen Testbestand über
  13 Minuten: je geprüfter Adresse (bis 2000) wurden alle VMs, Portgruppen und Endpunkte des
  Mandanten neu geladen.
- Die Doku ändert sich nur, wenn sich ihre Eingaben ändern: ein Lauf eines Quellsystems (Geräte,
  IPAM), die Konfiguration in Automato (Zonen, Kategorien, Namensprofil, Verbund, L3Outs, Mandant)
  oder die Applikation selbst (Stammdaten, Mitglieder, zugeordnete bzw. ausgeblendete Regeln).

## Entscheid

1. **Stand je Applikation** (Tabelle `doc_snapshot`): je Applikation, Art (`doc` = Doku,
   `connections` = Verbindungen mit Plan) und Variante (Sprache, Namen der Gegenstellen, Ausschnitt
   eines Kunden-Benutzers als Abdruck seiner Regeln) das Ergebnis als JSON (gepackt), wann und wie
   lange gerechnet, zuletzt angesehen. Abgeleitete Daten – jederzeit neu berechenbar, nie Quelle;
   mit der Applikation gelöscht.
2. **Fingerabdruck der Eingaben** (`store/doc_snapshots.fingerprint`): Version der Plattform, die
   Applikation (geändert, ihr Audit), der letzte abgeschlossene Lauf eines Quellsystems des
   Mandanten, die Quellsysteme (an/aus, Stand der Referenzquelle) und die letzte Änderung an der
   Konfiguration (Audit ohne Lesen, Läufe, Agenten, Befunde, NetBox-Sync, Versionen des Zonenplans,
   Vorschläge und Benutzer der Kunden). Gleicher Abdruck = gleicher Stand. Der Abdruck wird vor dem
   Rechnen genommen: was während des Rechnens dazukommt, gilt als neu.
3. **Sofort zeigen:** `GET /applications/{id}/doc` bzw. `…/connections` liefert den passenden
   Stand sofort. Passt er nicht mehr, kommt der letzte mit `snapshot: {fresh: false, building:
   true}` und er wird hinter der Antwort neu gerechnet – gewartet wird nur, wenn die letzte
   Berechnung kürzer war als `AUTOMATO_DOC_STALE_SECONDS` (1,5 s) oder es noch keinen Stand gibt.
   `GET …/doc/state?kind=` sagt, wann er fertig ist; `?fresh=true` rechnet neu („↻ neu berechnen“).
   Exporte (Word, Markdown, Plan) warten immer auf den aktuellen Stand.
4. **Befunde immer aktuell:** die Befunde in der Doku kommen bei jedem Aufruf aus der Datenbank
   (Eingaben im Stand), Triage braucht keine neue Berechnung.
5. **Vorrechnen** (`warm`): nach einem Lauf, nach dem Nachziehen einer geänderten Applikation, nach
   Zonen, Netzwerkdesign, Grundschema und Verbund rechnet der Hintergrund die in den letzten
   `AUTOMATO_DOC_WARM_DAYS` (7) Tagen angesehenen Stände neu, höchstens
   `AUTOMATO_DOC_WARM_SECONDS` (600) je Durchgang; nicht bei ausgeschaltetem Modul Doku.
6. **Speichern ohne Warten:** die Befunde der Zonen (Zone anlegen/ändern/löschen, Absichten,
   Vorschläge, Zonenplan-Import, Netzwerkdesign, Grundschema) und die Befunde nach einem geänderten
   Verbund paralleler Firewalls laufen wie die der Applikationen (0.74.1) hinter der Antwort
   (`app_refresh.schedule_zones`, `schedule_regroup`). Die Zonen-Prüfung lädt die EPG-Zuordnung
   einmal je Durchgang (`relevance.Relevance.classifier`): Speichern einer Zone im grossen
   Testbestand 13 min → 3 s.
7. **Im Prozess:** je Stand rechnet höchstens ein Durchgang zugleich (wie `app_refresh`); der
   Orchestrator läuft als ein Prozess. Speichern des Stands ist „best effort“ – scheitert es (zwei
   gleichzeitig, SQLite gesperrt), bleibt der alte und wird beim nächsten Mal ersetzt.
8. **Nur lesend, kein neuer Schreibweg.** `AUTOMATO_DOC_SNAPSHOTS=false` schaltet alles ab (wie bis
   0.107.0).

## Konsequenzen

- Gemessen im grossen Testbestand: Doku 9,5 s → 0,2 s, Verbindungen 4,7 s → 0,04 s; nach dem
  Speichern einer Applikation ist der Stand schon vorgerechnet (0,3 s).
- Nach einer Änderung kann die Seite kurz den alten Stand zeigen („Stand von …, wird im
  Hintergrund aktualisiert…“); sie lädt von selbst neu, sobald er fertig ist.
- Neue Eingaben der Doku (z. B. eine neue Einstellung) müssen über das Audit, einen Lauf bzw. die
  Applikation laufen – sonst bemerkt der Abdruck sie nicht. `AUTOMATO_TEST_DOC_SNAPSHOTS=1` lässt
  die Orchestrator-Tests mit dem Stand laufen und zeigt solche Lücken.
- Die Tabelle wächst je Applikation und Variante (Sprache, Kunde); Stände sind klein (gepackt).
