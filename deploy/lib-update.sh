# shellcheck shell=bash
# Gemeinsame Schritte von deploy/update.sh (Bau aus git) und deploy/betrieb.sh (Images aus dem
# signierten Manifest, 1.15.0, ADR 0060): Sperre, Health, Sicherung, Stand fuer den Rueckfall,
# gepinnte Images und der Rueckfall selbst.
#
# Einbinden aus dem Repo-Verzeichnis nach `. deploy/env.sh` und `compose_profile`; erwartet
# COMPOSE, PROFILES, HEALTH_URL und BACKUP_DIR. Die Funktionen beenden das Skript nie selbst -
# sie geben einen Code zurueck (auch unter `set -e` sicher, weil sie jeden Schritt pruefen).
#
# Stand fuer den Rueckfall (.automato-update, gitignored, NAME=Wert mit printf %q):
#   prev_rev, prev_version   Commit und Version vor dem letzten Update
#   prev_quelle, prev_kanal  git (gebaut) bzw. images (aus dem Manifest) und dessen Kanal
#   prev_images              Inhalt von .automato-images.env vor dem Update (leer = gebaut)
#   dump, key, at            Sicherung des Updates und Zeitpunkt
#   ok=1                     Update erfolgreich (Health ok)
#   zurueck_am=<Zeit>        Rueckfall auf diesen Stand erledigt (betrieb.sh zurueck nur einmal)

STATE=.automato-update
SPERRE=.betrieb.lock # eine Aktion je Checkout: update.sh und betrieb.sh (Vertrag betrieb/v1)

lu_sperren() { # 0 = Sperre gehalten bis zum Ende des Skripts, 1 = belegt, 2 = flock fehlt
  command -v flock >/dev/null 2>&1 || return 2
  exec 9>>"$SPERRE" || return 1
  flock -n 9
}

lu_version() { cat VERSION 2>/dev/null || echo "?"; }

lu_laufende_version() { # mit gepinnten Images die Version aus dem Manifest, sonst VERSION
  if [ -n "${AUTOMATO_MANIFEST_VERSION:-}" ]; then echo "$AUTOMATO_MANIFEST_VERSION"; else lu_version; fi
}

lu_health() { # /health meldet "ok" - LU_HEALTH_VERSUCHE Versuche (Standard 60, betrieb.sh 150)
  # im Abstand von AUTOMATO_HEALTH_PAUSE Sekunden (2)
  local i versuche=${LU_HEALTH_VERSUCHE:-${AUTOMATO_HEALTH_VERSUCHE:-60}}
  for ((i = 0; i < versuche; i++)); do
    if health_curl -fsS -m 10 "$HEALTH_URL" 2>/dev/null | grep -q '"status":"ok"'; then return 0; fi
    sleep "${AUTOMATO_HEALTH_PAUSE:-2}"
  done
  return 1
}

lu_compose_pruefen() { # Projekt lesbar? Sonst scheitert schon die Sicherung unklar (0.42.1)
  local meldung
  if ! meldung=$($COMPOSE "${PROFILES[@]}" config --quiet 2>&1); then
    echo "FEHLER: docker compose kann das Projekt nicht lesen: $meldung" >&2
    if [ -f docker-compose.override.yml ]; then
      echo "        Pruefen: $(pwd)/docker-compose.override.yml (VM-spezifisch) - ein fremder" >&2
      echo "        Dienst darin (z. B. netbox:) gehoert in source-of-truth/netbox/netbox-docker/." >&2
    fi
    echo "        Ueberblick: deploy/aufbau.sh pruefen" >&2
    return 2
  fi
}

lu_sicherung_moeglich() {
  [ -n "${AUTOMATO_BACKUP_AGE_RECIPIENT:-}" ] || [ "${AUTOMATO_BACKUP_PLAIN:-}" = "1" ]
}

lu_sicherung() { # verschluesselte Sicherung wie vor jedem Update; setzt LU_DUMP und LU_KEY
  LU_DUMP="" LU_KEY=""
  if ! lu_sicherung_moeglich; then
    echo "FEHLER: keine verschluesselte Sicherung moeglich - age-Schluessel fehlt." >&2
    echo "        Einmal einrichten: deploy/aufbau.sh backup-schluessel (schreibt ihn in .env)" >&2
    return 2
  fi
  deploy/backup/backup.sh "$BACKUP_DIR" || return $?
  # shellcheck disable=SC2012 # Namen von backup.sh (Zeitstempel, ohne Leerzeichen)
  LU_DUMP=$(ls -t "$BACKUP_DIR"/automato-*.dump* 2>/dev/null | head -1)
  # shellcheck disable=SC2012
  LU_KEY=$(ls -t "$BACKUP_DIR"/credentials-*.key* 2>/dev/null | head -1 || true)
  [ -n "$LU_DUMP" ] || { echo "FEHLER: keine Sicherung in $BACKUP_DIR gefunden" >&2; return 1; }
}

lu_letzte_sicherung() { # Zeit der neusten Sicherung (ISO, UTC) oder leer
  local zeit
  zeit=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'automato-*.dump*' -printf '%T@\n' 2>/dev/null |
    sort -n | tail -1)
  [ -n "$zeit" ] || return 0
  date -u -d "@${zeit%.*}" +%Y-%m-%dT%H:%M:%SZ
}

lu_images_inhalt() { # gepinnte Images ohne Kommentare (fuer den Stand) bzw. nichts
  [ -f "$IMAGES_DATEI" ] || return 0
  grep -E '^AUTOMATO_(IMAGE|MANIFEST)_[A-Z0-9_]+=' "$IMAGES_DATEI" || true
}

lu_stand_schreiben() { # $1 bisheriger Commit, $2 bisherige Version - nach der Sicherung
  local quelle=git
  [ -f "$IMAGES_DATEI" ] && quelle=images
  {
    printf 'prev_rev=%q\n' "$1"
    printf 'prev_version=%q\n' "$2"
    printf 'prev_quelle=%q\n' "$quelle"
    printf 'prev_kanal=%q\n' "${AUTOMATO_MANIFEST_KANAL:-}"
    printf 'prev_images=%q\n' "$(lu_images_inhalt)"
    printf 'dump=%q\n' "$LU_DUMP"
    printf 'key=%q\n' "$LU_KEY"
    printf 'at=%q\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } >"$STATE.neu" && mv -f "$STATE.neu" "$STATE"
}

lu_stand_ok() { printf 'ok=1\n' >>"$STATE"; }

lu_stand_zurueck() { printf 'zurueck_am=%q\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$STATE"; }

lu_stand_lesen() { # Stand des letzten Updates in die Variablen oben; 1 = keiner
  unset prev_rev prev_version prev_quelle prev_kanal prev_images dump key at ok zurueck_am
  [ -f "$STATE" ] || return 1
  # shellcheck disable=SC1090 # von lu_stand_schreiben bzw. update.sh geschrieben (printf %q)
  . "./$STATE"
}

lu_zurueck_moeglich() { # Stand gelesen, Sicherung vorhanden, noch nicht zurueck
  lu_stand_lesen && [ -n "${prev_rev:-}" ] && [ -n "${dump:-}" ] && [ -f "$dump" ] &&
    [ -z "${zurueck_am:-}" ]
}

lu_images_schreiben() { # stdin: Zeilen NAME=Wert -> .automato-images.env, danach laden
  {
    echo "# Gepinnte Images aus dem signierten Manifest (deploy/betrieb.sh, ADR 0060) - nicht von"
    echo "# Hand aendern; deploy/update.sh (Bau aus git) entfernt die Datei wieder."
    cat
  } >"$IMAGES_DATEI.neu" && mv -f "$IMAGES_DATEI.neu" "$IMAGES_DATEI" || return 1
  images_laden
}

lu_images_entfernen() { # zurueck zum Bau aus git: gepinnte Images gelten nicht mehr
  rm -f "$IMAGES_DATEI" "$IMAGES_DATEI.neu"
  images_laden
}

lu_images_refs() { # $1 Datei -> je Zeile "<VARIABLE> <Referenz>"
  local datei=$1 schluessel wert
  [ -f "$datei" ] || return 0
  while IFS='=' read -r schluessel wert; do
    if [[ $wert =~ @sha256:[0-9a-f]{64}$ ]]; then printf '%s %s\n' "$schluessel" "$wert"; fi
  done < <(grep -E '^AUTOMATO_IMAGE_[A-Z0-9_]+=' "$datei" || true)
}

lu_images_holen() { # $1 Datei mit Images - fehlende holen, nur per Digest (nie bauen)
  local variable ref
  while read -r variable ref; do
    docker image inspect "$ref" >/dev/null 2>&1 && continue
    echo "    hole ${variable#AUTOMATO_IMAGE_}: $ref"
    docker pull --quiet "$ref" >/dev/null || {
      echo "FEHLER: Image nicht geholt: $ref (privat: Pull-Zugang aus dem Cockpit bzw. docker login ghcr.io)" >&2
      return 1
    }
  done < <(lu_images_refs "$1")
}

lu_images_lokal_markieren() { # lokale Namen <projekt>-<dienst> auf die gepinnten Images: ein
  # `docker compose up -d` von Hand (ohne deploy/env.sh) nimmt dann dieselben, nicht alte gebaute
  local projekt variable ref dienst
  projekt=$($COMPOSE "${PROFILES[@]}" config 2>/dev/null | sed -n 's/^name: *//p' | head -1)
  [[ $projekt =~ ^[a-z0-9][a-z0-9_-]*$ ]] || return 0
  while read -r variable ref; do
    dienst=${variable#AUTOMATO_IMAGE_}
    dienst=${dienst//_/-}
    docker tag "$ref" "$projekt-${dienst,,}" 2>/dev/null ||
      echo "HINWEIS: lokaler Name $projekt-${dienst,,} nicht gesetzt"
  done < <(lu_images_refs "$IMAGES_DATEI")
}

lu_rueckfall() { # Code, Images und Datenbank auf den Stand vor dem letzten Update
  # 0 = Health ok, 1 = Health nicht ok, 2 = ein Schritt gescheitert
  lu_stand_lesen || { echo "FEHLER: kein vorheriges Update gefunden ($STATE)" >&2; return 2; }
  if [ -z "${prev_rev:-}" ] || [ -z "${dump:-}" ] || [ ! -f "$dump" ]; then
    echo "FEHLER: Stand des Updates unvollstaendig bzw. Sicherung fehlt (${dump:-keine})" >&2
    return 2
  fi
  echo "Rueckfall auf $prev_rev (Version ${prev_version:-?}), Datenbank aus $(basename "$dump")"
  git checkout --quiet --detach "$prev_rev" || return 2
  if [ -n "${prev_images:-}" ]; then
    # vorher Images aus dem Manifest: dieselben wieder (per Digest, fehlende neu geholt) - nicht
    # in einer Pipe, sonst laedt eine Subshell die Variablen
    lu_images_schreiben < <(printf '%s\n' "$prev_images") || return 2
    lu_images_holen "$IMAGES_DATEI" || return 2
    lu_images_lokal_markieren
  else
    lu_images_entfernen
    $COMPOSE "${PROFILES[@]}" build || return 2
  fi
  AUTOMATO_RESTORE_YES=1 deploy/backup/restore.sh "$dump" ${key:+"$key"} || return 2
  if [ -n "${prev_images:-}" ]; then
    $COMPOSE "${PROFILES[@]}" up -d --no-build || return 2
  else
    $COMPOSE "${PROFILES[@]}" up -d || return 2
  fi
  lu_health || return 1
}
