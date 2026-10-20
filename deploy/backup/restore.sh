#!/usr/bin/env bash
# Wiederherstellung einer Sicherung von backup.sh (ERSETZT die Datenbank).
#
#   AUTOMATO_BACKUP_AGE_IDENTITY=~/automato-backup.agekey \
#     deploy/backup/restore.sh /var/backups/automato/automato-<zeit>.dump.age [credentials-<zeit>.key.age]
#
# Ablauf: Orchestrator stoppen, Datenbank leeren und aus dem Dump fuellen, Schluesseldatei
# zurueckspielen, Orchestrator starten (Migrationen laufen beim Start). Fragt vorher nach;
# ohne Rueckfrage mit AUTOMATO_RESTORE_YES=1.
set -euo pipefail
cd "$(dirname "$0")/../.."

DUMP=${1:?Dump-Datei angeben}
KEYFILE=${2:-}
IDENTITY=${AUTOMATO_BACKUP_AGE_IDENTITY:-}
COMPOSE=${COMPOSE:-docker compose}

open_file() { # <datei> -> stdout (entschluesselt, falls .age)
  case "$1" in
    *.age)
      [ -n "$IDENTITY" ] || { echo "FEHLER: AUTOMATO_BACKUP_AGE_IDENTITY fehlt fuer $1" >&2; exit 2; }
      age -d -i "$IDENTITY" "$1"
      ;;
    *) cat "$1" ;;
  esac
}

if [ "${AUTOMATO_RESTORE_YES:-}" != "1" ]; then
  read -r -p "Datenbank wird durch $(basename "$DUMP") ERSETZT. Fortfahren? [ja/NEIN] " answer
  [ "$answer" = "ja" ] || { echo "Abgebrochen."; exit 1; }
fi

open_file "$DUMP" >/dev/null # Entschluesselung vorab pruefen (Identitaet/Datei)
$COMPOSE stop orchestrator collector >/dev/null 2>&1 || $COMPOSE stop orchestrator
open_file "$DUMP" | $COMPOSE exec -T postgres pg_restore -U automato -d automato --clean --if-exists --no-owner
echo "Datenbank wiederhergestellt."
if [ -n "$KEYFILE" ]; then
  open_file "$KEYFILE" | $COMPOSE run --rm --no-deps -T --entrypoint sh orchestrator \
    -c 'umask 077; cat > /var/lib/automato/credentials.key; chmod 600 /var/lib/automato/credentials.key'
  echo "Schluessel der Zugangsdaten zurueckgespielt."
fi
$COMPOSE up -d orchestrator
echo "Orchestrator gestartet - System-Seite und /health pruefen."
