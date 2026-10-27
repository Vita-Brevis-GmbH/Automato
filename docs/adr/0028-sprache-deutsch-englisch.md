# ADR 0028 — Sprache der Oberfläche und der Doku: Deutsch und Englisch

## Status

Angenommen (2026-10-27), Schritt 1 umgesetzt in 0.86.0, Schritte 2 und 3 in 0.87.0. Frage des
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
| 4 | Meldungen von Collector/Agent und CLI (`automato …`), Betriebsdoku auf Wunsch | offen |

## Folgen

- Jede neue Oberflächen-Zeichenkette entsteht als `L("…", "…")`; die UI-Tests prüfen weiter
  Deutsch, `e2e/i18n.spec.ts` lädt jede Ansicht auf Englisch (Titel = Eintrag der Navigation).
- Seit 0.87.0 kommen auch die Texte vom Server in der Sprache der Anfrage; deutsch bleiben nur
  gespeicherte Teile (z. B. Gründe in einem Befund-Detail, Laufmeldungen) – `docs/BETRIEB.md`.
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
