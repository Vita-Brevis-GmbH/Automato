#!/bin/sh
# Redis mit TLS intern (0.49.0, ADR 0023). AUTOMATO_INTERNAL_TLS=on: Port 6379 nur noch mit TLS
# (kein Klartext-Port), Zertifikat und Schluessel aus /etc/automato/tls (deploy/tls/pki/redis).
set -eu
if [ "${AUTOMATO_INTERNAL_TLS:-off}" = "on" ]; then
  src=/etc/automato/tls
  dir=/tmp/tls
  for f in server.crt server.key ca.crt; do
    [ -r "$src/$f" ] || { echo "TLS intern: $src/$f fehlt - deploy/aufbau.sh tls-intern" >&2; exit 1; }
  done
  mkdir -p "$dir"
  install -o redis -g redis -m 0644 "$src/server.crt" "$dir/server.crt"
  install -o redis -g redis -m 0600 "$src/server.key" "$dir/server.key"
  exec docker-entrypoint.sh redis-server --port 0 --tls-port 6379 \
    --tls-cert-file "$dir/server.crt" --tls-key-file "$dir/server.key" \
    --tls-ca-cert-file "$src/ca.crt" --tls-auth-clients no --tls-protocols "TLSv1.2 TLSv1.3" "$@"
fi
exec docker-entrypoint.sh redis-server "$@"
