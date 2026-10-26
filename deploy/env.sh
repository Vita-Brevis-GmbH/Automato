# Betriebs-Einstellungen aus der .env des Repos (0.42.0) - fuer update.sh, aufbau.sh und die
# Sicherung. Einmal in .env eingetragen, gelten sie bei jedem Aufruf; ein Update vergisst keine
# Angabe (Muster: Magisters plattform-aufbau.sh). Eine Umgebungsvariable geht vor.
#
# Nur bekannte Schluessel, und `read` statt `source`: die .env wird nicht ausgefuehrt.
# Einbinden mit `. deploy/env.sh` aus dem Repo-Verzeichnis.

AUTOMATO_ENV_KEYS="AUTOMATO_BACKUP_AGE_RECIPIENT AUTOMATO_BACKUP_AGE_IDENTITY AUTOMATO_BACKUP_DIR \
AUTOMATO_BACKUP_KEEP_DAYS AUTOMATO_BACKUP_PLAIN AUTOMATO_COMPOSE_PROFILES AUTOMATO_NETBOX_DIR \
AUTOMATO_DOCKER_UPDATE AUTOMATO_PULL_IMAGES AUTOMATO_DB_USER AUTOMATO_INTERNAL_TLS"

ENV_DATEI=${AUTOMATO_ENV_FILE:-.env}  # andere Datei nur fuer Tests

env_aus_datei() {  # $1 = Datei (Standard .env)
  local datei="${1:-$ENV_DATEI}" schluessel wert
  [ -f "$datei" ] || return 0
  while IFS='=' read -r schluessel wert; do
    case " $AUTOMATO_ENV_KEYS " in
      *" $schluessel "*) ;;
      *) continue ;;
    esac
    [ -z "${!schluessel:-}" ] || continue  # Umgebung geht vor
    wert="${wert%$'\r'}"
    wert="${wert#\"}"; wert="${wert%\"}"
    wert="${wert#\'}"; wert="${wert%\'}"
    export "$schluessel=$wert"
  done < <(grep -E '^AUTOMATO_[A-Z_]+=' "$datei" || true)
}

setze_env() {  # $1 = Schluessel, $2 = Wert, $3 = Datei (Standard .env): ersetzen bzw. anhaengen
  local schluessel="$1" wert="$2" datei="${3:-$ENV_DATEI}"
  touch "$datei"
  if grep -qE "^$schluessel=" "$datei"; then
    local tmp
    tmp=$(mktemp)
    awk -v k="$schluessel" -v v="$wert" 'BEGIN{FS=OFS="="} $1==k {print k "=" v; next} {print}' \
      "$datei" >"$tmp" && cat "$tmp" >"$datei" && rm -f "$tmp"
  else
    printf '%s=%s\n' "$schluessel" "$wert" >>"$datei"
  fi
}

env_aus_datei

# TLS intern (0.49.0): Zertifikate je Dienst (gitignored); der Orchestrator antwortet dann per
# HTTPS - Health-Abfrage auf der VM gegen die interne CA
TLS_PKI=${AUTOMATO_TLS_PKI:-deploy/tls/pki}
standard_health_url() {
  if [ "${AUTOMATO_INTERNAL_TLS:-off}" = "on" ]; then echo "https://127.0.0.1:8000/health"
  else echo "http://127.0.0.1:8000/health"; fi
}
health_curl() {  # wie curl, mit TLS intern gegen die interne CA
  if [ "${AUTOMATO_INTERNAL_TLS:-off}" = "on" ] && [ -r "$TLS_PKI/client/ca.crt" ]; then
    curl --cacert "$TLS_PKI/client/ca.crt" "$@"
  else
    curl "$@"
  fi
}
