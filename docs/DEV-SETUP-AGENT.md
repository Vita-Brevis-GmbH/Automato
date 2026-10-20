# Dev: externen Collector-Agent einbinden (mit frischer NetBox)

Schritt-für-Schritt, um auf der Dev-VM **sauber** (frische Daten) neu aufzusetzen, einen
Agenten im UI zu registrieren, ihm ein Gerät zuzuweisen und einen externen Lauf
durchzuspielen. Bezug: ADR 0008 (externer Collector), ADR 0009 (Geräte-Zugangsdaten),
`collectors/EXTERNAL.md`, `lab/README.md`.

> **Windows-Agent:** Die Einrichtung steht direkt im Web-UI unter **Collector-Agenten →
> „Einrichtung eines Agenten (Windows)“** – inkl. `agent.env`-Generator und Download des
> Agent-Pakets (ZIP mit collectors-Wheel, `install.ps1`, `run-agent.ps1`). Ein eigener
> Build-Rechner mit `uv` ist dafür nicht nötig.

> Warum frisch? Datenmodell/Namensprofil haben sich geändert (Alembic bis `0017`). Ein
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

`AUTOMATO_RESET=1` löscht die Automato-Volumes; die Alembic-Migrationen (bis `0017`)
laufen beim Start automatisch, die DB ist danach leer und aktuell:

```bash
cd ../..                      # Repo-Wurzel
NETBOX_TOKEN=<collector-token aus Schritt 1> AUTOMATO_RESET=1 ./lab/up-automato.sh
# Orchestrator: http://localhost:8000  ·  Web-UI (Dev): http://localhost:5173
```

## 3. Mandant anlegen (falls noch keiner)

Im UI unter **„Mandanten & Standorte"** (oder per CLI):

```bash
docker compose exec orchestrator automato ensure-tenant --name lab --code lab01
```

## 4. Agent im UI registrieren

UI → **„Collector-Agenten"**:

1. Name (z. B. `agent-dev-01`) + Mandant wählen → **Anlegen & Token erzeugen**.
2. Das **Token** wird **einmalig** angezeigt → kopieren (danach nur noch der Hash gespeichert).

Der Agent erscheint in der Liste; „Zuletzt gesehen" füllt sich beim ersten Pull.
Deaktivieren/Aktivieren über die Statusspalte (ein deaktivierter Agent authentisiert nicht mehr).

## 4b. Gerät erfassen und dem Agenten zuweisen

UI → **„Mandanten & Standorte"** → Mandant anklicken → **Quellsysteme → + Quellsystem**
(bzw. **Bearbeiten**): Name `fw-01`, Plattform FortiGate, Adresse `https://<fw>`,
**Verwaltet von** = `agent-dev-01`, **API-Token** (read-only REST-API-Admin der FortiGate),
TLS-Prüfung im Lab mit selbstsigniertem Zertifikat aus. Ein **APIC** genauso: Plattform
Cisco ACI, Adresse `https://<apic>`, **APIC-Benutzer/-Passwort** (Nur-Lese-Rolle) und
optional **ACI-Tenants** (leer = alle ausser infra/mgmt). Das Token wird verschlüsselt im
Orchestrator gespeichert (Schlüsseldatei im Volume `orchestrator-data`) und nie wieder
angezeigt; die Tabelle zeigt nur „hinterlegt". Die Agentenliste zeigt danach `fw-01` beim
Agenten.

## 5. Externen Lauf durchspielen (Dev)

Auf Dev kann der Agent lokal aus dem Repo laufen (ohne Windows). Er braucht nur
Orchestrator-Adresse + Agent-Token; Adresse und Token der FortiGate kommen vom Quellsystem
(Schritt 4b). **Ohne hinterlegtes Token bricht ein Agent den Lauf ab** (keine Beispieldaten
im Kundennetz). Nur Beispieldaten testen: Gerät **ohne** Agent anlegen und zentral einlesen
(`automato collect … --execution inprocess`).

```bash
export AUTOMATO_ORCHESTRATOR_URL=http://localhost:8000 AUTOMATO_AGENT_TOKEN=<token aus Schritt 4>

# (a) Erreichbarkeit aller zugewiesenen Geräte prüfen (read-only)
uv run --package collectors automato-collector --check
# -> fw-01 (fortigate) check: OK - erreichbar: https://<fw>

# (b) Externen Lauf anlegen - per CLI im Container (funktioniert auch mit Login/OIDC,
#     kein Token noetig). execution external -> kein zentraler Worker, der Agent holt ihn.
docker compose exec orchestrator automato collect --platform fortigate \
  --tenant lab --source fw-01 --execution external
# -> run_id=<RUN_ID> status=queued ... Externer Lauf: wartet auf einen Agenten

# (c) Agent holt den Lauf per Pull (inkl. Zugangsdaten des Geräts) und liefert das
#     Ergebnis mit seinem Agent-Token ab - kein internes Token nötig.
uv run --package collectors automato-collector --poll
# -> "fortigate run <RUN_ID> (fw-01): completed"

# (d) Status prüfen
docker compose exec orchestrator automato status --run-id <RUN_ID>
```

Mit Login (`AUTOMATO_AUTH_MODE=oidc`) geht `curl` gegen `/jobs/collect` nur mit Bearer-Token;
die CLI im Container braucht keins.

### Windows-Agent im Dev

Paket aus `deploy/collector-agent/` installieren (`install.ps1`), dann
`C:\Automato\collector\agent.env` füllen (Vorlage `agent.env.example`):
`AUTOMATO_ORCHESTRATOR_URL=http://<dev-vm>:8000` (direkt, **nicht** über den Web-UI-Proxy —
der liefert `/internal` nicht aus) und `AUTOMATO_AGENT_TOKEN` – mehr nicht; die Geräte
kommen vom Orchestrator (Schritt 4b). TLS-/Zertifikatszeilen nur einkommentieren, wenn die
Dateien existieren. Die Station braucht Zugriff auf Port 8000 der Dev-VM und auf die FortiGate.

```powershell
cd C:\Automato\collector
.\run-agent.ps1 -Check        # alle zugewiesenen Geräte erreichbar? (Exit 0 = OK)
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
