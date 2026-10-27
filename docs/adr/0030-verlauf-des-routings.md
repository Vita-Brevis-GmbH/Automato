# ADR 0030 — Verlauf des Routings: Änderungen von Lauf zu Lauf festhalten

## Status

**Entwurf** (2026-10-27) – umgesetzt, zur Abnahme durch den Anwender. Entscheid des Anwenders
2026-10-27: den Verlauf jetzt umsetzen (bisher „Momentaufnahme“). Ergänzt ADR 0019 (Topologie und
Pfade, Punkt 4 „Routen … der Verlauf ist begrenzt“, Stufe 2 „Ein Verlauf der Routen folgt bei
Bedarf“) und ADR 0022 (Aufbewahrung).

## Kontext

- Routing, BGP, HA, Policy-Routen und L3Outs (`topo_*`, ADR 0019 Stufe 2) werden je Lauf eines
  Quellsystems **ersetzt**. Was vorher galt, ist danach weg: „Seit wann geht die Default-Route
  nur noch über einen Provider?“, „Wann fiel der BGP-Nachbar aus?“, „Welche Route kam mit dem
  letzten Lauf dazu?“ lässt sich nicht beantworten.
- Die Grenze steht in ADR 0019 („Dynamische Routen sind eine Momentaufnahme des Laufs“).
- Ein voller Stand je Lauf wäre gross (ACI: bis 100 000 Routen je Leaf und VRF) und meist gleich.
  Gebraucht werden die **Änderungen**, nicht Kopien.
- Teile des Routings sind je Lauf manchmal **nicht lesbar** (`topo_context.missing`, z. B. HTTP
  403 auf die BGP-Pfade, Tabelle zu gross) bzw. ein VDOM fehlt ganz (ohne Leserecht). Ein
  einfacher Vergleich meldete dann Tausende Routen als „entfernt“ und beim nächsten Lauf als
  „hinzugefügt“.
- Bei FortiGate-HA heisst der Knoten nach dem Hostnamen des aktiven Mitglieds; ein HA-Wechsel
  benennt alle Kontexte um.

## Entscheidung

1. **Vergleich beim Einspielen:** Bevor `topology.replace` den Stand eines Quellsystems ersetzt,
   vergleicht `store/topo_changes.record` den alten (Datenbank) mit dem neuen Stand (die Zeilen,
   die gleich eingefügt werden) und speichert die Änderungen in **`topo_change`** (Alembic
   `0057`): Mandant, Quellsystem, Lauf (Kennung ohne Fremdschlüssel – Läufe haben eine kürzere
   Frist), Zeit, Art, Knoten, Kontext (VDOM bzw. VRF), Tabelle, Schlüssel, Präfix mit Bereich
   (`family`/`lo`/`hi`, für den Kunden-Ausschnitt), Änderung, vorher/nachher als JSON.
2. **Arten und Schlüssel:**
   - `route`: Tabelle (`rib`/`bgp`/`static`), VRF im Kontext (ausser 0), Präfix; beim BGP-Pfad
     dazu der Nachbar (`learned_from`, sonst der Next-Hop), bei der statischen Route die Sequenz
     (sonst Gateway bzw. Interface) – z. B. `10.0.0.0/24`, `10.0.0.0/24 from=192.0.2.1`,
     `0.0.0.0/0 seq=5`, `10.1.0.0/16 vrf=3`.
   - `bgp_peer`: IP des Nachbarn; `policy_route`: Art (`policy`/`sdwan`) und Sequenz; `l3out`: DN
     (Kontext = seine VRF); `ha`: einmal je Quellsystem; `context`: der Kontext selbst.
   - Gleicher Schlüssel mehrfach (z. B. zwei RIB-Einträge desselben Präfixes) = eine Liste,
     Reihenfolge egal.
3. **Verglichen werden nur Felder einer Positivliste**, in fester Form:
   - Route: Next-Hops (IP, Interface; sortiert), Protokoll, Distanz, Metrik, aktiv/Backup (BGP:
     bester Pfad, statisch: aktiv), dazu je Tabelle AS-Pfad, Local Preference, Origin, Weight,
     Priorität, Owner/Routen-Typ (ACI), Blackhole, SD-WAN-Zone.
   - Nachbar: Zustand, Remote AS, lokale IP, Admin-Status, Typ. HA: Modus, Gruppe, Mitglieder mit
     Rolle und Priorität (sortiert), Session-Pickup, Session-Sync. Policy-Route: eingeschaltet,
     Name, Aktion, Gateway, Ausgang (Reihenfolge = Vorrang bleibt), Bedingung, ToS, SD-WAN-Zonen
     und -Mitglieder. L3Out: VRF, Border-Leafs mit statischen Routen, Interfaces/SVIs, Peers,
     externe EPGs.
   - **Gleichwertiges ist keine Änderung:** andere Reihenfolge, Zeitstempel, Zähler, Kommentare,
     Pfad-IDs, Tunnel-Angaben. Neue Felder des Collectors zählen erst, wenn sie in der Positivliste
     stehen.
4. **Änderung** `added` / `removed` / `changed`; vorher/nachher enthalten nur die verglichenen
   Felder. Dazu **Vermerke**:
   - `baseline` – **erster Lauf** eines Quellsystems (kein Stand und kein Verlauf): keine einzelnen
     Änderungen, nur „Ausgangsstand“ mit den Zahlen. Bestehende Installationen haben einen Stand:
     der Verlauf beginnt mit dem nächsten Lauf.
   - `unreadable` – ein Kontext ist in diesem Lauf (teilweise) **nicht lesbar**: was in einem der
     beiden Läufe nicht lesbar war (Tabelle und Familie nach der Angabe des Collectors, unbekannte
     Angabe = der ganze Kontext), wird nicht einzeln verglichen – ein Vermerk mit der Anzahl statt
     einer Flut „entfernt“. Wird es wieder lesbar, ein Eintrag „wieder lesbar“ (neuer
     Ausgangsstand für diesen Teil) statt einer Flut „hinzugefügt“.
   - `truncated` – **Obergrenze** 5000 Einträge je Lauf (`LIMIT`); Wichtiges zuerst (Kontexte, HA,
     Nachbarn, Policy-Routen, L3Outs, dann Routen: RIB, statisch, BGP), der Rest als ein
     Sammel-Eintrag mit der Anzahl je Art und Änderung.
   - Ein **neuer bzw. nicht mehr gelesener Kontext** (neuer VDOM, VDOM ohne Leserecht, neuer
     Border-Leaf) ist ein Eintrag `context` mit den Zahlen, nicht jede Route darin.
   - **Gerätename gewechselt** (genau ein Knoten vorher und nachher, z. B. HA-Wechsel): ein
     Eintrag; verglichen wird unter dem neuen Namen.
5. **Gespeichert werden nur Daten** (Arten, Schlüssel, Felder), keine Texte. Kurztext („vorher →
   nachher“) und Feldnamen entstehen beim Abruf in der Sprache der Anfrage (ADR 0028).
6. **Aufbewahrung:** eigene Frist `retention_topo_changes_days` (Standard 180 Tage,
   `AUTOMATO_RETENTION_TOPO_CHANGES_DAYS`, `0` = nie) in `store/retention.py`, wie die übrigen
   Fristen (täglich, `GET /system/retention`, `POST /system/retention/purge`, `automato purge`,
   Audit `retention.purged` mit `topo_changes`). Beim Löschen eines Quellsystems bzw. Mandanten
   fällt sein Verlauf mit.
7. **API** `GET /routing/changes` (viewer, seitenweise mit `X-Total-Count`, neuste zuerst, je Lauf
   Wichtiges zuerst): Filter `tenant_id`, `source_system_id`, `context`, `kind`, `change` (auch
   `note` = alle Vermerke), `q` (Wörter in Schlüssel, Gerät, Kontext, vorher/nachher – Präfix,
   Next-Hop, Nachbar), `since`/`until` (ISO 8601, ohne Zeitzone = UTC), `limit`/`offset`; je
   Eintrag `fields` (Feld, Bezeichnung, vorher, nachher als Text), `summary`, `run_exists`. Mit
   Kunde oben (ADR 0018, 0.76.0) nur sein Ausschnitt: Routen wie in der Routen-Liste (in geteilten
   Kontexten nur zu seinen Netzen), Nachbarn/Policy-Routen/Kontexte/L3Outs in seinen Kontexten, HA
   an seinen Firewalls. **Kunden-Benutzer** erreichen den Verlauf nicht (nicht in der Freigabeliste).
   Dazu `GET /routing/routes` je Route `changed_at` (letzte Änderung neu bzw. geändert) und
   `GET /routing/summary` `changes_7d`.
8. **Oberfläche:** Routing → Reiter **„Verlauf“** (`#routing-changes`): Filter Quellsystem,
   Kontext, Art, Änderung, Zeitraum, Suche; Tabelle mit Zeit, Gerät/Kontext, Art, Schlüssel,
   Änderung, vorher → nachher (Detail je Feld aufklappbar) und Link zum Lauf (Ansicht Läufe mit
   gewähltem Quellsystem, Lauf hervorgehoben; nach Ablauf der Frist der Läufe nur die Kennung).
   Leer: „Verlauf beginnt mit dem nächsten Lauf“. In der Karte „Gelesene Kontexte“ die Zahl der
   Änderungen der letzten 7 Tage, an den Routen „geändert <Zeit>“ mit Sprung in ihren Verlauf.

## Konsequenzen

- Fragen wie „seit wann“ und „was hat sich mit dem letzten Lauf geändert“ sind beantwortbar,
  ohne den ganzen Stand je Lauf zu speichern. Ein Lauf ohne Änderung kostet nur den Vergleich.
- Der Vergleich lädt den alten Stand einmal je Lauf (nur die verglichenen Spalten, als feste
  JSON-Form je Schlüssel); bei sehr grossen ACI-Tabellen ist das Speicher und Zeit im Einspielen,
  aber in der Grössenordnung des Ersetzens selbst.
- **Grenzen:**
  - Der Verlauf sieht nur, was zwischen zwei Läufen verschieden ist – ein Flattern zwischen den
    Läufen bleibt unsichtbar (Takt = Zeitplan des Quellsystems).
  - Was während eines nicht lesbaren Laufs geändert wurde, erscheint nicht einzeln; der nächste
    lesbare Lauf ist für diesen Teil ein neuer Ausgangsstand. Ebenso für einen Kontext, der
    zwischendurch fehlte.
  - Ein VDOM ohne Leserecht fehlt im Lauf ganz und erscheint als „Kontext nicht mehr gelesen“ –
    der Orchestrator kann „gelöscht“ und „nicht lesbar“ hier nicht unterscheiden.
  - Ein Gerätename-Wechsel wird nur bei genau einem Knoten erkannt (FortiGate); beim ACI ändern
    sich umbenannte Leafs als neuer bzw. nicht mehr gelesener Kontext.
  - HA-Angaben, die global nicht lesbar waren, stehen nur in der Lauf-Meldung; fehlt der HA-Stand,
    ist das ein Eintrag „entfernt“ (einer, keine Flut).
- Mehr gespeicherte Daten (R14): begrenzt durch die Frist und die Obergrenze je Lauf; der Verlauf
  enthält nur Routing-Daten, die ohnehin gelesen werden, keine Secrets.

## Umsetzung

- Modell `TopoChange` (`models/topology.py`), Migration `0057`, `store/topo_changes.py` (`State`,
  `from_db`, `from_rows`, `diff`, `limit`, `record`, `last_changed`, `fields`, `summary`),
  Aufruf in `topology.replace` (Zeilen erst bauen, vergleichen, dann ersetzen), Löschen in
  `store/deletion.py`, Frist in `store/retention.py`/`config.py`/CLI/Audit-Allowlist.
- API in `api/routing.py`, Oberfläche `Routing.tsx` (`ChangesTab`), `Runs.tsx` (Link mit
  `source_system_id`/`run`), `System.tsx` (Frist), Typen in `api.ts`.
- Tests `orchestrator/tests/test_routing_changes.py`, UI `e2e/routing_changes.spec.ts`
  (Beispieldaten: fw-05 mit einem Lauf davor, `e2e/seed.py`).
