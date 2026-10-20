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
2. **pinnt die NetBox-Version auf die 4.6er-Linie** (`NETBOX_VERSION`, Default
   `v4.6.10-5.0.2`) und startet den Stack, mappt NetBox auf `http://localhost:8000`
   (via `NETBOX_PORT` änderbar),
3. legt idempotent an:
   - **Admin-User** (UI-Login, Default `admin` / `admin1234!`) + Schreib-Token (Seeding),
   - **`automato-collector`** (kein Superuser, Gruppe mit `view_*`-Rechten) + einen
     **read-only Token** (`write_enabled=false`) — das ist der Least-Privilege-Zugang
     für den Collector (ADR 0005),
   - **`automato-sync`** (kein Superuser) + **Sync-Token** für den NetBox-Sync (ADR 0010):
     liest alles, schreibt aber nur die Objekttypen des Syncs (Tags, Tenants, Sites,
     Hersteller, Geräterollen/-typen, Geräte, Interfaces, VRFs, VLAN-Gruppen, VLANs,
     Prefixe, IP-Ranges, IP-Adressen) – siehe `sync-user.sh`.

> **Zu den Tokens:** Seit **NetBox 4.6** werden *neue* API-Tokens (v2) gehasht
> gespeichert (Header `Bearer nbt_<key>.<token>`); ein fester Token-Wert lässt sich
> dafür nicht mehr vorgeben (sonst `value too long for type character varying(12)`).
> Das Setup legt deshalb bewusst **Legacy-v1-Tokens** an (Klartext in der DB, Header
> `Authorization: Token <wert>`) — die NetBox weiterhin akzeptiert und die der
> Automato-NetBox-Adapter nutzt. Die NetBox-Version ist zusätzlich auf eine
> verifizierte 4.6er-Linie gepinnt (`NETBOX_VERSION`, Default `v4.6.10-5.0.2`).

Am Ende werden URL, Logins und beide Tokens ausgegeben.

### Automato anbinden

Im Web-UI unter **Mandanten & Standorte → Quellsysteme → + Quellsystem**:

| Feld | Wert |
|---|---|
| Plattform | NetBox (Referenzquelle) |
| Adresse | `http://host.docker.internal:<NETBOX_PORT>` (so erreichen Orchestrator und zentraler Worker den Host) |
| Rolle | *Anreicherung* (wenn die DDI führend ist), sonst *IPAM führend* |
| API-Token (lesen) | Collector-Token aus der `setup.sh`-Ausgabe |
| Schreib-Token | Sync-Token (nur für den NetBox-Sync; **nicht** den Admin-Token verwenden) |
| Verwaltet von | kein Agent (zentraler Worker) |
| TLS-Zertifikat prüfen | aus (Lab über HTTP) |

Nur Sync-Benutzer neu anlegen (bestehende NetBox, andere Tokens bleiben):

```bash
cd source-of-truth/netbox
./sync-user.sh          # gibt den neuen Sync-Token aus
```

> `setup.sh` erneut auszuführen erzeugt **neue** Admin-/Collector-/Sync-Tokens (ausser sie
> werden per `ADMIN_TOKEN`/`COLLECTOR_TOKEN`/`SYNC_TOKEN` mitgegeben) – danach die Tokens
> im Automato-Quellsystem nachtragen.

Älterer Weg (Fallback ohne Quellsystem, `.env`): `AUTOMATO_NETBOX_URL` +
`AUTOMATO_NETBOX_TOKEN=<collector-token>`.

### Lab-Daten seeden (optional, empfohlen für den Durchstich)

```bash
NETBOX_URL=http://localhost:8000 NETBOX_TOKEN=<admin-token> python3 seed_lab.py
```

Legt Zonen-Tags, eine Site, zwei Prefixe (mit Zone-Tag am Subnetz – so verortet
ADR 0004 die Zone) und drei IP-Adressen an. Richte die Adressobjekte auf der
FortiGate auf dieselben Netze/IPs aus, damit das Matching Treffer erzeugt.

### NetBox aus der FortiGate seeden (Bootstrap)

Wenn NetBox noch keine echten IPAM-Daten hat, kannst du die **FortiGate-Adressobjekte
als Ausgangsbasis** nach NetBox übernehmen – Netze werden zu Prefixen, Hosts zu
IP-Adressen (markiert mit Tag `automato-import`). Danach matcht der (rein lesende)
Collector die echten Regeln gegen diese Daten.

```bash
FORTIGATE_URL=https://<fortigate-ip> FORTIGATE_TOKEN=<readonly-key> \
NETBOX_URL=http://localhost:8080 NETBOX_TOKEN=<admin-schreib-token> \
  python3 import_from_fortigate.py
```

> **Wichtig:** Dieser Schritt **schreibt in NetBox** und nutzt daher den
> **Schreib-Token** (Admin) – nicht den read-only Collector-Token. Der FortiGate-Zugriff
> bleibt rein lesend. Es ist ein **einmaliger Bootstrap**: anschließend ist NetBox die
> Source of Truth und sollte dort gepflegt/kuratiert werden (die Firewall ist nur der
> Startpunkt). Ranges/FQDN/Geography werden übersprungen (nicht als IPAM-Objekt abbildbar).

Am einfachsten über den Bootstrap mit einem Flag (nutzt automatisch den frischen
Admin-Token, vor dem Collect-Lauf):

```bash
IMPORT_FORTIGATE=1 FORTIGATE_URL=https://<fortigate-ip> FORTIGATE_TOKEN=<key> \
  bash lab/bootstrap.sh
```

## Anpassen / Zurücksetzen

- Werte überschreiben: `NETBOX_PORT`, `ADMIN_USER`, `ADMIN_PASS`, `COLLECTOR_TOKEN`,
  `ADMIN_TOKEN`, `SYNC_TOKEN` als Umgebungsvariablen vor `./setup.sh`. **Admin-Passwort
  ändern** (`ADMIN_PASS=…`), sobald die NetBox echte Daten enthält.
- Stoppen: `cd netbox-docker && docker compose down`
- Komplett zurücksetzen (inkl. Daten): `cd netbox-docker && docker compose down -v`

## Fehlerbehebung

**`value too long for type character varying(12)` bei der Token-Anlage**

Alte `setup.sh`, die noch versucht, einen festen Token-Wert in das (ab NetBox 4.6
gehashte, kurze) `key`-Feld zu schreiben. Behoben: das Setup legt jetzt Legacy-v1-
Tokens an. **Repo aktualisieren und erneut ausführen**, ein DB-Reset ist dafür nicht
nötig:

```bash
git pull
bash lab/bootstrap.sh
```

Nur falls die DB von einer *höheren* NetBox-Version stammt (Schema lässt sich nicht
downgraden), einmalig mit Reset:

```bash
NETBOX_RESET=1 bash lab/bootstrap.sh
```

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
