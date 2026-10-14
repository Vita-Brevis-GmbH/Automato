# Dev: externen Collector-Agent einbinden (mit frischer NetBox)

Schritt-für-Schritt, um auf der Dev-VM **sauber** (frische Daten) neu aufzusetzen, einen
Agenten im UI zu registrieren und einen externen Lauf durchzuspielen. Bezug: ADR 0008
(externer Collector), `collectors/EXTERNAL.md`, `lab/README.md`.

> Warum frisch? Datenmodell/Namensprofil haben sich geändert (Alembic bis `0013`). Ein
> sauberer Reset von NetBox **und** der Automato-DB vermeidet Altlasten.

## 1. NetBox neu installieren (Daten löschen)

`NETBOX_RESET=1` löscht die NetBox-Volumes vor dem Neuaufbau:

```bash
cd source-of-truth/netbox
NETBOX_RESET=1 NETBOX_PORT=8080 ./setup.sh
# Am Ende ausgegeben: Collector-Token (read-only) + Admin-Token -> notieren
```

(Optional Lab-Daten seeden: `NETBOX_URL=http://localhost:8080 NETBOX_TOKEN=<admin-token>
python3 seed_lab.py`.)

## 2. Automato-Stack frisch hochfahren (leere Orchestrator-DB)

`AUTOMATO_RESET=1` löscht die Automato-Volumes; die Alembic-Migrationen (bis `0013`)
laufen beim Start automatisch, die DB ist danach leer und aktuell:

```bash
cd ../..                      # Repo-Wurzel
NETBOX_TOKEN=<collector-token aus Schritt 1> AUTOMATO_RESET=1 ./lab/up-automato.sh
# Orchestrator: http://localhost:8000  ·  Web-UI (Dev): http://localhost:5173
```

## 3. Mandant anlegen (falls noch keiner)

Im UI-Tab **„Mandant / Standorte"** (oder per CLI):

```bash
docker compose exec orchestrator automato ensure-tenant --name lab --code lab01
docker compose exec orchestrator automato ensure-source --name fw-01 --platform fortigate
```

## 4. Agent im UI registrieren

UI → Tab **„Agenten"**:

1. Name (z. B. `agent-dev-01`) + Mandant wählen → **Agent anlegen**.
2. Das **Token** wird **einmalig** angezeigt → kopieren (danach nur noch der Hash gespeichert).

Der Agent erscheint in der Liste; „Zuletzt gesehen" füllt sich beim ersten Pull.
Deaktivieren/Aktivieren über die Statusspalte (ein deaktivierter Agent authentisiert nicht mehr).

## 5. Externen Lauf durchspielen (Dev)

Auf Dev kann der Agent lokal aus dem Repo laufen (ohne Windows). Ziel-Creds optional —
ohne echte FortiGate liest der Collector Beispieldaten.

```bash
# (a) Erreichbarkeit prüfen (optional, nur mit echten Ziel-Creds sinnvoll)
AUTOMATO_FORTIGATE_URL=https://<fw> AUTOMATO_FORTIGATE_TOKEN=<ro> \
  uv run --package collectors automato-collector --check --platform fortigate

# (b) Externen Lauf anlegen (dev-Modus = Admin, kein Login nötig). tenant_id/source_system_id
#     aus dem UI bzw. der DB. execution=external -> kein zentraler Worker, der Agent holt ihn.
curl -s -X POST http://localhost:8000/jobs/collect -H "Content-Type: application/json" \
  -d '{"platform":"fortigate","tenant_id":1,"source_system_id":1,"execution":"external"}'
# -> {"run_id":"<RUN_ID>","status":"queued"}

# (c) Agent holt den Lauf per Pull und führt ihn aus
AUTOMATO_ORCHESTRATOR_URL=http://localhost:8000 AUTOMATO_AGENT_TOKEN=<token aus Schritt 4> \
  uv run --package collectors automato-collector --poll
# -> "fortigate run <RUN_ID>: completed"
```

Danach im UI: **„Agenten"** zeigt „Zuletzt gesehen", und die Regeln/Findings/Applikations-Doku
sind befüllt.

## Produktion/Windows

In Produktion läuft der Agent **nicht** aus dem Repo, sondern als Dienst nahe am Zielsystem:
`collectors/EXTERNAL.md` (Env, mTLS) und `deploy/collector-agent/` (Windows-`install.ps1`,
Scheduled Task `--poll`). Nur ausgehend HTTPS, Agent-Token/Client-Zertifikat je Agent rotierbar.
