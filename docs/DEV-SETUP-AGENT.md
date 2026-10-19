# Dev: externen Collector-Agent einbinden (mit frischer NetBox)

Schritt-für-Schritt, um auf der Dev-VM **sauber** (frische Daten) neu aufzusetzen, einen
Agenten im UI zu registrieren und einen externen Lauf durchzuspielen. Bezug: ADR 0008
(externer Collector), `collectors/EXTERNAL.md`, `lab/README.md`.

> **Windows-Agent:** Die Einrichtung steht direkt im Web-UI unter **Collector-Agenten →
> „Einrichtung eines Agenten (Windows)“** – inkl. `agent.env`-Generator und Download des
> Agent-Pakets (ZIP mit collectors-Wheel, `install.ps1`, `run-agent.ps1`). Ein eigener
> Build-Rechner mit `uv` ist dafür nicht nötig.

> Warum frisch? Datenmodell/Namensprofil haben sich geändert (Alembic bis `0013`). Ein
> sauberer Reset von NetBox **und** der Automato-DB vermeidet Altlasten.

## 1. NetBox neu installieren (Daten löschen, **leer lassen**)

`NETBOX_RESET=1` löscht die NetBox-Volumes vor dem Neuaufbau:

```bash
cd source-of-truth/netbox
NETBOX_RESET=1 NETBOX_PORT=8080 ./setup.sh
```

**Der Collector-Token (read-only) wird am Ende ausgegeben** – notieren, das ist der Wert für
`NETBOX_TOKEN` in Schritt 2:

```
Collector-Token (read-only): <40 Hex-Zeichen>
Admin-Token (Schreiben/Seed): <40 Hex-Zeichen>
```

Token vergessen? Einfach mit festem Wert neu setzen (idempotent, löscht die alten Tokens):

```bash
NETBOX_RESET=1 NETBOX_PORT=8080 \
  COLLECTOR_TOKEN=$(openssl rand -hex 20) ADMIN_TOKEN=$(openssl rand -hex 20) ./setup.sh
```

> **NetBox bleibt leer** – `setup.sh` legt nur Admin-/Collector-User + Tokens an, **keine
> IPAM-Daten**. Für einen sauberen, leeren Stand das optionale `seed_lab.py` **nicht** laufen
> lassen; auch den FortiGate→NetBox-Import (`import_from_fortigate.py`) weglassen. Die Plattform
> funktioniert mit leerer NetBox – unaufgelöste Endpunkte landen dann als Findings.

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
docker compose exec orchestrator automato ensure-source --tenant <mandant> --name fw-01 --platform fortigate
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

# (b) Externen Lauf anlegen - per CLI im Container (funktioniert auch mit Login/OIDC,
#     kein Token noetig). execution external -> kein zentraler Worker, der Agent holt ihn.
docker compose exec orchestrator automato collect --platform fortigate \
  --tenant lab --source fw-01 --execution external
# -> run_id=<RUN_ID> status=queued ... Externer Lauf: wartet auf einen Agenten

# (c) Agent holt den Lauf per Pull und führt ihn aus. Der Ergebnis-Upload braucht das
#     interne Token aus der .env des Orchestrators.
AUTOMATO_ORCHESTRATOR_URL=http://localhost:8000 AUTOMATO_AGENT_TOKEN=<token aus Schritt 4> \
  AUTOMATO_INTERNAL_API_TOKEN=$(sed -n 's/^AUTOMATO_INTERNAL_API_TOKEN=//p' .env) \
  uv run --package collectors automato-collector --poll
# -> "fortigate run <RUN_ID>: completed"

# (d) Status prüfen
docker compose exec orchestrator automato status --run-id <RUN_ID>
```

Mit Login (`AUTOMATO_AUTH_MODE=oidc`) geht `curl` gegen `/jobs/collect` nur mit Bearer-Token;
die CLI im Container braucht keins.

### Windows-Agent im Dev

Paket aus `deploy/collector-agent/` installieren (`install.ps1`), dann
`C:\Automato\collector\agent.env` füllen (Vorlage `agent.env.example`):
`AUTOMATO_ORCHESTRATOR_URL=http://<dev-vm>:8000` (direkt, **nicht** über den Web-UI-Proxy —
der liefert `/internal` nicht aus), `AUTOMATO_AGENT_TOKEN`, `AUTOMATO_INTERNAL_API_TOKEN` und
die FortiGate-Werte. TLS-/Zertifikatszeilen nur einkommentieren, wenn die Dateien existieren.
Die Station braucht Zugriff auf Port 8000 der Dev-VM und auf die FortiGate.

```powershell
cd C:\Automato\collector
.\run-agent.ps1 -Check        # Zielsystem erreichbar? (Exit 0 = OK)
.\run-agent.ps1               # externen Lauf holen und ausführen (sonst: geplante Aufgabe)
```

Danach im UI: **„Agenten"** zeigt „Zuletzt gesehen", und die Regeln/Findings/Applikations-Doku
sind befüllt.

## Produktion/Windows

In Produktion läuft der Agent **nicht** aus dem Repo, sondern als Dienst nahe am Zielsystem:
`collectors/EXTERNAL.md` (Env, mTLS) und `deploy/collector-agent/` (Windows-`install.ps1`,
Scheduled Task `--poll`). Nur ausgehend HTTPS, Agent-Token/Client-Zertifikat je Agent rotierbar.

> **Hinweis zur Windows-Installation:** `install.ps1` baut ein venv und installiert das
> Collector-Wheel per `pip`. Das Wheel zieht seine Abhängigkeiten (httpx, pydantic, …) nach,
> daher braucht die Station beim Installieren **Internet-Zugang zu PyPI** – oder einen
> vorbereiteten Offline-Wheelhouse (`pip download` auf einem Build-Host, dann
> `pip install --no-index --find-links <wheelhouse> collectors`). Alternativ die
> Python-freie `.exe` (PyInstaller, siehe `deploy/collector-agent/README.md`). Zur Laufzeit
> spricht der Agent nur **ausgehend HTTPS** zum Orchestrator.
