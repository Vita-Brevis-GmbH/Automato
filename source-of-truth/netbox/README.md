# NetBox-Lab (Source of Truth, Model 2)

Bootstrapping einer NetBox-Instanz als IPAM/SoT für den UC1-Durchstich (M7).
**Nur für Labor/Test** – nicht produktiv.

## Voraussetzungen

- Docker + Docker Compose v2, `git`, `curl`, `openssl`
- Für das Seeding zusätzlich: Python 3 + `requests` (`pip install requests`)

## Verwendung

```bash
cd source-of-truth/netbox
./setup.sh
```

Das Skript:

1. holt `netbox-docker` (release-Branch) nach `./netbox-docker/` (nicht eingecheckt),
2. startet den Stack und mappt NetBox auf `http://localhost:8000` (via `NETBOX_PORT` änderbar),
3. legt idempotent an:
   - **Admin-User** (UI-Login, Default `admin` / `admin1234!`) + Schreib-Token (Seeding),
   - **`automato-collector`** (kein Superuser, Gruppe mit `view_*`-Rechten) + einen
     **read-only Token** (`write_enabled=false`) — das ist der Least-Privilege-Zugang
     für den Collector (ADR 0005).

Am Ende werden URL, Logins und beide Tokens ausgegeben.

### Automato anbinden

Den **read-only** Collector-Token in die Automato-`.env` des Model-2-Mandanten:

```
AUTOMATO_NETBOX_URL=http://localhost:8000
AUTOMATO_NETBOX_TOKEN=<collector-token aus der setup.sh-Ausgabe>
```

### Lab-Daten seeden (optional, empfohlen für den Durchstich)

```bash
NETBOX_URL=http://localhost:8000 NETBOX_TOKEN=<admin-token> python3 seed_lab.py
```

Legt Zonen-Tags, eine Site, zwei Prefixe (mit Zone-Tag am Subnetz – so verortet
ADR 0004 die Zone) und drei IP-Adressen an. Richte die Adressobjekte auf der
FortiGate auf dieselben Netze/IPs aus, damit das Matching Treffer erzeugt.

## Anpassen / Zurücksetzen

- Werte überschreiben: `NETBOX_PORT`, `ADMIN_USER`, `ADMIN_PASS`, `COLLECTOR_TOKEN`,
  `ADMIN_TOKEN` als Umgebungsvariablen vor `./setup.sh`.
- Stoppen: `cd netbox-docker && docker compose down`
- Komplett zurücksetzen (inkl. Daten): `cd netbox-docker && docker compose down -v`

## Fehlerbehebung

**`dependency failed to start: container netbox-docker-netbox-1 is unhealthy`**

Der `netbox`-Container wurde nicht rechtzeitig „healthy", darum brechen die
abhängigen Dienste (`netbox-worker`, `netbox-housekeeping`) ab. Fast immer ist es
ein **langsamer Erststart** (DB-Migrationen + gunicorn) oder **zu wenig RAM**.
Das aktuelle `setup.sh` fängt das ab (relaxter Healthcheck, startet zuerst nur
`netbox`), also am einfachsten neu ausführen: `NETBOX_PORT=8080 ./setup.sh`.

Diagnose (im `netbox-docker/`-Verzeichnis):

```bash
cd source-of-truth/netbox/netbox-docker
docker compose ps                       # Zustand aller Container
docker compose logs --tail=80 netbox    # echte Ursache
free -h                                 # RAM: der NetBox-Stack braucht ~3-4 GB
```

- **Log zeigt Migrationen/„Starting gunicorn"** → nur langsam. Warten, dann
  `docker compose up -d`. Für UC1 (Lesen über die REST-API) genügt der
  `netbox`-Container; `worker`/`housekeeping` sind dafür nicht nötig.
- **RAM knapp / OOM-Kill im Log** → VM mehr Speicher geben (≥ 4 GB empfohlen).
- **Echter Fehler im Log** (DB-Verbindung, Konfig) → Meldung zurückmelden.

Sobald `curl -fsS http://localhost:8080/login/` antwortet, ist die API bereit –
auch wenn `worker`/`housekeeping` noch nicht healthy sind.

## Hinweise

- Die Token-Anlage nutzt `from users.models import Token`. Sollte sich das App-Layout
  in einer künftigen NetBox-Version ändern, den Import im `setup.sh`-Shell-Block anpassen.
- Das Skript ist gegen `bash -n`/Syntax geprüft, aber der vollständige Stack-Lauf
  gehört auf deinen Lab-Host (hier ist kein Docker-Daemon verfügbar).
