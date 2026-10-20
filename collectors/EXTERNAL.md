# Externer Collector-Agent (ADR 0008/0009)

Betrieb des Collectors **nahe am Zielsystem** (Kundennetz, oft Windows), wenn die zentrale
Plattform FortiGate/APIC nicht erreicht. Der Agent spricht nur **ausgehend HTTPS** zum
Orchestrator; read-only gegenüber dem Zielsystem.

## Geräte & Zugangsdaten (ADR 0009)

Geräte werden **nicht am Agenten** konfiguriert, sondern im Web-UI beim Mandanten unter
**Quellsysteme**: Adresse, read-only Zugangsdaten (FortiGate: API-Token; Cisco APIC: Benutzer
+ Passwort und optional die ACI-Tenants), TLS-Prüfung und **Verwaltet von** (Agent).
Der Orchestrator speichert die Zugangsdaten verschlüsselt und gibt es **nur dem zugewiesenen
Agenten** – pro Lauf (`next-run` → `target`) bzw. für `--check` (`/internal/agent/targets`).
Ein Agent kann beliebig viele Geräte lesen; Umverteilen = im UI einen anderen Agenten wählen.
Den Ergebnis-Upload signiert der Agent mit seinem **Agent-Token** – das interne Service-Token
des Orchestrators braucht er nicht. Ohne Zugangsdaten bricht ein Agent den Lauf ab (keine
Beispieldaten im Kundennetz).

**Referenzquellen (ADR 0010):** Auch eine **Infoblox DDI** (WAPI-Benutzer + Passwort) oder eine
NetBox (Lese-Token) kann einem Agenten zugewiesen werden. Der Agent liest die Quelle komplett
(seitenweise) und lädt sie in Teilen hoch (`POST /internal/collector-runs/{id}/references`,
je bis 2000 Einträge); das Ergebnis meldet die Anzahl – passt sie nicht, verwirft der
Orchestrator den Teilstand. Alle Lese-Clients (FortiGate, APIC, Infoblox, NetBox) lassen im
Code nur GET zu (APIC zusätzlich Login/Logout); ein NetBox-**Schreib**-Token bekommt ein Agent
nie – geschrieben wird nur vom Orchestrator (NetBox-Sync). Das Agent-Paket enthält dafür das
`sot-gateway`-Wheel.

## Ablauf (Alt: RUN_ID out-of-band)

> Normalfall ist das **Pull-Modell** (unten, `--poll`). Der folgende Weg mit `--run-id`
> bleibt für den zentralen Worker/Tests und nutzt das interne Token.

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

## Erreichbarkeit vorab prüfen

Vor dem ersten echten Lauf im Kundennetz die Verbindung/Authentisierung zu **allen
zugewiesenen Geräten** testen (read-only, kein Ingest):

```bash
automato-collector --check                 # optional: --platform fortigate
# fw-01 (fortigate) check: OK - erreichbar: https://fw-01.kunde.intern
# apic-01 (cisco_aci) check: OK - erreichbar: https://apic-01.kunde.intern (5 Tenants sichtbar)
# ddi (infoblox) check: OK - erreichbar: https://ddi.kunde.intern (WAPI v2.12)
# fw-02 (fortigate) check: FEHLER - HTTPStatusError: 401 ...
# Exit 0 = alle OK, 2 = mindestens ein Gerät fehlerhaft
```

Ohne Agent-Token bzw. ohne zugewiesene Geräte prüft `--check` die lokale Konfiguration
(`AUTOMATO_FORTIGATE_URL/_TOKEN` bzw. `AUTOMATO_ACI_URL/_USERNAME/_PASSWORD`).

## Installation (Windows)

- Python 3.11+.
- `pip install .` im `collectors/`-Paket (oder ein gebautes Wheel ausliefern).
- `AUTOMATO_*`-Umgebungsvariablen setzen (s. u.).
- `automato-collector …` per **Aufgabenplanung**/Dienst zeitgesteuert ausführen.

## Konfiguration (Env, Präfix `AUTOMATO_`)

| Variable | Zweck |
|---|---|
| `AUTOMATO_ORCHESTRATOR_URL` | Basis-URL des Orchestrators (intern erreichbar) |
| `AUTOMATO_AGENT_TOKEN` | Agent-Token fürs Pull-Modell (einmalig bei `POST /agents`): Läufe abholen + Ergebnis abliefern |
| `AUTOMATO_INTERNAL_API_TOKEN` | nur zentraler Worker / `--run-id`-Modus – **nicht** am Agenten |
| `AUTOMATO_FORTIGATE_URL` / `_TOKEN` / `_VERIFY_TLS` | nur Rückfall; Normalfall: am Quellsystem im Web-UI (ADR 0009) |
| `AUTOMATO_ACI_URL` / `_USERNAME` / `_PASSWORD` / `_VERIFY_TLS` / `_TENANTS` | Cisco APIC, nur Rückfall (wie oben) |
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

`--poll` holt den ältesten offenen Lauf mit `execution=external` eines **diesem Agenten
zugewiesenen** Geräts im Tenant-Scope (`GET /internal/agent/next-run`, inkl. `target` mit
Adresse/Token) und führt ihn aus; ohne offenen Lauf endet er mit 0. Läufe von Geräten ohne
Agent-Zuweisung (Altbestand) kann jeder Agent des Mandanten abholen, bekommt dafür aber
keine Zugangsdaten.

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
