#!/usr/bin/env bash
# Update der Automato-VM mit Sicherung vorher und Rueckfall (Produktions-Gate).
#
#   deploy/update.sh            Sicherung -> git pull --ff-only -> Images bauen -> starten -> Health
#   deploy/update.sh <git-ref>  wie oben, aber auf einen bestimmten Stand (Tag/Commit/Branch)
#   deploy/update.sh --rollback zurueck auf Code UND Datenbank vor dem letzten Update
#
# Am Ende prueft deploy/aufbau.sh den Aufbau und die Verbindungen (nur Hinweis, kein Abbruch).
#
# Die Sicherung macht deploy/backup/backup.sh (age-Empfaenger: AUTOMATO_BACKUP_AGE_RECIPIENT,
# fuer den Rueckfall die Identitaet AUTOMATO_BACKUP_AGE_IDENTITY). Profile ueber
# AUTOMATO_COMPOSE_PROFILES (Standard "--profile core"). Migrationen laufen beim Start des
# Orchestrators; deshalb setzt der Rueckfall auch die Datenbank auf die Sicherung zurueck -
# Daten seit dem Update gehen dabei verloren (meist nur Minuten).
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE=${COMPOSE:-docker compose}
read -r -a PROFILES <<<"${AUTOMATO_COMPOSE_PROFILES:---profile core}"
HEALTH_URL=${AUTOMATO_HEALTH_URL:-http://127.0.0.1:8000/health}
BACKUP_DIR=${AUTOMATO_BACKUP_DIR:-/var/backups/automato}
STATE=.automato-update # letzter Update-Stand (gitignored)

health() {
  for _ in $(seq 1 60); do
    if curl -fsS "$HEALTH_URL" 2>/dev/null | grep -q '"status":"ok"'; then return 0; fi
    sleep 2
  done
  return 1
}

version() { cat VERSION 2>/dev/null || echo "?"; }

rollback() {
  [ -f "$STATE" ] || { echo "FEHLER: kein vorheriges Update gefunden ($STATE)" >&2; exit 2; }
  # shellcheck disable=SC1090
  . "./$STATE"
  echo "Rueckfall auf $prev_rev (Version $prev_version), Datenbank aus $(basename "$dump")"
  git checkout --quiet --detach "$prev_rev"
  $COMPOSE "${PROFILES[@]}" build
  AUTOMATO_RESTORE_YES=1 deploy/backup/restore.sh "$dump" ${key:+"$key"}
  $COMPOSE "${PROFILES[@]}" up -d
  health || { echo "FEHLER: Health nach dem Rueckfall nicht ok - Logs pruefen" >&2; exit 1; }
  echo "Rueckfall fertig: Version $(version)."
}

update() {
  local ref=${1:-}
  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "FEHLER: lokale Aenderungen im Repo - erst sichern/verwerfen" >&2
    exit 2
  fi
  if [ -z "$ref" ] && ! git symbolic-ref -q HEAD >/dev/null; then
    echo "FEHLER: kein Branch ausgecheckt (z. B. nach einem Rueckfall) - Stand angeben:" >&2
    echo "        deploy/update.sh <tag|branch>" >&2
    exit 2
  fi
  local prev_rev prev_version
  prev_rev=$(git rev-parse HEAD)
  prev_version=$(version)

  echo "1/4 Sicherung"
  deploy/backup/backup.sh "$BACKUP_DIR"
  local dump key
  dump=$(ls -t "$BACKUP_DIR"/automato-*.dump* | head -1)
  key=$(ls -t "$BACKUP_DIR"/credentials-*.key* 2>/dev/null | head -1 || true)
  {
    printf 'prev_rev=%q\n' "$prev_rev"
    printf 'prev_version=%q\n' "$prev_version"
    printf 'dump=%q\n' "$dump"
    printf 'key=%q\n' "$key"
    printf 'at=%q\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } >"$STATE"

  echo "2/4 Code holen"
  git fetch --quiet --tags origin
  if [ -n "$ref" ]; then git checkout --quiet --detach "$ref"; else git pull --quiet --ff-only; fi
  echo "    $prev_version ($prev_rev) -> $(version) ($(git rev-parse HEAD))"

  echo "3/4 Images bauen und starten"
  $COMPOSE "${PROFILES[@]}" build
  $COMPOSE "${PROFILES[@]}" up -d

  echo "4/4 Health"
  if ! health; then
    echo "FEHLER: Orchestrator meldet nach dem Update nicht 'ok'." >&2
    echo "        Logs: $COMPOSE logs orchestrator | Rueckfall: deploy/update.sh --rollback" >&2
    exit 1
  fi
  echo "Update fertig: Version $(version). Rueckfall bei Bedarf: deploy/update.sh --rollback"
  # Aufbau und Verbindungen pruefen (0.40.0) - meldet, bricht das Update aber nicht ab
  deploy/aufbau.sh pruefen || echo "HINWEIS: Die Prüfung meldet Fehler (oben) – Abhilfe dort bzw. System → Verbindungen."
}

case "${1:-}" in
  --rollback) rollback ;;
  -h | --help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) update "${1:-}" ;;
esac
