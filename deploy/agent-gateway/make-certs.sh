#!/usr/bin/env bash
# Zertifikate fuer das mTLS-Agent-Gateway (ablegen in pki/, gitignored):
#   ./make-certs.sh ca                 Agent-CA (einmalig; agents-ca.key sicher verwahren)
#   ./make-certs.sh server <host|ip>   Server-Zertifikat des Gateways (SAN = Host bzw. IP)
#   ./make-certs.sh agent <agent-name> Client-Zertifikat je Agent (CN = Agent-Name wie im UI)
# Die Agenten pinnen agents-ca.crt (AUTOMATO_ORCHESTRATOR_CA_BUNDLE) und praesentieren ihr
# Client-Zertifikat (AUTOMATO_CLIENT_CERT/_KEY). Schluessel verlassen pki/ nur zum Agenten.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p pki
chmod 700 pki
cd pki
umask 077

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
    echo "CA: pki/agents-ca.crt (Schluessel pki/agents-ca.key offline/sicher verwahren)"
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
    sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
