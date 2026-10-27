# ADR 0028 — Sprache der Oberfläche und der Doku: Deutsch und Englisch

## Status

Angenommen (2026-10-27), Schritt 1 umgesetzt in 0.86.0, Schritte 2 und 3 in 0.87.0, Schritt 4 in
0.88.0. Frage des
Anwenders vom 2026-10-27 („alle Texte umschalten von Deutsch auf English und zurück – ist dies ein grosser Aufwand?“),
Entscheid: ja, zuerst die Oberfläche, die Doku soll auch in beiden Sprachen möglich sein; alle
Benutzer können umschalten; Fachbegriffe bleiben englisch („das würde verwirren“).

## Kontext

Automato ist durchgehend deutsch: rund 3 000 Texte in der Oberfläche (`web-ui/frontend/src`,
29 000 Zeilen) und schätzungsweise 600–1 000 Texte, die der Orchestrator liefert (Befundtexte
und Hinweise `store/finding_text.py`, Gründe und Hinweise im Weg, Urteile, Meldungen von Läufen,
Spaltenköpfe und Titel der Applikations-Doku, Exporte Word/PDF/draw.io, Fehlermeldungen der
API). Ein Teil der Logik hängt heute an deutschen Texten (z. B. `"(Rückrichtung)"` in
`app_paths`, `"implizit gesperrt"` im Plan, `via.startswith("vzAny")` in `appdoc`).

Vorgaben: Open Source bevorzugt, keine neuen Abhängigkeiten ohne Not; die UI-Tests prüfen
deutsche Texte; Kunden-Benutzer sehen dieselbe Oberfläche (ADR 0018).

## Entscheidung

1. **Text neben Text statt Schlüsseln:** in der Oberfläche `L("deutsch", "english")`
   (`web-ui/frontend/src/i18n.ts`), für JSX `L(<>…</>, <>…</>)`. Kein Übersetzungs-Framework,
   keine Schlüsseldateien: der deutsche Text bleibt lesbar im Code, beide Fassungen stehen
   nebeneinander und werden zusammen geändert. Der deutsche Text bleibt dabei Zeichen für
   Zeichen gleich – die bestehenden UI-Tests gelten unverändert für Deutsch.
2. **Sprache je Browser**, gewählt im Benutzermenü (`localStorage automato_lang`), Standard
   Deutsch, für alle Rollen inkl. Kunden-Benutzer. Die Sprache steht beim Laden fest; Umschalten
   lädt die Seite neu – Konstanten auf Modulebene (Navigation, Beschriftungen) brauchen so keine
   Hooks. `<html lang>` und das Gebietsschema für Datum/Zeit (`de-CH` bzw. `en-GB`) folgen.
3. **Texte vom Server** folgen der Kopfzeile `Accept-Language`, die die Oberfläche seit 0.86.0
   bei jedem Aufruf mitschickt. In der Oberfläche werden Werte vom Server nie übersetzt und nie
   mit übersetzten Texten verglichen (Schlüssel bleiben Schlüssel, z. B. Bereiche des
   Betriebszustands `AREA_LABEL`).
4. **Begriffe** einheitlich nach `docs/GLOSSAR.md`. Fachbegriffe bleiben in beiden Sprachen
   englisch (Policy, Contract, EPG, L3Out, VDOM, VIP, Load Balancer, Source/Destination …).
5. **Exporte** (Word, PDF, draw.io, Markdown) erhalten eine eigene Sprachwahl im Export-Dialog,
   Vorgabe = Sprache der Oberfläche – so entsteht eine englische Doku auch aus einer deutschen
   Oberfläche.

## Schritte

| Schritt | Inhalt | Stand |
|---|---|---|
| 1 | Oberfläche: alle Ansichten, Dialoge, Hinweise, Markdown-Export und Druck aus der Oberfläche; Sprachwahl; Glossar; UI-Tests auf Englisch | **0.86.0** |
| 2 | Server: Befundtexte/Hinweise, Gründe und Urteile im Weg, Spaltenköpfe und Titel der Applikations-Doku, Fehlermeldungen; Logik, die an Texten hängt, auf Schlüssel umstellen | **0.87.0** |
| 3 | Exporte Word/PDF/draw.io/SVG in beiden Sprachen mit Sprachwahl im Export-Dialog | **0.87.0** |
| 4 | Meldungen der Läufe (beim Abruf übersetzt), Konsole von Collector/Agent (`AUTOMATO_LANG`) und CLI (`automato --lang …`); Betriebsdoku auf Wunsch | **0.88.0** (Betriebsdoku offen) |

## Folgen

- Jede neue Oberflächen-Zeichenkette entsteht als `L("…", "…")`; die UI-Tests prüfen weiter
  Deutsch, `e2e/i18n.spec.ts` lädt jede Ansicht auf Englisch (Titel = Eintrag der Navigation).
- Seit 0.87.0 kommen auch die Texte vom Server in der Sprache der Anfrage; deutsch bleiben nur
  gespeicherte Teile (z. B. Gründe in einem Befund-Detail) – `docs/BETRIEB.md`. Seit 0.88.0
  werden auch die gespeicherten Meldungen der Läufe beim Abruf übersetzt (Schritt 4).
- Freie Vorbelegungen (z. B. Beschreibung einer Zone aus dem Grundschema) werden in der
  gewählten Sprache gespeichert; sie bleiben frei änderbarer Text.

## Umsetzung Schritt 2/3 (0.87.0)

- `orchestrator/app/i18n.py`: `L(de, en)` zur Laufzeit (nie auf Modulebene – feste Tabellen als
  Paare), `lang()`, `use()`, `term()`; ASGI-Middleware setzt die Sprache je Anfrage als
  ContextVar (gilt auch im Threadpool der sync-Endpunkte; neue Threads beginnen deutsch, die
  Verbindungsprüfung gibt sie ausdrücklich weiter).
- **Gespeichertes bleibt deutsch:** `@german_only` an den Einstiegen, die Texte speichern
  (`reconcile.reconcile_tenant`, `refresh_zone_findings`, `refresh_app_findings`,
  `customers.derive`/`derive_findings`, `ingest.persist_result`, `rule_index.rebuild`,
  `plan_versions.snapshot`, `netbox_sync.compute_job`/`apply_job`, Grundschema-Befunde). Gespeicherte
  deutsche Gründe/Hinweise werden bei der Anzeige übersetzt (`customers.show_reason`,
  `netbox_sync.show_*`, `SchemaZone.out()` mit `raw=True` zum Speichern).
- **Werte bleiben Schlüssel:** Spalten der Kategorien (`columns`, Anzeige `labels`), Zellwerte
  („Allow“), `area` des Betriebszustands, `direkt`, Urteile/Arten. Logik an Texten auf Merkmale
  umgestellt (`implicit`, `leak.kind`, `failure`, Marken der Rückrichtung in beiden Fassungen).
- **Exporte:** die Oberfläche hängt `?lang=` der gewählten Sprache der Datei an (geht vor
  `Accept-Language`); Rundlauf Zonenplan draw.io → Import in allen Kombinationen geprüft.

## Umsetzung Schritt 4 (0.88.0)

- **Meldungen der Läufe beim Abruf übersetzt:** gespeichert bleibt deutsch – die Meldung eines
  Laufs (`collector_run.message`, vom Orchestrator: Abbruch von Hand, Ablauf ohne Ergebnis bzw.
  nicht abgeholt, „Einspielen fehlgeschlagen“, Teil-Upload unvollständig, Zugangsdaten nicht
  lesbar; vom Collector/Agenten: Zusammenfassungen, Fehler, Teilfehler je Switch, Hinweise je
  Plattform) und der Grund der Update-Prüfung eines Agenten (`agent.update_note`, ASCII).
  `store/runs.show_message` übersetzt beim Abruf mit der Tabelle `store/run_text.PHRASES`:
  bekannte *Teile* (deutsche Form mit Platzhaltern → englische), an jeder Stelle der Meldung,
  genauere vor allgemeinen – zusammengesetzte Meldungen („ · “, „; “, „. “) werden so Teil für
  Teil übersetzt, Unbekanntes (Texte der Geräte, Ausnahmen, Namen) bleibt; Nicht-ASCII passt auch
  als „?“ (ASCII-Grund). Angewendet in der Liste der Läufe und „Letzte Läufe“ (`GET /jobs`,
  `/jobs/{id}`, Abbrechen), im Betriebszustand (letzter Lauf, Agent aktualisiert nicht), in der
  Agentenliste (`update_note`), in `automato status` und in der Quittung an den Agenten. Die
  Werte, die die Oberfläche vergleicht (`status`, `platform`, `trigger`), bleiben unverändert.
- **Gründe in „Alle einlesen“** (`runs.RunRejected`, nur Antwort/Log) jetzt zweisprachig mit
  `L()` (vorher nur englisch), z. B. „netbox-Quelle „nb“ hat kein Lese-Token“.
- **Konsole des Agenten** (`collectors/common/i18n.py`, Muster wie `app/i18n.py`: `L`, `use`,
  `german`, `german_only`, dazu `carry` für Threads, `both` für deutsch + Konsole, `Failure` =
  Fehler, dessen Text erst bei der Ausgabe entsteht): Sprache aus `AUTOMATO_LANG` (agent.env bzw.
  Umgebung), Standard Deutsch. Zweisprachig sind Hilfe, `--check` (inkl. Ursache und Hinweis bei
  abgelehnter Anmeldung, `diagnose.device_error`/`http.describe_error`), `--poll`/`--watch` und
  das Selbst-Update.
- **Was an den Orchestrator geht, bleibt deutsch:** die `collect_once` je Plattform laufen
  `@german_only` (Threads der Switches mit `carry`); der Grund der Update-Prüfung entsteht deutsch
  für `X-Agent-Update` und in der Sprache der Konsole für die Ausgabe (`both`, `Failure`). Der
  Agent schickt bei nicht deutscher Konsole `Accept-Language`; der Orchestrator speichert trotzdem
  deutsch (Ergebnis und Teil-Uploads unter `german()`) und gibt die Meldung in der Quittung
  zusätzlich übersetzt zurück (`ResultAck.shown`, nur wenn übersetzt). Die Konsole zeigt `shown`,
  sonst die deutsche Meldung (älterer Orchestrator). Teile, die der Collector auch bei `--check`
  zeigt (Fehler, Ursache, Hinweis, fehlende Zugangsdaten), stehen dort zweisprachig (`L()`) und
  gleichlautend in `run_text.PHRASES` – `tests/test_i18n_runs.py` erzeugt beide Fassungen mit dem
  Collector und vergleicht sie mit der Übersetzung des Orchestrators; reine Laufmeldungen
  (Zusammenfassungen, Routing nicht lesbar) erzeugt der Collector nur deutsch.
- **CLI** `automato …`: `--lang de|en` vor oder nach dem Befehl bzw. `AUTOMATO_LANG`, Standard
  Deutsch; Hilfe, Meldungen, Schritte von `tls-init`/`db-roles` und `purge` in der Sprache. Was
  speichert (`ensure-tenant`, `ensure-source`, `collect`/`lab`, `purge` mit Audit), läuft
  `@german_only` bzw. unter `german()`; `status` zeigt die gespeicherte Meldung übersetzt.
- **Oberfläche:** Collector-Agenten → Einrichtung zeigt die Meldungen des Agenten in der
  Fehlertabelle deutsch und englisch (die Konsole folgt `AUTOMATO_LANG`, nicht der Oberfläche);
  die erzeugte `agent.env` enthält `AUTOMATO_LANG=en`, wenn die Oberfläche englisch ist.
- **Neue Meldungen** im Collector bzw. Orchestrator, die gespeichert werden: deutsch erzeugen
  und die Teile in `run_text.PHRASES` ergänzen (sonst erscheinen sie in beiden Sprachen deutsch).
