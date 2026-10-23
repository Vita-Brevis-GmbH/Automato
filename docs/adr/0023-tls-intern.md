# ADR 0023 — TLS intern mit eigener CA

## Status

Entwurf (2026-10-23), umgesetzt in 0.49.0. Teil der R14-Massnahmen vor dem Produktivbetrieb;
schliesst ADR 0005 Nr. 3 („Verschlüsselung überall, auch intern“).

## Kontext

Zwischen den Containern lief bis 0.48 alles im Klartext: Orchestrator ↔ Postgres (die ganze
Topologie aller Kunden), Collector → Orchestrator (gelesene Konfigurationen),
Agent-Gateway/Proxy → Orchestrator bzw. Web-UI (API mit Token), Orchestrator/Collector ↔
Redis. Nach aussen waren nur der Proxy (Browser) und das Agent-Gateway (mTLS) verschlüsselt.
Entscheid des Anwenders (2026-10-23): TLS intern vor dem Produktivbetrieb.

## Entscheidung

- **Eigene CA** je Instanz, erzeugt mit `automato tls-init` (Python `cryptography`, EC P-256):
  CA 10 Jahre, Server-Zertifikate 825 Tage für `postgres`, `redis`, `orchestrator`, `web-ui`
  (Dienstname im Compose-Netz, `localhost`, `127.0.0.1`; Orchestrator und Web-UI zusätzlich
  die Adressen der VM). Kein öffentliches PKI nötig, keine Abhängigkeit nach aussen.
- **Ablage je Dienst** (`deploy/tls/pki/<dienst>`, gitignored): Jeder Container sieht nur sein
  Zertifikat, seinen Schlüssel und die CA; Clients (Migration, Collector, Proxy, Gateway) nur die
  CA (`client/`). Der CA-Schlüssel liegt in `ca/` und wird in keinen Container gelegt.
  Ausgestellt wird als root im Dienst `migrate` (`deploy/aufbau.sh tls-intern`), damit der
  Schlüssel des Orchestrators seinem Dienstbenutzer gehört; Postgres und Redis kopieren ihren
  Schlüssel beim Start selbst (`deploy/tls/*-start.sh`).
- **Ein Schalter** `AUTOMATO_INTERNAL_TLS=on` (in `.env`), überall gleich ausgewertet:
  - Postgres: `ssl=on`, `pg_hba` mit `hostssl` und `hostnossl … reject`; Socket im Container
    wie bisher (Healthcheck, `pg_dump` der Sicherung). Clients `sslmode=verify-full`.
  - Redis: nur TLS-Port, Clients `rediss://` mit `ssl_cert_reqs=required` (Celery
    `broker_use_ssl`, Verbindungsprüfung).
  - Orchestrator: uvicorn mit Zertifikat (`python -m app.serve`), Healthcheck gegen die CA.
  - Collector (zentral): `https://`/`rediss://` und die CA aus dem Schalter; Agenten nicht.
  - Web-UI (Vite): HTTPS; Proxy zum Orchestrator mit der CA (`secure`).
  - Proxy und Agent-Gateway (Caddy): Upstream per HTTPS gegen die CA
    (`import …/upstream-{$AUTOMATO_INTERNAL_TLS}.caddy`).
- **Opt-in:** Ohne Schalter bleibt alles wie bis 0.48 – ein Update bricht nichts, insbesondere
  keine Agenten, die direkt auf Port 8000 gehen. Betriebszustand, `pruefen` und die
  System-Seite warnen, solange er aus ist.
- **Prüfbar:** CI-Job `postgres` startet Postgres und Redis aus `docker-compose.yml` mit dem
  Schalter und prüft: TLS mit Prüfung von CA und Name ja, Klartext und fremde CA nein.

## Konsequenzen

- Agenten ohne Agent-Gateway brauchen danach `https://<VM>:8000` und die CA
  (`deploy/tls/pki/client/ca.crt`); produktiv gehen Agenten ohnehin über das Gateway.
- Die Web-UI direkt auf `:5173` zeigt die interne CA (Browser-Warnung); Anwender nutzen den
  Proxy (`:443`).
- Erneuern vor Ablauf: `deploy/aufbau.sh tls-intern --erneuern` (CA bleibt, Neustart der
  Container). Verlust der Ablage: neu ausstellen, Agenten ohne Gateway bekommen die neue CA.
- Offen: mTLS zwischen den Diensten (Client-Zertifikate) statt Token/Passwort, Rotation
  automatisch, Keycloak und NetBox (eigene Stacks) mit eigener TLS-Konfiguration.
