#!/usr/bin/env bash
# Bringt den Automato-Stack (core+collectors) hoch und verdrahtet ihn mit der
# bereits laufenden NetBox- und FortiGate-Lab-Umgebung. NUR fuer Labor/Test.
# Aus dem Repo-Root ausfuehren.
#
# Eingaben ueber Umgebungsvariablen:
#   NETBOX_TOKEN     (Pflicht) read-only Collector-Token aus source-of-truth/netbox/setup.sh
#   FORTIGATE_URL    (Pflicht) z. B. https://10.10.0.1
#   FORTIGATE_TOKEN  (Pflicht) API-Token des read-only FortiGate-Users (LAB.md)
#   NETBOX_PORT      (optional) Host-Port der NetBox-Instanz (Default 8080)
#
# Beispiel:
#   NETBOX_TOKEN=abc... FORTIGATE_URL=https://10.10.0.1 FORTIGATE_TOKEN=xyz... \
#     bash lab/up-automato.sh
set -euo pipefail

NETBOX_PORT="${NETBOX_PORT:-8080}"
: "${NETBOX_TOKEN:?NETBOX_TOKEN fehlt (read-only Collector-Token aus der NetBox-setup.sh)}"
: "${FORTIGATE_URL:?FORTIGATE_URL fehlt (z. B. https://10.10.0.1)}"
: "${FORTIGATE_TOKEN:?FORTIGATE_TOKEN fehlt (API-Token des read-only FortiGate-Users)}"

command -v docker >/dev/null 2>&1 || { echo "docker fehlt -> erst lab/install-prereqs.sh"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "docker compose v2 fehlt"; exit 1; }

# Internen Service-Token stabil halten (aus vorhandener .env uebernehmen), sonst neu.
INTERNAL_TOKEN="$(sed -n 's/^AUTOMATO_INTERNAL_API_TOKEN=//p' .env 2>/dev/null | head -n1)"
if [ -z "${INTERNAL_TOKEN}" ] || [ "${INTERNAL_TOKEN}" = "dev-only-token" ]; then
  INTERNAL_TOKEN="$(openssl rand -hex 32)"
fi

[ -f .env ] && cp .env .env.bak && echo "Vorhandene .env nach .env.bak gesichert."

cat > .env <<EOF
# Von lab/up-automato.sh erzeugt - NICHT committen. Lab/Test.
POSTGRES_PASSWORD=$(openssl rand -hex 16)

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

echo "== Stack bauen und starten =="
docker compose --profile core --profile collectors up -d --build

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

Durchstich ausloesen (Model 2, FortiGate -> NetBox-Matching):
  docker compose exec orchestrator automato lab --base-url ${FORTIGATE_URL}
  docker compose exec orchestrator automato status --run-id <aus-obiger-Ausgabe>
  curl -s http://localhost:8000/rules | python3 -m json.tool
EOF
