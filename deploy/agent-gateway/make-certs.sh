#!/usr/bin/env bash
# Zertifikate fuer das mTLS-Agent-Gateway (ablegen in pki/, gitignored):
#   ./make-certs.sh ca                 Agent-CA (einmalig) - fuer die Oberflaeche freigegeben
#   ./make-certs.sh server <host|ip>   Server-Zertifikat des Gateways (SAN = Host bzw. IP)
#   ./make-certs.sh oberflaeche [aus]  Agent-CA fuer den Orchestrator freigeben bzw. sperren
#   ./make-certs.sh agent <agent-name> Client-Zertifikat von Hand (ohne Oberflaeche)
# Normalfall (1.6.0, ADR 0052): Zertifikat, Schluessel und agent.env je Agent im Web-UI
# (Collector-Agenten -> Zugang). Dafuer liest der Orchestrator (uid 1001) agents-ca.key - nur
# diese Datei und agents-ca.crt; gateway.key und die Schluessel von Hand bleiben root.
set -euo pipefail
cd "$(dirname "$0")"
ORCH_UID=1001  # Benutzer im Orchestrator-Container (orchestrator/Dockerfile)
mkdir -p pki
cd pki
umask 077

oberflaeche() { # [aus] - Agent-CA fuer den Orchestrator lesbar machen bzw. wieder sperren
  [ -f agents-ca.key ] || { echo "pki/agents-ca.key fehlt - zuerst ./make-certs.sh ca" >&2; exit 1; }
  if [ "${1:-}" = "aus" ]; then
    chown 0:0 agents-ca.key 2>/dev/null || true
    chmod 600 agents-ca.key
    chmod 700 .
    echo "Agent-CA gesperrt - Zugang mit Zertifikat nur noch von Hand (./make-certs.sh agent)"
    return
  fi
  chown "$ORCH_UID" agents-ca.key 2>/dev/null ||
    echo "WARNUNG: Besitzer von agents-ca.key nicht gesetzt (als root ausfuehren: sudo)" >&2
  chmod 400 agents-ca.key
  chmod 644 agents-ca.crt
  chmod 711 .  # Orchestrator darf hinein, aber nicht auflisten; andere Schluessel bleiben root
  echo "Agent-CA freigegeben: Zugang mit Zertifikat im Web-UI (Collector-Agenten)."
  echo "Danach einmal: sudo docker compose up -d (Orchestrator mit der Agent-CA neu starten)"
}

new_key() { openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$1" 2>/dev/null; }

sign() { # <name> <cn> <extensions> <tage>
  openssl req -new -key "$1.key" -subj "/CN=$2/O=Automato" -out "$1.csr"
  openssl x509 -req -in "$1.csr" -CA agents-ca.crt -CAkey agents-ca.key -CAcreateserial \
    -out "$1.crt" -days "$4" -sha256 -extfile <(printf '%s\n' "$3") 2>/dev/null
  rm -f "$1.csr"
  chmod 644 "$1.crt"
}

case "${1:-}" in
  ca)
    [ -f agents-ca.key ] && { echo "agents-ca.key existiert bereits - nicht ueberschrieben" >&2; exit 1; }
    new_key agents-ca.key
    openssl req -x509 -new -key agents-ca.key -subj "/CN=Automato Agent CA/O=Automato" \
      -days 3650 -sha256 -out agents-ca.crt \
      -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
      -addext "keyUsage=critical,keyCertSign,cRLSign"
    chmod 644 agents-ca.crt
    echo "CA: pki/agents-ca.crt (Schluessel pki/agents-ca.key - mitsichern)"
    oberflaeche
    ;;
  oberflaeche)
    oberflaeche "${2:-}"
    ;;
  server)
    host="${2:?Host oder IP angeben}"
    if [[ "$host" =~ ^[0-9.]+$ || "$host" == *:* ]]; then san="IP:$host"; else san="DNS:$host"; fi
    new_key gateway.key
    sign gateway "$host" "subjectAltName=$san
extendedKeyUsage=serverAuth
keyUsage=critical,digitalSignature" 825
    echo "Server: pki/gateway.crt ($san)"
    ;;
  agent)
    name="${2:?Agent-Name angeben (wie im UI)}"
    [[ "$name" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "Agent-Name nur aus A-Z a-z 0-9 . _ -" >&2; exit 1; }
    new_key "agent-$name.key"
    sign "agent-$name" "$name" "extendedKeyUsage=clientAuth
keyUsage=critical,digitalSignature" 825
    echo "Agent: pki/agent-$name.crt + pki/agent-$name.key -> auf die Agent-Station kopieren"
    ;;
  *)
    sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
