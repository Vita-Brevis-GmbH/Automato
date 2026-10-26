#!/usr/bin/env bash
# Bringt den Automato-Stack (core+collectors) hoch und verdrahtet ihn mit der
# bereits laufenden NetBox- und (optional) FortiGate-Lab-Umgebung. NUR Labor/Test.
# Aus dem Repo-Root ausfuehren.
#
# Eingaben ueber Umgebungsvariablen:
#   NETBOX_TOKEN     (Pflicht) read-only Collector-Token aus source-of-truth/netbox/setup.sh
#   FORTIGATE_URL    (optional) z. B. https://10.10.0.1 - leer -> Collector liest Beispieldaten
#   FORTIGATE_TOKEN  (optional) API-Token des read-only FortiGate-Users (LAB.md)
#   NETBOX_PORT      (optional) Host-Port der NetBox-Instanz (Default 8080)
#
# Beispiel (mit echtem FortiGate):
#   NETBOX_TOKEN=abc... FORTIGATE_URL=https://10.10.0.1 FORTIGATE_TOKEN=xyz... \
#     bash lab/up-automato.sh
set -euo pipefail

[ -f docker-compose.yml ] || { echo "Bitte aus dem Repo-Root ausfuehren."; exit 1; }
NETBOX_PORT="${NETBOX_PORT:-8080}"
FORTIGATE_URL="${FORTIGATE_URL:-}"
FORTIGATE_TOKEN="${FORTIGATE_TOKEN:-}"
: "${NETBOX_TOKEN:?NETBOX_TOKEN fehlt (read-only Collector-Token aus der NetBox-setup.sh)}"
if [ -z "$FORTIGATE_URL" ] || [ -z "$FORTIGATE_TOKEN" ]; then
  echo "Hinweis: ohne FORTIGATE_URL/FORTIGATE_TOKEN liest der Collector Beispieldaten."
fi

command -v docker >/dev/null 2>&1 || { echo "docker fehlt -> erst 'sudo bash lab/install-prereqs.sh'"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "docker compose v2 fehlt"; exit 1; }
docker info >/dev/null 2>&1 || {
  echo "Docker-Daemon nicht erreichbar. Nach install-prereqs neu einloggen oder 'newgrp docker'."; exit 1
}

# Internen Service-Token stabil halten (aus vorhandener .env uebernehmen), sonst neu.
# Guard: ohne vorhandene .env wuerde sed unter 'set -euo pipefail' das Skript
# lautlos beenden - deshalb nur lesen, wenn die Datei existiert.
INTERNAL_TOKEN=""
PG_PASSWORD=""
if [ -f .env ]; then
  INTERNAL_TOKEN="$(sed -n 's/^AUTOMATO_INTERNAL_API_TOKEN=//p' .env | head -n1 || true)"
  PG_PASSWORD="$(sed -n 's/^POSTGRES_PASSWORD=//p' .env | head -n1 || true)"
fi
if [ -z "${INTERNAL_TOKEN}" ] || [ "${INTERNAL_TOKEN}" = "dev-only-token" ]; then
  INTERNAL_TOKEN="$(openssl rand -hex 32)"
fi
# POSTGRES_PASSWORD MUSS ueber Laeufe stabil bleiben: Postgres setzt es nur bei der
# Erst-Initialisierung des Volumes; ein neuer Wert in .env wuerde den Orchestrator
# mit falschem Passwort verbinden lassen (unhealthy). Nur neu, wenn noch keiner da.
[ -n "${PG_PASSWORD}" ] || PG_PASSWORD="$(openssl rand -hex 16)"

HAD_ENV=0
if [ -f .env ]; then
  cp .env .env.bak && HAD_ENV=1 && echo "Vorhandene .env nach .env.bak gesichert."
fi

cat > .env <<EOF
# Von lab/up-automato.sh erzeugt - NICHT committen. Lab/Test.
POSTGRES_PASSWORD=${PG_PASSWORD}

AUTOMATO_ENVIRONMENT=dev
AUTOMATO_INTERNAL_API_TOKEN=${INTERNAL_TOKEN}
AUTOMATO_CORS_ORIGINS=["http://localhost:5173"]

AUTOMATO_NETBOX_URL=http://host.docker.internal:${NETBOX_PORT}
AUTOMATO_NETBOX_TOKEN=${NETBOX_TOKEN}
AUTOMATO_NETBOX_VERIFY_TLS=false

AUTOMATO_FORTIGATE_URL=${FORTIGATE_URL}
AUTOMATO_FORTIGATE_TOKEN=${FORTIGATE_TOKEN}
AUTOMATO_FORTIGATE_VERIFY_TLS=false
EOF
echo ".env geschrieben."

# Eigene Eintraege aus der alten .env behalten (z. B. AUTOMATO_AUTH_MODE/AUTOMATO_OIDC_*,
# AUTOMATO_UI_HOST): alles, was dieses Skript nicht selbst setzt, wird uebernommen.
if [ "$HAD_ENV" = "1" ]; then
  awk -F= 'NR == FNR { if ($0 ~ /^[A-Za-z_][A-Za-z0-9_]*=/) seen[$1] = 1; next }
           $0 ~ /^[A-Za-z_][A-Za-z0-9_]*=/ && !($1 in seen) { print }' .env .env.bak > .env.extra
  if [ -s .env.extra ]; then
    { echo; echo "# Aus der vorherigen .env uebernommen"; cat .env.extra; } >> .env
    echo "Uebernommen: $(cut -d= -f1 .env.extra | tr '\n' ' ')"
  fi
  rm -f .env.extra
fi

# TLS-Proxy fuer die Web-UI mitstarten, wenn eine UI-Adresse konfiguriert ist.
PROFILES=(--profile core --profile collectors)
if grep -q '^AUTOMATO_UI_HOST=.' .env; then
  PROFILES+=(--profile proxy)
fi

# Optional: Automato-Volumes vorher loeschen (z. B. nach einem Postgres-Passwort-
# Mismatch). Harmlos im Lab - die Regeln werden beim Collect-Lauf neu befuellt.
if [ "${AUTOMATO_RESET:-0}" = "1" ]; then
  echo "AUTOMATO_RESET=1 -> loesche Automato-Volumes ..."
  docker compose --profile core --profile collectors down -v --remove-orphans 2>/dev/null || true
fi

echo "== Stack bauen und starten =="
docker compose "${PROFILES[@]}" up -d --build

echo "Warte auf Orchestrator-Health unter http://localhost:8000/health ..."
ok=""
for _ in $(seq 1 60); do
  if curl -fsS "http://localhost:8000/health" >/dev/null 2>&1; then ok=1; break; fi
  sleep 3
done
[ -n "$ok" ] || { echo "FEHLER: Orchestrator nicht rechtzeitig healthy (docker compose logs orchestrator)"; exit 1; }

cat <<EOF

== Automato-Stack laeuft ==
Orchestrator:  http://localhost:8000  (Health: /health, Regeln: /rules)
Web-UI (Dev):  http://localhost:5173
Web-UI (TLS):  https://$(sed -n 's/^AUTOMATO_UI_HOST=//p' .env | head -n1)  (nur mit AUTOMATO_UI_HOST)

Durchstich ausloesen (Model 2, FortiGate -> NetBox-Matching):
  docker compose exec orchestrator automato lab --base-url ${FORTIGATE_URL}
  docker compose exec orchestrator automato status --run-id <aus-obiger-Ausgabe>
  curl -s http://localhost:8000/rules | python3 -m json.tool
EOF
