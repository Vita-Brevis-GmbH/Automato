# shellcheck shell=bash
# Eigene Automato-Instanz je Kunde (1.16.0, ADR 0061): `deploy/betrieb.sh installieren` und
# `entfernen` (Vertrag betrieb/v1, Cockpit P4 „Ausrollen“). Eingebunden von deploy/betrieb.sh nach
# deploy/env.sh und deploy/lib-update.sh; nutzt dessen ende, passt, datei_ok, dienst_variable,
# herausgeber, lokal_geaendert und gesund_kurz.
#
# Eine Instanz ist ein eigener Checkout <basisordner>/<instanz>, den der Host-Agent des Cockpits
# anlegt (git clone der Quelle aus seiner Konfiguration, Commit des Manifests) - mit eigener .env
# (hier erzeugt: Geheimnisse nur auf diesem Host), eigenem Compose-Projekt automato-<instanz> und
# ohne Ports auf dem Host (deploy/instanz/compose.yml). Erreichbar ist sie nur über den Front-Proxy
# derselben VM: Netz automato-front-instanzen, Anschluss unter automato-<instanz>-web-ui:5173.
# Bis zum Proxmox-Adapter nur auf dem Host des Front-Proxys (ADR 0061).

INSTANZ_RE='^[a-z][a-z0-9-]{1,30}$'
INSTANZ_NETZ=automato-front-instanzen
INSTANZ_COMPOSE_FILE=docker-compose.yml:deploy/instanz/compose.yml
INSTANZ_PROFILE=core,collectors # wie der Kern: Datenbank, Redis, migrate, Orchestrator, Web-UI, Worker
INSTANZ_PORT=5173
KUNDE_RE='^[a-z0-9]([a-z0-9-]{0,40}[a-z0-9])?$' # Kurzname wie im Kunden-Portal bzw. front.sh
DOMAIN_RE='^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?$'
ISSUER_RE='^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~-]+)*/realms/[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
# Einmal-Code der Kopplung (ADR 0057): Crockford-Base32 ohne I, L, O, U, 3 x 4 Zeichen
KOPPLUNG_RE='^[0-9A-HJKMNP-TV-Z]{4}-?[0-9A-HJKMNP-TV-Z]{4}-?[0-9A-HJKMNP-TV-Z]{4}$'

# Angaben der Instanz fuer die Antwort (gesetzt von installieren, sonst aus der .env)
S_INSTANZ="" S_INSTANZ_KUNDE="" S_INSTANZ_DOMAIN=""

instanz_antwort() { # ,"instanz":{…} fuer die Antwort - nur in einer eigenen Instanz
  local n=${S_INSTANZ:-${AUTOMATO_INSTANZ:-}} k d adresse=""
  [[ $n =~ $INSTANZ_RE ]] || return 0
  k=$(passt "${S_INSTANZ_KUNDE:-${AUTOMATO_INSTANZ_KUNDE:-}}" "$KUNDE_RE")
  d=$(passt "${S_INSTANZ_DOMAIN:-${AUTOMATO_INSTANZ_DOMAIN:-}}" "$DOMAIN_RE")
  [ -z "$k" ] || [ -z "$d" ] || adresse="https://$k-doc.$d"
  printf ',"instanz":{"name":%s,"projekt":%s,"web_ui":%s,"kunde":%s,"domain":%s,"adresse":%s}' \
    "$(json_text "$n")" "$(json_text "automato-$n")" "$(json_text "automato-$n-web-ui:$INSTANZ_PORT")" \
    "$(json_text "$k")" "$(json_text "$d")" "$(json_text "$adresse")"
}

instanz_projekt() { # Name des Compose-Projekts dieses Checkouts (aus .env bzw. Verzeichnis)
  $COMPOSE config 2>/dev/null | sed -n 's/^name: *//p' | head -1
}

instanz_reste() { # $1 Projekt: 0 = es gibt Container, Volumes oder Netze dieses Projekts
  local label="label=com.docker.compose.project=$1"
  [ -n "$(docker ps -aq --filter "$label" 2>/dev/null)$(docker volume ls -q --filter "$label" 2>/dev/null)$(docker network ls -q --filter "$label" 2>/dev/null)" ]
}

instanz_reste_entfernen() { # $1 Projekt: Container, Volumes und Netze nur dieses Projekts
  local label="label=com.docker.compose.project=$1" id
  for id in $(docker ps -aq --filter "$label" 2>/dev/null); do docker rm -f "$id" >/dev/null 2>&1; done
  for id in $(docker volume ls -q --filter "$label" 2>/dev/null); do docker volume rm "$id" >/dev/null 2>&1; done
  for id in $(docker network ls -q --filter "$label" 2>/dev/null); do docker network rm "$id" >/dev/null 2>&1; done
  return 0
}

instanz_namen_entfernen() { # $1 Projekt: lokale Namen <projekt>-<dienst> (lu_images_lokal_markieren)
  local variable dienst
  while read -r variable _; do
    dienst=${variable#AUTOMATO_IMAGE_}
    dienst=${dienst//_/-}
    docker image rm "$1-${dienst,,}" >/dev/null 2>&1 || true
  done < <(lu_images_refs "$IMAGES_DATEI")
}

instanz_manifest() { # $1 Version, $2 Manifest, $3 Signatur -> M_KANAL, M_COMMIT, M_IMAGES
  # (wie aktualisieren: Format, Produkt, Version, Signatur, Images nur per Digest)
  local schluessel pruefung rc=0 zeile name wert
  schluessel=$(herausgeber) ||
    ende 3 "Herausgeber-Schlüssel fehlt (/etc/automato/herausgeber.pub bzw. $HERAUSGEBER_REPO) – nichts geändert"
  echo "Manifest pruefen ($schluessel)"
  pruefung=$(python3 -I scripts/release_manifest.py pruefen --manifest "$2" --signatur "$3" \
    --schluessel "$schluessel" --produkt automato --version "$1") || rc=$?
  case $rc in
    0) ;;
    4) ende 3 "Signatur des Manifests fehlt bzw. ist ungültig – nichts geändert" ;;
    5) ende 3 "Herausgeber-Schlüssel ungültig (Ed25519, PEM) bzw. openssl fehlt – nichts geändert" ;;
    6) ende 3 "Manifest passt nicht zum Auftrag (Produkt bzw. Version) – nichts geändert" ;;
    *) ende 3 "Manifest ungültig – nichts geändert" ;;
  esac
  M_KANAL="" M_COMMIT="" M_IMAGES=()
  while IFS= read -r zeile; do
    name=${zeile%%=*} wert=${zeile#*=}
    case $name in
      kanal) M_KANAL=$wert ;;
      commit) M_COMMIT=$wert ;;
      image.*)
        [[ $wert =~ ^[a-z0-9./:_-]+@sha256:[0-9a-f]{64}$ ]] || ende 3 "Manifest ungültig – nichts geändert"
        M_IMAGES+=("$(dienst_variable "${name#image.}")=$wert")
        ;;
    esac
  done <<<"$pruefung"
  if ! [[ $M_COMMIT =~ $COMMIT_RE && $M_KANAL =~ ^(stable|latest|dev)$ ]] || [ "${#M_IMAGES[@]}" -eq 0 ]; then
    ende 3 "Manifest ungültig – nichts geändert"
  fi
}

instanz_age() { # $1 Identitaet -> age-Empfaenger; legt die Identitaet an, wenn es keine gibt
  # (wie aufbau.sh backup-schluessel: dieselbe des Hosts fuer Hauptinstanz und Instanzen)
  if [ ! -f "$1" ]; then
    (umask 077 && age-keygen -o "$1" >/dev/null 2>&1) || return 1
    echo "age-Identitaet fuer die Sicherung angelegt: $1 (zusaetzlich getrennt aufbewahren)" >&2
  fi
  local empfaenger
  empfaenger=$(age-keygen -y "$1" 2>/dev/null) || return 1
  [[ $empfaenger =~ ^age1[0-9a-z]{20,100}$ ]] || return 1
  printf '%s' "$empfaenger"
}

instanz_env_schreiben() { # $1 Instanz, $2 Kunde, $3 Domain, $4 Issuer, $5 age-Empfaenger,
  # $6 age-Identitaet: .env mit neuen Geheimnissen (nur auf diesem Host, nie im Auftrag)
  local pg db api audit backup
  pg=$(openssl rand -hex 32 2>/dev/null) && db=$(openssl rand -hex 24 2>/dev/null) &&
    api=$(openssl rand -hex 32 2>/dev/null) && audit=$(openssl rand -hex 32 2>/dev/null) || return 1
  [ ${#pg} -eq 64 ] && [ ${#db} -eq 48 ] && [ ${#api} -eq 64 ] && [ ${#audit} -eq 64 ] || return 1
  backup="${AUTOMATO_BACKUP_DIR:-/var/backups/automato}/instanzen/$1"
  (
    umask 077
    cat >.env.neu <<EOF
# Eigene Automato-Instanz $1 (Kunde $2, ADR 0061) - erzeugt von deploy/betrieb.sh installieren
# am $(date -u +%Y-%m-%dT%H:%M:%SZ). Geheimnisse nur auf diesem Host: nie kopieren, nie ins Cockpit.
# Prüfen ohne Werte: sudo deploy/aufbau.sh env

# --- Betrieb (Docker Compose, Skripte) ---
COMPOSE_PROJECT_NAME=automato-$1
COMPOSE_FILE=$INSTANZ_COMPOSE_FILE
COMPOSE_PROFILES=$INSTANZ_PROFILE
AUTOMATO_INSTANZ=$1
AUTOMATO_INSTANZ_KUNDE=$2
AUTOMATO_INSTANZ_DOMAIN=$3
AUTOMATO_ENVIRONMENT=prod
POSTGRES_PASSWORD=$pg
AUTOMATO_DB_USER=automato_app
AUTOMATO_DB_PASSWORD=$db

# --- Orchestrator ---
AUTOMATO_INTERNAL_API_TOKEN=$api
AUTOMATO_AUDIT_ENCRYPTION_KEY=$audit
AUTOMATO_CORS_ORIGINS=["https://$2-doc.$3"]

# --- Anmeldung (Realm des Kunden, Client automato-web) ---
AUTOMATO_AUTH_MODE=oidc
AUTOMATO_OIDC_ISSUER=$4
AUTOMATO_OIDC_AUDIENCE=automato-api
AUTOMATO_OIDC_JWKS_URL=$4/protocol/openid-connect/certs
AUTOMATO_OIDC_WEB_CLIENT_ID=automato-web
AUTOMATO_ALLOW_UNAUTHENTICATED_READ_API=false

# --- Sicherung (deploy/backup) ---
AUTOMATO_BACKUP_AGE_RECIPIENT=$5
AUTOMATO_BACKUP_AGE_IDENTITY=$6
AUTOMATO_BACKUP_DIR=$backup
EOF
  ) && chmod 600 .env.neu && mv -f .env.neu .env
}

instanz_aufraeumen() { # $1 Projekt: was ein gescheitertes installieren angelegt hat - 0 = nichts mehr
  # da (Container, Volumes, Netz des Projekts, lokale Namen, .env); nur dieses Projekts
  echo "Aufraeumen: $1 (Container, Volumes, Netz)"
  $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
  instanz_reste_entfernen "$1"
  instanz_namen_entfernen "$1"
  rm -f .env .env.neu "$IMAGES_DATEI" "$MANIFEST_IMAGES"
  images_laden
  ! instanz_reste "$1"
}

instanz_gescheitert() { # $1 Projekt, $2 Meldung (was scheiterte) - aufraeumen und Ende
  S_GESUND=false
  if instanz_aufraeumen "$1"; then
    S_GESUND=""
    ende 1 "$2 – Instanz wieder entfernt, nichts installiert"
  fi
  ende 2 "$2, Aufräumen unvollständig – von Hand: docker ps -a bzw. docker volume ls --filter label=com.docker.compose.project=$1"
}

# --- installieren --instanz <n> --version <v> --manifest <m> --signatur <s> --kunde <k> -----------
#     --domain <d> --issuer <url> [--kopplung <einmal-code>]
installieren() {
  local instanz="" version="" manifest="" signatur="" kunde="" domain="" issuer="" kopplung=""
  while [ $# -gt 0 ]; do
    case $1 in
      --instanz | --version | --manifest | --signatur | --kunde | --domain | --issuer | --kopplung)
        [ $# -ge 2 ] || ende 3 "Angabe ohne Wert"
        ;;
      *) ende 3 "Unbekannte Angabe – erwartet --instanz, --version, --manifest, --signatur, --kunde, --domain, --issuer [--kopplung]" ;;
    esac
    case $1 in
      --instanz) instanz=$2 ;;
      --version) version=$2 ;;
      --manifest) manifest=$2 ;;
      --signatur) signatur=$2 ;;
      --kunde) kunde=$2 ;;
      --domain) domain=$2 ;;
      --issuer) issuer=$2 ;;
      --kopplung) kopplung=${2^^} ;;
    esac
    shift 2
  done
  [[ $instanz =~ $INSTANZ_RE ]] ||
    ende 3 "--instanz fehlt bzw. ist ungültig (a–z, 0–9 und -, 2–31 Zeichen, zuerst ein Buchstabe)"
  [[ $version =~ $VERSION_RE ]] || ende 3 "--version fehlt bzw. ist ungültig"
  datei_ok "$manifest" || ende 3 "Manifest fehlt, ist leer oder grösser als 64 KiB – nichts geändert"
  datei_ok "$signatur" || ende 3 "Signatur fehlt bzw. ist ungültig – nichts geändert"
  [[ $kunde =~ $KUNDE_RE ]] || ende 3 "--kunde fehlt bzw. ist ungültig (Kurzname wie im Kunden-Portal)"
  { [ "${#domain}" -le 253 ] && [[ $domain =~ $DOMAIN_RE ]]; } || ende 3 "--domain fehlt bzw. ist ungültig"
  { [ "${#issuer}" -le 300 ] && [[ $issuer =~ $ISSUER_RE ]]; } ||
    ende 3 "--issuer fehlt bzw. ist ungültig (https://…/realms/<realm>)"
  [ -z "$kopplung" ] || [[ $kopplung =~ $KOPPLUNG_RE ]] ||
    ende 3 "--kopplung ist kein Einmal-Code (XXXX-XXXX-XXXX)"
  S_INSTANZ=$instanz S_INSTANZ_KUNDE=$kunde S_INSTANZ_DOMAIN=$domain
  local projekt="automato-$instanz"

  # frischer Checkout genau unter <basisordner>/<instanz> (Vertrag: Ziel nie ausserhalb)
  [ "$(basename "$PWD")" = "$instanz" ] ||
    ende 3 "Checkout liegt nicht unter <basisordner>/$instanz – nichts geändert"
  if [ -e .env ] || [ -e "$IMAGES_DATEI" ] || [ -e "$STATE" ]; then
    ende 3 "Checkout ist schon eingerichtet (.env) – installieren nur in einem frischen Checkout"
  fi
  lokal_geaendert && ende 3 "Lokale Änderungen im Checkout – nichts geändert"
  local werkzeug
  for werkzeug in python3 openssl age-keygen docker; do
    command -v "$werkzeug" >/dev/null 2>&1 || ende 3 "$werkzeug fehlt – nichts geändert"
  done

  # 1. Manifest und Signatur wie aktualisieren; der Checkout steht genau auf seinem Commit
  instanz_manifest "$version" "$manifest" "$signatur"
  [ "$(git rev-parse HEAD 2>/dev/null)" = "$M_COMMIT" ] ||
    ende 3 "Checkout steht nicht auf dem Commit des Manifests – nichts geändert"

  # 2. Host: Netz des Front-Proxys da, keine Reste einer früheren Instanz gleichen Namens
  docker network inspect "$INSTANZ_NETZ" >/dev/null 2>&1 ||
    ende 3 "Netz $INSTANZ_NETZ fehlt – eigene Instanzen nur auf dem Host des Front-Proxys (≥ 1.16.0: docker compose up -d proxy) – nichts geändert"
  if instanz_reste "$projekt"; then
    ende 3 "Reste einer früheren Instanz $projekt (Container, Volumes bzw. Netz) – erst prüfen, nichts geändert"
  fi
  local identitaet=${AUTOMATO_BACKUP_AGE_IDENTITY:-$HOME/automato-backup.agekey} empfaenger
  empfaenger=$(instanz_age "$identitaet") ||
    ende 3 "age-Schlüssel für die Sicherung nicht lesbar bzw. nicht angelegt ($identitaet) – nichts geändert"

  # 3. .env mit neuen Geheimnissen (nur hier), Profil der Instanz
  echo "1/5 Einstellungen der Instanz $instanz (.env, neue Geheimnisse)"
  instanz_env_schreiben "$instanz" "$kunde" "$domain" "$issuer" "$empfaenger" "$identitaet" || {
    rm -f .env .env.neu
    ende 3 "Einstellungen nicht geschrieben (openssl, Rechte im Checkout) – nichts geändert"
  }
  env_aus_datei .env
  compose_profile || true
  BACKUP_DIR=${AUTOMATO_BACKUP_DIR:-/var/backups/automato}
  if ! lu_compose_pruefen || [ "$(instanz_projekt)" != "$projekt" ]; then
    rm -f .env
    ende 3 "docker compose kann die Instanz nicht lesen (Compose ≥ 2.24 nötig) – nichts geändert"
  fi

  # 4. Images nur per Digest (nie bauen), starten, Health bis 5 min
  echo "2/5 Images holen (nur per Digest, kein Bau)"
  printf '%s\n' "AUTOMATO_MANIFEST_VERSION=$version" "AUTOMATO_MANIFEST_KANAL=$M_KANAL" \
    "AUTOMATO_MANIFEST_COMMIT=$M_COMMIT" "${M_IMAGES[@]}" >"$MANIFEST_IMAGES" ||
    instanz_gescheitert "$projekt" "Images nicht vorgemerkt"
  lu_images_holen "$MANIFEST_IMAGES" ||
    instanz_gescheitert "$projekt" "Images nicht geholt (Pull-Zugang aus dem Cockpit, GHCR öffentlich bzw. docker login ghcr.io?)"
  lu_images_schreiben <"$MANIFEST_IMAGES" || instanz_gescheitert "$projekt" "Images nicht vorgemerkt"
  rm -f "$MANIFEST_IMAGES"
  lu_images_lokal_markieren
  if [ "${AUTOMATO_PULL_IMAGES:-1}" != "0" ]; then
    # Fremd-Images (Postgres, Redis, Caddy) im festen Tag wie update.sh
    $COMPOSE "${PROFILES[@]}" pull --quiet --ignore-buildable ||
      echo "HINWEIS: Fremd-Images nicht geholt – weiter mit den vorhandenen"
  fi
  echo "3/5 Starten ($projekt, ohne Ports auf dem Host)"
  $COMPOSE "${PROFILES[@]}" up -d --no-build || instanz_gescheitert "$projekt" "Instanz startet nicht"
  echo "4/5 Health (bis 5 min)"
  lu_health || instanz_gescheitert "$projekt" "Instanz nicht gesund (Logs: docker compose logs migrate orchestrator)"
  S_GESUND=true

  # 5. Einmal-Code der Kopplung vorab hinterlegen (nur als Hash, wie aufbau.sh cockpit-kopplung)
  local gekoppelt=""
  if [ -n "$kopplung" ]; then
    echo "5/5 Einmal-Code der Kopplung hinterlegen"
    printf '%s\n' "$kopplung" | $COMPOSE "${PROFILES[@]}" exec -T orchestrator automato \
      cockpit-kopplung --hinterlegen || instanz_gescheitert "$projekt" "Einmal-Code der Kopplung nicht hinterlegt"
    gekoppelt=", Einmal-Code der Kopplung hinterlegt"
  else
    echo "5/5 ohne Einmal-Code der Kopplung (koppeln: deploy/aufbau.sh cockpit-kopplung)"
  fi
  ende 0 "Instanz $instanz installiert: $version, Health ok$gekoppelt"
}

# --- entfernen --ja ------------------------------------------------------------------------------
entfernen() {
  { [ $# -eq 1 ] && [ "$1" = --ja ]; } ||
    ende 3 "entfernen nur mit --ja (sichert, entfernt Container und Volumes, archiviert den Ordner)"
  local instanz=${AUTOMATO_INSTANZ:-}
  if ! [[ $instanz =~ $INSTANZ_RE ]] || ! grep -qx "COMPOSE_FILE=$INSTANZ_COMPOSE_FILE" .env 2>/dev/null; then
    ende 3 "entfernen nur in einer eigenen Automato-Instanz – die Hauptinstanz wird nie entfernt"
  fi
  [ "$(basename "$PWD")" = "$instanz" ] ||
    ende 3 "Checkout liegt nicht unter <basisordner>/$instanz – nichts geändert"
  local projekt="automato-$instanz"
  lu_compose_pruefen || ende 3 "docker compose kann die Instanz nicht lesen – nichts geändert"
  [ "$(instanz_projekt)" = "$projekt" ] || ende 3 "Compose-Projekt ist nicht $projekt – nichts geändert"
  lu_sicherung_moeglich ||
    ende 3 "Keine verschlüsselte Sicherung möglich (age-Schlüssel fehlt) – nichts geändert"

  # 1. Sicherung - sie liest Datenbank und Schlüssel aus den laufenden Containern
  echo "1/3 Sicherung der Instanz $instanz nach $BACKUP_DIR"
  gesund_kurz
  if [ "$S_GESUND" != true ]; then
    if ! $COMPOSE "${PROFILES[@]}" up -d --no-build || ! lu_health; then
      echo "HINWEIS: Instanz startet nicht gesund – Sicherung versucht es trotzdem"
    fi
  fi
  S_GESUND=""
  lu_sicherung || ende 2 "Sicherung fehlgeschlagen – nichts gelöscht, Instanz $instanz bleibt"
  local dump
  dump=$(basename "$LU_DUMP")

  # 2. Container und Volumes nur dieses Projekts
  echo "2/3 Container und Volumes entfernen ($projekt)"
  $COMPOSE "${PROFILES[@]}" down -v --remove-orphans ||
    echo "HINWEIS: docker compose down meldet einen Fehler – Reste einzeln"
  instanz_reste_entfernen "$projekt"
  if instanz_reste "$projekt"; then
    ende 2 "Instanz $instanz nur teilweise entfernt (Sicherung $dump) – von Hand: docker ps -a bzw. docker volume ls --filter label=com.docker.compose.project=$projekt"
  fi
  instanz_namen_entfernen "$projekt"

  # 3. Ordner archivieren (Sicherung und .env bleiben - ohne .env ist die Sicherung des Audits
  # nicht lesbar)
  echo "3/3 Ordner archivieren"
  local basis zeit ziel
  basis=$(dirname "$PWD")
  zeit=$(date -u +%Y%m%dT%H%M%SZ)
  ziel="$basis/archiv/$instanz-$zeit"
  if ! install -d -m 700 "$basis/archiv" || ! mv "$PWD" "$ziel"; then
    ende 2 "Container und Volumes entfernt (Sicherung $dump), Ordner nicht archiviert – von Hand nach $basis/archiv/ verschieben"
  fi
  cd "$ziel" || true
  ende 0 "Instanz $instanz entfernt: Sicherung $dump, Container und Volumes gelöscht, Ordner unter archiv/$instanz-$zeit"
}
