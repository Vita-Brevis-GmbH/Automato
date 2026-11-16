#!/usr/bin/env bash
# Update der Automato-VM mit Sicherung vorher und Rueckfall (Produktions-Gate).
#
#   deploy/update.sh            Sicherung -> git pull --ff-only -> Images bauen -> starten -> Health
#   deploy/update.sh <git-ref>  wie oben, aber auf einen bestimmten Stand: Branch (z. B. main -
#                               haengt wieder an den Branch an, auch nach einem Rueckfall), Tag
#                               oder Commit (losgeloest)
#   deploy/update.sh --rollback zurueck auf Code UND Datenbank vor dem letzten Update
#
# Am Ende prueft deploy/aufbau.sh den Aufbau und die Verbindungen (nur Hinweis, kein Abbruch).
#
# Aktuell halten (0.43.0): Fremd-Images (Postgres, Redis, Keycloak, Caddy) und die Basis-Images
# der eigenen holt jedes Update neu - innerhalb ihres Tags (Sicherheitskorrekturen, die
# Hauptversion legt docker-compose.yml fest; abschalten mit AUTOMATO_PULL_IMAGES=0). Docker
# selbst (Engine, Compose, containerd) mit AUTOMATO_DOCKER_UPDATE=1 in .env, sonst von Hand:
# sudo deploy/aufbau.sh docker-update. Alte Image-Staende raeumt es danach auf.
#
# Die Sicherung macht deploy/backup/backup.sh (age-Empfaenger: AUTOMATO_BACKUP_AGE_RECIPIENT,
# fuer den Rueckfall die Identitaet AUTOMATO_BACKUP_AGE_IDENTITY) - beide seit 0.42.0 auch aus
# .env, einmal eingerichtet mit `deploy/aufbau.sh backup-schluessel`. Profile ueber
# COMPOSE_PROFILES in .env (Standard nur "core"). Migrationen laufen vor dem Start des
# Orchestrators (seit 0.48.0 im Dienst migrate, als Eigentuemer der Tabellen); deshalb setzt der
# Rueckfall auch die Datenbank auf die Sicherung zurueck - Daten seit dem Update gehen dabei
# verloren (meist nur Minuten).
#
# Images aus dem Cockpit (1.15.0, ADR 0060): deploy/betrieb.sh aktualisiert mit fertigen Images
# aus GHCR (signiertes Manifest). update.sh baut wie bisher aus git und entfernt dabei die
# gepinnten Images (.automato-images.env); --rollback stellt auch sie wieder her. Beide teilen
# Sperre, Stand und Rueckfall (deploy/lib-update.sh).
set -euo pipefail
cd "$(dirname "$0")/.."
# Einstellungen der Sicherung bzw. Profile aus .env (0.42.0), die Umgebung geht vor
. deploy/env.sh

COMPOSE=${COMPOSE:-docker compose}
compose_profile || exit 2
HEALTH_URL=${AUTOMATO_HEALTH_URL:-$(standard_health_url)}
BACKUP_DIR=${AUTOMATO_BACKUP_DIR:-/var/backups/automato}
# Health, Sicherung, Stand des letzten Updates (.automato-update), gepinnte Images, Rueckfall
# shellcheck source=deploy/lib-update.sh
. deploy/lib-update.sh

sperren() {
  lu_sperren || {
    echo "FEHLER: gerade laeuft schon ein Update bzw. Rueckfall (deploy/betrieb.sh oder" \
      "update.sh) - spaeter erneut" >&2
    exit 2
  }
}

rollback() {
  [ -f "$STATE" ] || { echo "FEHLER: kein vorheriges Update gefunden ($STATE)" >&2; exit 2; }
  sperren
  local rc=0
  lu_rueckfall || rc=$?
  case $rc in
    0) ;;
    1) echo "FEHLER: Health nach dem Rueckfall nicht ok - Logs pruefen" >&2; exit 1 ;;
    *) exit "$rc" ;;
  esac
  lu_stand_zurueck
  echo "Rueckfall fertig: Version $(lu_laufende_version)."
}

update() {
  local ref=${1:-}
  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "FEHLER: lokale Aenderungen im Repo - erst sichern/verwerfen" >&2
    exit 2
  fi
  if [ -z "$ref" ] && ! git symbolic-ref -q HEAD >/dev/null; then
    echo "FEHLER: kein Branch ausgecheckt (z. B. nach einem Rueckfall) - Branch angeben:" >&2
    echo "        deploy/update.sh main   (bzw. einen Tag/Commit)" >&2
    exit 2
  fi
  sperren
  local prev_rev prev_version
  prev_rev=$(git rev-parse HEAD)
  prev_version=$(lu_laufende_version)

  lu_compose_pruefen || exit 2

  echo "1/4 Sicherung"
  lu_sicherung || exit $?
  lu_stand_schreiben "$prev_rev" "$prev_version"

  echo "2/4 Code holen"
  git fetch --quiet --tags origin
  if [ -z "$ref" ]; then
    git pull --quiet --ff-only
  elif git show-ref --verify --quiet "refs/remotes/origin/${ref#origin/}"; then
    # Branch (1.6.0): wieder anhaengen, damit das naechste Update ohne Angabe geht
    git checkout --quiet -B "${ref#origin/}" "origin/${ref#origin/}"
  else
    git checkout --quiet --detach "$ref"  # Tag oder Commit
  fi
  echo "    $prev_version ($prev_rev) -> $(lu_version) ($(git rev-parse HEAD))"

  echo "3/4 Images holen, bauen und starten"
  # Bau aus git (1.15.0): Images aus einem Update ueber deploy/betrieb.sh gelten nicht mehr
  lu_images_entfernen
  if [ "${AUTOMATO_DOCKER_UPDATE:-}" = "1" ]; then
    deploy/aufbau.sh docker-update || echo "HINWEIS: Docker nicht aktualisiert – weiter mit der bisherigen Version"
  fi
  if [ "${AUTOMATO_PULL_IMAGES:-1}" != "0" ]; then
    # neuster Stand je Tag; eigene Images werden gebaut, nicht geholt
    $COMPOSE "${PROFILES[@]}" pull --quiet --ignore-buildable 2>/dev/null ||
      $COMPOSE "${PROFILES[@]}" pull --quiet --ignore-pull-failures ||
      echo "HINWEIS: Images nicht geholt – weiter mit den vorhandenen"
    $COMPOSE "${PROFILES[@]}" build --pull || {
      echo "HINWEIS: Basis-Images nicht erreichbar – Bau mit den vorhandenen"
      $COMPOSE "${PROFILES[@]}" build
    }
  else
    $COMPOSE "${PROFILES[@]}" build
  fi
  $COMPOSE "${PROFILES[@]}" up -d

  echo "4/4 Health"
  if ! lu_health; then
    echo "FEHLER: Orchestrator meldet nach dem Update nicht 'ok'." >&2
    echo "        Logs: $COMPOSE logs migrate orchestrator | Rueckfall: deploy/update.sh --rollback" >&2
    exit 1
  fi
  lu_stand_ok
  echo "Update fertig: Version $(lu_version). Rueckfall bei Bedarf: deploy/update.sh --rollback"
  # alte, nicht mehr benutzte Image-Staende (ohne Tag) freigeben - sonst fuellt sich die Platte
  docker image prune -f >/dev/null 2>&1 || true
  # Aufbau und Verbindungen pruefen (0.40.0) - meldet, bricht das Update aber nicht ab
  deploy/aufbau.sh pruefen || echo "HINWEIS: Die Prüfung meldet Fehler (oben) – Abhilfe dort bzw. System → Verbindungen."
}

case "${1:-}" in
  --rollback) rollback ;;
  -h | --help) sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//' ;;
  *) update "${1:-}" ;;
esac
