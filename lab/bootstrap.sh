#!/usr/bin/env bash
# End-to-End Lab-Installation auf einer frischen VM: NetBox (Source of Truth) +
# Automato-Stack, verdrahtet und mit einem ersten Collect-Lauf. NUR Labor/Test.
#
# Voraussetzung: einmalig 'sudo bash lab/install-prereqs.sh' und neu einloggen
# (Docker ohne sudo). Danach aus dem Repo-Root:
#   bash lab/bootstrap.sh                                   # Stack-Test mit Beispieldaten
#   FORTIGATE_URL=https://10.0.0.1 FORTIGATE_TOKEN=xxxx bash lab/bootstrap.sh   # echtes FortiGate
#
# Idempotent: mehrfaches Ausfuehren ist ok (NetBox-Setup/Seed/CLI sind idempotent).
set -euo pipefail

[ -f docker-compose.yml ] || { echo "Bitte aus dem Repo-Root (Automato/) ausfuehren."; exit 1; }

NETBOX_PORT="${NETBOX_PORT:-8080}"
NETBOX_RESET="${NETBOX_RESET:-0}"
FORTIGATE_URL="${FORTIGATE_URL:-}"
FORTIGATE_TOKEN="${FORTIGATE_TOKEN:-}"
# Tokens immer frisch erzeugen (ephemere Lab-Tokens). Bewusst KEINE Env-Vererbung,
# damit versehentlich gesetzte COLLECTOR_TOKEN/ADMIN_TOKEN (oder ein stray Export)
# die NetBox-Tokens nicht ueberschreiben.
COLLECTOR_TOKEN="$(openssl rand -hex 20)"
ADMIN_TOKEN="$(openssl rand -hex 20)"

# -- Preflight ----------------------------------------------------------------
command -v docker >/dev/null 2>&1 || { echo "docker fehlt -> erst 'sudo bash lab/install-prereqs.sh'"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "docker compose v2 fehlt"; exit 1; }
docker info >/dev/null 2>&1 || {
  echo "Docker-Daemon nicht erreichbar. Nach install-prereqs neu einloggen oder 'newgrp docker'."; exit 1
}
command -v python3 >/dev/null 2>&1 || { echo "python3 fehlt (install-prereqs)"; exit 1; }
python3 -c "import requests" 2>/dev/null || { echo "python3-requests fehlt (install-prereqs)"; exit 1; }

mem_gb=$(awk '/MemTotal/ {printf "%.1f", $2/1024/1024}' /proc/meminfo 2>/dev/null || echo "?")
echo "VM-RAM: ${mem_gb} GB (NetBox-Stack braucht ~3-4 GB)."

# -- 1/4 NetBox ---------------------------------------------------------------
echo; echo "== 1/4 NetBox hochfahren (Port ${NETBOX_PORT}) =="
( cd source-of-truth/netbox \
  && NETBOX_PORT="$NETBOX_PORT" NETBOX_RESET="$NETBOX_RESET" \
     COLLECTOR_TOKEN="$COLLECTOR_TOKEN" ADMIN_TOKEN="$ADMIN_TOKEN" ./setup.sh )

# -- 2/4 Seed -----------------------------------------------------------------
echo; echo "== 2/4 NetBox mit Lab-Daten seeden =="
( cd source-of-truth/netbox \
  && NETBOX_URL="http://localhost:${NETBOX_PORT}" NETBOX_TOKEN="$ADMIN_TOKEN" python3 seed_lab.py )

# -- 3/4 Automato-Stack -------------------------------------------------------
echo; echo "== 3/4 Automato-Stack hochfahren =="
NETBOX_TOKEN="$COLLECTOR_TOKEN" NETBOX_PORT="$NETBOX_PORT" \
  FORTIGATE_URL="$FORTIGATE_URL" FORTIGATE_TOKEN="$FORTIGATE_TOKEN" \
  AUTOMATO_RESET="${AUTOMATO_RESET:-0}" \
  bash lab/up-automato.sh

# -- 4/4 Erster Lauf ----------------------------------------------------------
echo; echo "== 4/4 Ersten Collect-Lauf ausloesen =="
out="$(docker compose exec -T orchestrator automato lab --base-url "${FORTIGATE_URL:-beispieldaten}" || true)"
echo "$out"
run_id="$(printf '%s\n' "$out" | sed -n 's/.*run_id=\([^ ]*\).*/\1/p' | head -n1 || true)"

if [ -n "$run_id" ]; then
  echo "Warte auf Ergebnis (run_id=${run_id}) ..."
  for _ in $(seq 1 20); do
    st="$(docker compose exec -T orchestrator automato status --run-id "$run_id" || true)"
    case "$st" in
      *status=completed*|*status=failed*) echo "$st"; break ;;
      *) sleep 3 ;;
    esac
  done
fi

cat <<EOF

================ Automato-Lab bereit ================
NetBox:        http://localhost:${NETBOX_PORT}/      (UI-Login: admin / admin1234!)
Orchestrator:  http://localhost:8000/health , http://localhost:8000/rules
Web-UI (Dev):  http://localhost:5173

Gelesene Regeln ansehen:
  curl -s http://localhost:8000/rules | python3 -m json.tool

Erneut auslesen (nach FortiGate-Aenderungen):
  docker compose exec orchestrator automato collect --platform fortigate --tenant lab --source fw-01
EOF

if [ -z "$FORTIGATE_URL" ] || [ -z "$FORTIGATE_TOKEN" ]; then
  cat <<EOF

Hinweis: Dieser Lauf nutzte BEISPIELDATEN (kein FortiGate hinterlegt).
Fuer den echten Abruf erneut mit Creds starten:
  FORTIGATE_URL=https://<fortigate-ip> FORTIGATE_TOKEN=<api-token> bash lab/bootstrap.sh
EOF
fi
