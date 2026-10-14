# Externer Collector-Agent (ADR 0008)

Betrieb des Collectors **nahe am Zielsystem** (Kundennetz, oft Windows), wenn die zentrale
Plattform FortiGate/APIC nicht erreicht. Der Agent spricht nur **ausgehend HTTPS** zum
Orchestrator; read-only gegenüber dem Zielsystem.

## Ablauf

1. **Lauf anlegen** (zentral, einmal pro Erfassung) — ohne Celery-Dispatch:

   ```bash
   curl -X POST https://orchestrator.intern/jobs/collect \
     -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
     -d '{"platform":"fortigate","tenant_id":1,"source_system_id":2,"execution":"external"}'
   # -> {"run_id":"<RUN_ID>","status":"queued"}
   ```

2. **Agent ausführen** (im Kundennetz, mit Zugriff aufs Zielsystem):

   ```bash
   automato-collector --platform fortigate --run-id <RUN_ID>
   # bzw.: python -m collectors.run --platform cisco_aci --run-id <RUN_ID>
   ```

Der Agent erfasst (gather → normalize) und liefert das Ergebnis an
`POST /internal/collector-runs/<RUN_ID>/result` ab. Exit 0 = `completed`, 1 = `failed`.

## Installation (Windows)

- Python 3.11+.
- `pip install .` im `collectors/`-Paket (oder ein gebautes Wheel ausliefern).
- `AUTOMATO_*`-Umgebungsvariablen setzen (s. u.).
- `automato-collector …` per **Aufgabenplanung**/Dienst zeitgesteuert ausführen.

## Konfiguration (Env, Präfix `AUTOMATO_`)

| Variable | Zweck |
|---|---|
| `AUTOMATO_ORCHESTRATOR_URL` | Basis-URL des Orchestrators (intern erreichbar) |
| `AUTOMATO_INTERNAL_API_TOKEN` | Shared Secret für `/internal` (je Agent/Kunde rotierbar) |
| `AUTOMATO_FORTIGATE_URL` / `_TOKEN` / `_VERIFY_TLS` | FortiGate (read-only) |
| APIC-Creds (Phase 2) | Cisco APIC (read-only) |

Secrets nicht im Klartext ablegen: Referenzen `env:`/`file:`/`openbao:` nutzen
(ADR 0007, `deploy/secrets/README.md`). Nichts davon gehört ins Repo.

## Netz & Sicherheit

- **Nur ausgehend 443** vom Agenten zum Orchestrator; **keine** eingehende Verbindung ins
  Kundennetz, **kein** zentraler Zugriff aufs Zielsystem.
- Read-only erzwungen — der Collector schreibt nie ins Zielsystem.
- mTLS Agent ↔ `/internal` und eine Agent-Queue/Registrierung (statt out-of-band-RUN_ID)
  sind Folge-Hardening (ADR 0008 „offen").
