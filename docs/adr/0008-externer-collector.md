# ADR 0008 — Externer/dezentraler Collector (Agent)

## Status

Angenommen (2026-10-14). Erster Schritt umgesetzt; echter APIC-/FortiGate-Abruf über den
Agenten folgt mit Lab-/Kundenzugang.

## Kontext

Die Zielsysteme (FortiGate, Cisco APIC) stehen in **Kundennetzen**, die von der zentralen
Automato-Plattform **nicht erreichbar** sind (oft nur von einer dedizierten Station im
Kundennetz, teils Windows). Der Collector muss daher dort laufen, wo das Zielsystem
erreichbar ist — nicht zwingend zentral. „Modularität" (CLAUDE.md) heißt hier konkret:
das **Zugriffsmodul** (Verbindung zum Zielsystem) muss sich vom zentralen Betrieb lösen und
als eigenständiger Agent betreiben lassen.

## Entscheidung

Der Collector ist ein **eigenständiger Agent**, der nahe am Zielsystem läuft und nur
**ausgehend HTTPS** zum Orchestrator spricht:

```
Kundennetz:  [Zielsystem] ──read-only──▶ Collector-Agent ──┐
                                                           │  nur ausgehend HTTPS (443)
Zentral:     Orchestrator /internal  ◀────────────────────┘  (Token/mTLS, ADR 0005/0007)
```

- **Zugriffsmodul extrahiert:** je Plattform eine celery-freie Funktion `collect_once(run_id)`
  (gather → normalize → submit). Der Celery-Task ist nur noch ein dünner Wrapper; dieselbe
  Logik läuft im zentralen Worker **und** im Agenten.
- **Standalone-Runner:** `automato-collector --platform … --run-id …` (`collectors.run`)
  führt genau **eine** Erfassung ohne Celery/Redis aus. Keine DB-Kopplung — der einzige
  Rückkanal ist die interne API (`POST /internal/collector-runs/{run_id}/result`,
  `X-Internal-Token`).
- **Lauf-Modell:** `POST /jobs/collect` mit `execution=external` legt den Lauf **an, ohne**
  ihn an Celery zu dispatchen; der Agent wird mit der `RUN_ID` (out-of-band) ausgelöst,
  erfasst und liefert ab. `execution=inprocess` (Default) = bisheriger zentraler Worker.
- **Keine eingehende Verbindung** ins Kundennetz, kein zentraler Zugriff aufs Zielsystem.
  Secrets (Ziel-Creds, interner Token) liegen **beim Agenten** (ADR 0007 Secrets-Provider:
  `env:`/`file:`/`openbao:`), nie im Repo.

## Konsequenzen

- Eine Codebasis (`collectors/`), zwei Betriebsarten: zentraler Worker oder externer Agent.
  Windows-Betrieb: Python + `pip install` des `collectors`-Pakets, `AUTOMATO_*`-Env setzen,
  `automato-collector` per Task-Scheduler/Dienst (Details: `collectors/EXTERNAL.md`).
- **Sicherheit:** Angriffsfläche minimal (nur ausgehend 443). Der interne Token sollte je
  Agent/Kunde rotierbar sein; mTLS zwischen Agent und `/internal` ist Folge-Hardening
  (ADR 0005). Read-only bleibt erzwungen (Collector schreibt nie ins Zielsystem).
- **Pull-Modell umgesetzt:** Agenten werden registriert (`POST /agents`, admin; Token
  einmalig, nur Hash gespeichert) und holen Läufe per `GET /internal/agent/next-run`
  (`X-Agent-Token`), der den ältesten offenen **externen** Lauf im Tenant-Scope auf RUNNING
  schaltet; `POST /internal/agent/heartbeat` hält `last_seen` aktuell. Der Lauf trägt
  `collector_run.execution` (inprocess|external); nur `external` geht an Agenten, nur
  `inprocess` an den zentralen Worker. Runner-Modus `automato-collector --poll`. Damit ist
  kein out-of-band-RUN_ID-Handling mehr nötig. Synergie Magister (ADR 0007 D6):
  gemeinsames Agent-/Cockpit-Muster.
- **Noch offen:** echter APIC-REST-Client (Login + MO-Subtree) im Agenten; mTLS Agent↔
  `/internal`; Agent-Deaktivierung/Rotation-UI; Windows-Paketierung.
