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
| `AUTOMATO_AGENT_TOKEN` | Agent-Token fürs Pull-Modell (einmalig bei `POST /agents`) |
| `AUTOMATO_FORTIGATE_URL` / `_TOKEN` / `_VERIFY_TLS` | FortiGate (read-only) |
| APIC-Creds (Phase 2) | Cisco APIC (read-only) |
| `AUTOMATO_ORCHESTRATOR_CA_BUNDLE` | CA-Pin für den Orchestrator (TLS-Server) |
| `AUTOMATO_CLIENT_CERT` / `AUTOMATO_CLIENT_KEY` | mTLS-Client-Zertifikat des Agenten |

Secrets nicht im Klartext ablegen: Referenzen `env:`/`file:`/`openbao:` nutzen
(ADR 0007, `deploy/secrets/README.md`). Nichts davon gehört ins Repo.

## Pull-Modell (Agent-Queue)

Statt die `RUN_ID` out-of-band zu übergeben, kann der Agent Läufe **abholen**:

```bash
# einmalig (zentral, admin): Agent registrieren -> Token NUR hier sichtbar
curl -X POST https://orchestrator.intern/agents -H "Authorization: Bearer <admin>" \
  -H "Content-Type: application/json" -d '{"name":"agent-win-01","tenant_id":1}'
# -> {"id":.., "name":"agent-win-01", "token":"<AGENT_TOKEN>"}

# am Agenten (Task-Scheduler): naechsten externen Lauf holen und ausfuehren
AUTOMATO_AGENT_TOKEN=<AGENT_TOKEN> automato-collector --poll
```

`--poll` holt den ältesten offenen Lauf mit `execution=external` im Tenant-Scope des
Agenten (`GET /internal/agent/next-run`) und führt ihn aus; ohne offenen Lauf endet er mit 0.

## Netz & Sicherheit

- **Nur ausgehend 443** vom Agenten zum Orchestrator; **keine** eingehende Verbindung ins
  Kundennetz, **kein** zentraler Zugriff aufs Zielsystem.
- Read-only erzwungen — der Collector schreibt nie ins Zielsystem.
- **mTLS (empfohlen):** Agent präsentiert ein Client-Zertifikat (`AUTOMATO_CLIENT_CERT/_KEY`)
  und pinnt die Server-CA (`AUTOMATO_ORCHESTRATOR_CA_BUNDLE`). Serverseitig erzwingt der
  Reverse-Proxy vor `/internal` Client-Zertifikate, z. B. Caddy:

  ```caddyfile
  orchestrator.intern {
      @internal path /internal/*
      tls /srv/orch.crt /srv/orch.key {
          client_auth {
              mode require_and_verify
              trusted_ca_cert_file /srv/agents-ca.crt
          }
      }
      reverse_proxy @internal orchestrator:8000
  }
  ```

  Der interne Token (`X-Internal-Token`) bleibt zusätzlich als zweite Schicht. Agent-Token
  bzw. Client-Zertifikate je Agent/Kunde rotierbar.
