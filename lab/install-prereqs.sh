#!/usr/bin/env bash
# Grundinstallation fuer eine nackte Lab-VM (Debian/Ubuntu): Docker Engine +
# Compose-Plugin + git/curl/openssl/python3. NUR fuer Labor/Test.
# Idempotent - mehrfaches Ausfuehren ist ok.
#
# Verwendung (auf der VM):
#   sudo bash lab/install-prereqs.sh
#   # danach neu einloggen (Docker-Gruppe) oder: newgrp docker
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Bitte mit sudo/root ausfuehren: sudo bash $0" >&2
  exit 1
fi

# Nutzer, der spaeter docker ohne sudo nutzen soll (der aufrufende sudo-User)
TARGET_USER="${SUDO_USER:-${USER:-root}}"

if ! command -v apt-get >/dev/null 2>&1; then
  cat >&2 <<'EOF'
Dieses Skript automatisiert nur Debian/Ubuntu (apt).
Auf anderer Distro bitte manuell installieren:
  - Docker Engine + Compose-Plugin (https://docs.docker.com/engine/install/)
  - git, curl, openssl, python3 + python3-requests
EOF
  exit 1
fi

echo "== Basis-Pakete =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y git curl openssl ca-certificates python3 python3-requests

if ! command -v docker >/dev/null 2>&1; then
  echo "== Docker Engine (offizielles Convenience-Skript) =="
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  sh /tmp/get-docker.sh
  rm -f /tmp/get-docker.sh
else
  echo "== Docker bereits installiert -> uebersprungen =="
fi

# Compose v2 sicherstellen (get.docker.com bringt das Plugin i. d. R. mit)
if ! docker compose version >/dev/null 2>&1; then
  apt-get install -y docker-compose-plugin
fi

systemctl enable --now docker

# docker ohne sudo fuer den Lab-User
if [ "$TARGET_USER" != "root" ]; then
  usermod -aG docker "$TARGET_USER"
  echo "Nutzer '$TARGET_USER' zur docker-Gruppe hinzugefuegt (neu einloggen noetig)."
fi

echo
echo "== Fertig =="
docker --version
docker compose version
echo "Naechster Schritt: lab/README.md (NetBox + Automato hochfahren)."
