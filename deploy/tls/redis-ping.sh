#!/bin/sh
# Healthcheck von Redis - mit TLS intern ueber TLS gegen die interne CA (0.49.0)
if [ "${AUTOMATO_INTERNAL_TLS:-off}" = "on" ]; then
  exec redis-cli --tls --cacert /etc/automato/tls/ca.crt -h localhost ping
fi
exec redis-cli ping
