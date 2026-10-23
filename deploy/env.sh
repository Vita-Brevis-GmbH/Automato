# Betriebs-Einstellungen aus der .env des Repos (0.42.0) - fuer update.sh, aufbau.sh und die
# Sicherung. Einmal in .env eingetragen, gelten sie bei jedem Aufruf; ein Update vergisst keine
# Angabe (Muster: Magisters plattform-aufbau.sh). Eine Umgebungsvariable geht vor.
#
# Nur bekannte Schluessel, und `read` statt `source`: die .env wird nicht ausgefuehrt.
# Einbinden mit `. deploy/env.sh` aus dem Repo-Verzeichnis.

AUTOMATO_ENV_KEYS="AUTOMATO_BACKUP_AGE_RECIPIENT AUTOMATO_BACKUP_AGE_IDENTITY AUTOMATO_BACKUP_DIR \
AUTOMATO_BACKUP_KEEP_DAYS AUTOMATO_BACKUP_PLAIN AUTOMATO_COMPOSE_PROFILES AUTOMATO_NETBOX_DIR"

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
