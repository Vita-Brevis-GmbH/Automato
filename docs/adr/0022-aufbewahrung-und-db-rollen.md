# ADR 0022 — Aufbewahrung und getrennte DB-Rollen

## Status

Entwurf (2026-10-23), umgesetzt in 0.48.0. Teil der R14-Massnahmen (RISKS.md) vor dem
Produktivbetrieb; ergänzt ADR 0005 (fail-closed) und ADR 0013 (Produktions-Gate).

## Kontext

Die zentrale Datenbank hält die Firewall-, ACI- und Netz-Topologie der ganzen Umgebung (R14).
Entscheid des Anwenders (2026-10-23): vor dem Produktivbetrieb **Fristen für das Löschen alter
Läufe** und **getrennte DB-Rollen**; TLS intern folgt in 0.49.0.

Bis 0.47 wuchsen Läufe, NetBox-Sync-Vorschauen (mit allen Aktionen als JSON) und das Audit-Log
ohne Ende. Der Orchestrator verband sich als Eigentümer der Tabellen und führte beim Start die
Migrationen aus – eine Lücke in der Anwendung hätte Tabellen ändern oder löschen können.

## Entscheidung

### Aufbewahrung

- Je Art eine Frist in Tagen, `0` = nie: Läufe 90, NetBox-Sync-Vorschauen 90, Audit 365
  (`AUTOMATO_RETENTION_RUNS_DAYS`, `…_SYNC_DAYS`, `…_AUDIT_DAYS`).
- **Der aktuelle Stand bleibt immer:** Läufe, auf die Regeln, Objekte, NAT, VMs bzw. der
  Referenzstand einer Quelle zeigen; je Quellsystem der neuste und der neuste erfolgreiche
  Lauf; offene Läufe; die neuste Vorschau je NetBox und keine, die gerade rechnet oder
  übernimmt. Liegengebliebene Teil-Uploads alter Läufe gehen mit.
- Übernommene Syncs bleiben über das Audit nachvollziehbar (`netbox_sync.applied` mit den
  Zahlen); die Vorschau mit allen Aktionen fällt nach der Frist weg.
- Der Zeitplan räumt höchstens einmal in 24 h auf; von Hand über die System-Seite bzw.
  `automato purge`. Jedes Aufräumen schreibt `retention.purged` mit den Zahlen ins Audit; der
  neuste Eintrag ist zugleich „zuletzt aufgeräumt“ – keine eigene Tabelle, keine Migration.

### Getrennte DB-Rollen

- **Migration als Eigentümer:** ein eigener Compose-Dienst `migrate` (gleiches Image) führt
  `alembic upgrade head` als `automato` aus und endet; der Orchestrator startet erst danach
  und führt selbst keine Migration mehr aus.
- **Betrieb ohne DDL:** `automato db-roles` (im Dienst `migrate`, idempotent nach jeder
  Migration) richtet die App-Rolle ein – LOGIN ohne SUPERUSER/CREATEDB/CREATEROLE, kein CREATE
  im Schema (auch nicht für PUBLIC), SELECT/INSERT/UPDATE/DELETE auf alle Tabellen,
  Sequenzen nutzen, dasselbe per DEFAULT PRIVILEGES für künftige Tabellen; den Migrationsstand
  nur lesen.
- Das Passwort erzeugt `deploy/aufbau.sh db-rollen` zufällig und trägt es in `.env` ein
  (`AUTOMATO_DB_USER`, `AUTOMATO_DB_PASSWORD`); an Postgres geht nur der SCRAM-Verifier. Der
  Orchestrator-Container bekommt nur die App-Rolle, nicht das Passwort des Eigentümers.
- Ohne Eintrag bleibt alles wie bis 0.47 (Orchestrator als Eigentümer) – kein Bruch beim
  Update; Betriebszustand, Verbindungen und `pruefen` warnen.

## Konsequenzen

- Eine Lücke im Orchestrator kann Daten lesen und ändern, aber das Schema nicht zerstören;
  Migrationen brauchen den Eigentümer und laufen nur im kurzlebigen Dienst `migrate`.
- Die Datenmenge wächst nicht mehr unbegrenzt; ältere Läufe sind nach der Frist nicht mehr in
  der Läufe-Ansicht. Wer länger aufbewahren muss, setzt die Frist höher bzw. `0`.
- Sicherungen (pg_dump als `automato`) enthalten die Rechte der App-Rolle; die Rolle selbst
  ist cluster-weit und bleibt bei einer Wiederherstellung bestehen.
- Offen: Lese-Rolle für Auswertungen, Rotation des Passworts mit Neustart automatisch,
  Fristen je Mandant.
