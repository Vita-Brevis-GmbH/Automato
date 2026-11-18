# ADR 0069 — Export der Applikations-Doku im Hintergrund, Passwort nur im Speicher

## Status

**Angenommen** (2026-11-18) – zur Abnahme durch den Anwender.

Anwender 2026-11-18:

- „Ich habe eine Funktion bei Automato v1.0.1 einbauen lassen, kannst du mir diese hier auch
  einbringen und mergen lassen?“ – Doku aller Applikationen als ZIP je Applikation mit
  Export-Passwort (AES-256) und Findings als Excel (Branch auf Basis 1.9.0, per Merge übernommen).
- „Weiter wäre es noch super, wenn ich eine Auswahl treffen könnte, welche Applikationen ich
  herunterladen möchte.“
- „Zudem geht es sehr lange, bis der Export steht, könnte man dies als Hintergrunddienst laufen
  lassen?“

Ergänzt ADR 0026 (Export für Confluence), ADR 0037 (Doku als Stand) und ADR 0068 (Rechte im eigenen
Mandanten).

## Kontext

- Der Gesamt-Export aus 1.0.1 (`POST /applications/export`) rechnete in der Anfrage und lieferte
  das ZIP als Antwort. Bei vielen Applikationen dauerte das Minuten – Proxy und Browser warten, ein
  Abbruch der Verbindung verliert alles.
- Gemessen (synthetische Umgebung aus `scripts/bench_applications.py`, 10 ACI-Tenants, 310 EPGs,
  3000 Firewall-Regeln, 51 Applikationen, Postgres): mit vorgerechneten Ständen der Doku 68 s,
  ohne Stände 212 s. 90 % der Zeit mit Ständen kosteten die **Abhängigkeiten**: jede Applikation
  rechnete die Fussabdrücke aller anderen neu (51 × 51 = 2601 statt 51) – das wächst mit dem
  Quadrat der Applikationen.
- Das Export-Passwort darf nie gespeichert werden (1.0.1: „nicht gespeichert, nicht im Audit“).
  Ein Hintergrundauftrag braucht es aber, bis die letzte Applikation verschlüsselt ist.
- Der Container ist gehärtet (Dateisystem nur lesend); der Orchestrator schreibt nur ins Volume
  `orchestrator-data` (`/var/lib/automato`) und nach `/tmp` (tmpfs, im Speicher).
- Der Orchestrator läuft als **ein** Prozess je Datenbank (uvicorn ohne Worker, Dockerfile).

## Entscheidung

1. **Auswahl:** der Dialog listet die Applikationen des gewählten Mandanten bzw. Kunden mit Suche
   und „alle“/„keine“; die Anfrage nennt die Ids. Jede Id muss für den Benutzer sichtbar sein
   (Mandant, Kunde) – sonst **404 mit den Ids**, kein Auftrag, nie still ausgelassen. Ohne `ids`
   alle der Liste (wie 1.0.1).
2. **Auftrag im Prozess des Orchestrators:** die Anfrage legt eine Zeile `export_job` an
   (Migration `0096_export_auftrag`) und kehrt mit **202** zurück. Ein Thread rechnet je
   Applikation Doku und Plan, verschlüsselt sie mit dem Passwort und hängt das ZIP an die Datei
   an; nach jeder Applikation stehen Fortschritt (n von m, aktuelle Applikation) und Fehler je
   Applikation in der Zeile. Höchstens `export_parallel` (2) Aufträge rechnen gleichzeitig,
   weitere warten; ein Auftrag je Benutzer zugleich (409). Eine scheiternde Applikation bricht
   nichts ab – sie steht im Auftrag und in `FEHLER.txt`.
3. **Passwort nur im Speicher:** es kommt im Rumpf der Anfrage und lebt nur in der Arbeit des
   Threads (Closure). Nie in Datenbank, Redis/Celery, Protokoll, Audit (Allowlist ohne Passwort),
   Dateiname oder Datei; die fertige Datei enthält nur die verschlüsselten ZIPs. Ein Test sucht es
   nach einem Export in allen Tabellen, im Audit, im Protokoll und in der Ablage.
   Darum kein Celery: der Auftrag müsste das Passwort über Redis an den Worker geben.
4. **Ablage:** `export_dir` = `/var/lib/automato/exports` im Volume `orchestrator-data` (nicht
   `/tmp`: tmpfs belegt Arbeitsspeicher und ist nach einem Neustart leer); während des Rechnens
   `<id>.part`, danach `<id>.zip`, die Id ist zufällig (uuid4). Höchstens `export_max_mb`
   (2048 MB) je Export, darüber „Fehler“.
5. **Nur der Auftraggeber:** Liste, Fortschritt, Abbrechen, Entfernen und Download nur für den
   Benutzer, der den Auftrag gestellt hat (`Principal.subject`, bei Realms mit Präfix), und nur,
   solange er den Mandanten sieht – alle anderen (auch Admins) bekommen 404.
6. **Ablauf:** `export_hours` (24 h) nach dem Ende löscht der Orchestrator Zeile und Datei – beim
   Start, alle 10 Minuten (eigener Thread, unabhängig vom Zeitplan) und bei jeder Liste; Dateien
   ohne Auftrag (Reste) ebenso. Löschen des Mandanten nimmt seine Aufträge mit.
7. **Neustart:** ein wartender bzw. laufender Auftrag gilt beim Start als **abgebrochen**
   (`aborted`, Grund „neu gestartet … Passwort wird nicht gespeichert“), die halbe Datei wird
   gelöscht – sichtbar in „Meine Exporte“, nie still.
8. **Schneller:** die Fussabdrücke der Abhängigkeiten rechnet der Export je Applikation einmal
   (`applications.Footprints`), dazu je Mandant einmal Auflösung der Mitglieder, VMs, Standorte,
   Quellsysteme und Namen der Gegenstellen (`api/applications.Bulk`); das Lesen protokolliert der
   Auftrag einmal statt je Applikation, das ZIP je Applikation entsteht ohne Umweg über ein
   unverschlüsseltes ZIP. Gemessen (gleiche Umgebung): mit Ständen 68 s → 7 s, ohne Stände
   212 s → 160 s (dort rechnen Doku und Wege, unverändert).
9. **Rechte:** lesen – Betreuer ab `viewer`, Mandanten-Benutzer im eigenen Mandanten mit
   `mandant.read` (ADR 0068, `auth/tenant_routes.py`). **Nicht** für Kunden-Benutzer in
   gemeinsamen Mandanten: sie laden heute nur ihre Applikationen einzeln ohne Verbindungen und
   Plan – der Gesamt-Export (und der Excel-Export der Findings) bleibt ihnen verschlossen (enger).
   Der Mandant steht im Rumpf: das Modul „Doku“ prüft der Endpunkt selbst.
10. **Findings als Excel bleibt direkt:** gemessen 5000 Findings rund 2 s, 20 000 rund 6 s (ohne
    Stil je Zelle, vorher 2,4 s bzw. 8 s) – kein Hintergrund nötig. Mandanten mit ausgeschaltetem
    Modul Findings fehlen im globalen Export.

## Folgen

- API: `POST /applications/export` antwortet 202 mit dem Auftrag (statt der Datei);
  `GET /applications/exports`, `GET …/exports/{id}`, `POST …/{id}/cancel`, `DELETE …/{id}`,
  `GET …/{id}/download`. Die Router-Reihenfolge in `main.py` setzt die Exporte vor
  `/applications/{id}`.
- Ein Update bzw. Neustart bricht laufende Exporte ab; der Benutzer startet sie neu.
- Plattenplatz im Volume: höchstens die Exporte der letzten 24 h, je Export bis 2048 MB.
- Mehrere Orchestrator-Prozesse je Datenbank würden die Aufträge der anderen beim Start als
  abgebrochen markieren – heute läuft genau einer (wie beim NetBox-Sync, `abort_interrupted`).
