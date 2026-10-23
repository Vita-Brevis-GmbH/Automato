#!/bin/sh
# Postgres mit TLS intern (0.49.0, ADR 0023). AUTOMATO_INTERNAL_TLS=on: ueber das Netz nur
# noch verschluesselt (hostssl, pg_hba-tls.conf); lokal im Container (Healthcheck, Sicherung
# mit pg_dump) wie bisher. Zertifikat und Schluessel aus /etc/automato/tls (deploy/tls/pki/
# postgres, `deploy/aufbau.sh tls-intern`); Postgres verlangt einen Schluessel, der ihm gehoert.
set -eu
if [ "${AUTOMATO_INTERNAL_TLS:-off}" = "on" ]; then
  src=/etc/automato/tls
  dir=/var/lib/postgresql/tls
  for f in server.crt server.key; do
    [ -r "$src/$f" ] || { echo "TLS intern: $src/$f fehlt - deploy/aufbau.sh tls-intern" >&2; exit 1; }
  done
  mkdir -p "$dir"
  install -o postgres -g postgres -m 0644 "$src/server.crt" "$dir/server.crt"
  install -o postgres -g postgres -m 0600 "$src/server.key" "$dir/server.key"
  exec docker-entrypoint.sh postgres \
    -c ssl=on -c ssl_cert_file="$dir/server.crt" -c ssl_key_file="$dir/server.key" \
    -c ssl_min_protocol_version=TLSv1.2 -c hba_file=/etc/automato/pg_hba-tls.conf "$@"
fi
exec docker-entrypoint.sh postgres "$@"
