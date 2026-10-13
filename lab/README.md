# Lab-Runbook: NetBox + Automato auf einer VM (Model 2, UC1-Durchstich)

Bringt auf einer **nackten Linux-VM** (Debian/Ubuntu) die komplette Model-2-Kette
hoch: NetBox als Source of Truth, den Automato-Stack (Orchestrator, Collector,
Web-UI, Postgres, Redis) und löst einen echten, **rein lesenden** FortiGate-Abruf
mit NetBox-Matching aus. **Nur für Labor/Test, nicht produktiv.**

Voraussetzung nebenbei: eine erreichbare **FortiGate-VM** mit einem read-only
API-User (siehe `collectors/fortigate/LAB.md`).

```
FortiGate-VM ──(read-only REST)──▶ Collector ─┐
                                              ├─▶ Orchestrator ─▶ NetBox (Matching)
NetBox-Stack (Host :8080) ◀───────────────────┘        (Rule & Service Store, /rules)
```

## Schnellstart (ein Kommando)

Für die eilige Variante: Repo holen, einmal die Grundpakete installieren, dann
**ein** Skript, das NetBox + Automato hochfährt, verdrahtet und einen ersten Lauf
auslöst. Ohne FortiGate-Creds läuft es gegen **Beispieldaten** (gut zum Testen des
Stacks); mit Creds macht es den echten Abruf.

```bash
# 1. Repo holen
sudo apt-get update && sudo apt-get install -y git      # falls git noch fehlt
git clone <dieses-repo> automato && cd automato
git checkout claude/projekt-roadmap-planung-0mk5v0

# 2. Grundpakete (Docker etc.) - einmalig, braucht root; danach neu einloggen
sudo bash lab/install-prereqs.sh
newgrp docker        # oder aus-/einloggen

# 3a. Stack-Test mit Beispieldaten (noch ohne FortiGate):
bash lab/bootstrap.sh

# 3b. ... oder gleich mit echtem FortiGate (read-only API-User, siehe LAB.md):
FORTIGATE_URL=https://<fortigate-ip> FORTIGATE_TOKEN=<api-token> bash lab/bootstrap.sh
```

Am Ende zeigt das Skript NetBox-/Orchestrator-URLs und den Status des ersten Laufs.
`bootstrap.sh` ist **idempotent** — du kannst es gefahrlos erneut ausführen (z. B.
später mit FortiGate-Creds). Die Einzelschritte dahinter stehen unten, falls du etwas
gezielt nachvollziehen oder anpassen willst.

---

## 0. Repo auf die VM holen

```bash
git clone <dieses-repo> automato && cd automato
git checkout claude/projekt-roadmap-planung-0mk5v0   # aktueller Entwicklungsstand
```

## 1. Grundinstallation (Docker etc.)

```bash
sudo bash lab/install-prereqs.sh
newgrp docker            # oder neu einloggen, damit docker ohne sudo geht
```

Installiert Docker Engine + Compose-Plugin, `git/curl/openssl/python3`. Idempotent.

## 2. NetBox hochfahren (Port 8080) + Lab-Daten

NetBox läuft **auf Port 8080** — Port 8000 gehört dem Orchestrator.

```bash
cd source-of-truth/netbox
NETBOX_PORT=8080 ./setup.sh
```

Am Ende gibt das Skript URL, Logins und **zwei Tokens** aus. Wichtig:

- **Collector-Token (read-only)** → gleich für Automato (Schritt 3),
- **Admin-Token (Schreiben)** → nur fürs Seeding jetzt gleich:

```bash
# noch in source-of-truth/netbox/ :
NETBOX_URL=http://localhost:8080 NETBOX_TOKEN=<admin-token> python3 seed_lab.py
cd ../..                          # zurück ins Repo-Root
```

Das Seeding legt Zonen-Tags, Prefixe (Zone am Subnetz) und IPs an, die zu den
FortiGate-Beispielobjekten passen (`10.10.10.0/24`, `10.10.20.0/24`, `10.10.10.5`).
Richte die FortiGate-Adressobjekte auf **dieselben** Netze/IPs aus, sonst gibt es
keine Treffer.

## 3. Automato-Stack hochfahren + verdrahten

Ein Skript schreibt die `.env` (frischer interner Token, NetBox- und FortiGate-Zugang)
und startet den Stack:

```bash
NETBOX_TOKEN=<collector-token-read-only> \
FORTIGATE_URL=https://<fortigate-lan-ip> \
FORTIGATE_TOKEN=<fortigate-api-token> \
  bash lab/up-automato.sh
```

Das Skript:
1. sichert eine vorhandene `.env` nach `.env.bak`,
2. erzeugt eine `.env` (interner Service-Token via `openssl`, NetBox via
   `host.docker.internal:8080`, FortiGate per LAN-IP, TLS-Verify aus — Lab-Zertifikate),
3. `docker compose --profile core --profile collectors up -d --build`,
4. wartet auf `http://localhost:8000/health`.

> **Netzwerk:** Aus den Containern erreicht der Orchestrator NetBox über
> `host.docker.internal` (in der compose als `host-gateway` gesetzt). Die FortiGate
> ist eine LAN-IP und direkt erreichbar. Falls `host.docker.internal` auf deiner
> VM nicht auflöst, in der `.env` stattdessen die LAN-IP der VM eintragen:
> `AUTOMATO_NETBOX_URL=http://<vm-lan-ip>:8080`.

## 4. Durchstich auslösen (rein lesend)

```bash
# Legt Mandant (NetBox/AD) + Quellsystem (FortiGate) an und startet einen Lauf:
docker compose exec orchestrator automato lab --base-url https://<fortigate-lan-ip>

# Status des Laufs (completed/failed, Regelzahl, Meldung):
docker compose exec orchestrator automato status --run-id <run-id-aus-obiger-Ausgabe>

# Ergebnis ansehen:
curl -s http://localhost:8000/rules | python3 -m json.tool
```

Der Collector liest die FortiGate-Policies per REST, löst Adress-/Service-Objekte
zu Werten auf und liefert sie an den Orchestrator; dort läuft das Matching gegen
NetBox. Nicht auflösbare/abweichende Objekte erscheinen als **Findings**.

### CLI-Überblick

```
automato ensure-tenant --name <n> --ipam netbox --dns active_directory
automato ensure-source --name <n> --platform fortigate --base-url <url>
automato collect --platform fortigate --tenant <name> --source <name>
automato status  --run-id <id>
automato lab     # Kurzform: ensure-tenant + ensure-source + collect (Model 2)
```

## Betrieb / Zurücksetzen

| Aktion | Befehl |
|---|---|
| Logs | `docker compose logs -f orchestrator collector` |
| Stack stoppen | `docker compose --profile core --profile collectors down` |
| Stack + Daten weg | `docker compose --profile core --profile collectors down -v` |
| NetBox stoppen | `cd source-of-truth/netbox/netbox-docker && docker compose down` |
| NetBox + Daten weg | `cd source-of-truth/netbox/netbox-docker && docker compose down -v` |

## Sicherheitshinweise (Lab)

- Alle Zielsystem-Zugänge sind **read-only** (Least Privilege, ADR 0005): NetBox-Token
  `write_enabled=false`, FortiGate-API-User nur mit Leserechten.
- `.env` enthält Secrets und wird **nie** committet (`.gitignore` + gitleaks).
- `verify_tls=false` ist nur fürs Lab mit selbstsignierten Zertifikaten gedacht.
- `AUTOMATO_ENVIRONMENT=dev` erlaubt den Default-Token nur lokal; ab `staging/prod`
  erzwingt der Fail-closed-Check einen echten Token (ADR 0005).
