#!/usr/bin/env bash
# Betrieb aus dem Cockpit (1.15.0, ADR 0060, Vertrag betrieb/v1): einziger Einstiegspunkt fuer
# den Host-Agenten des Cockpits. Fortschritt auf stderr, auf stdout genau eine Zeile JSON.
#
#   deploy/betrieb.sh stand            Version, Commit, Quelle (images|git), Health, ob zurueck geht;
#                                      Herkunft (1.18.1): Branch bzw. losgeloest und Kanal (dev,
#                                      latest, stable bzw. unbekannt) - das Cockpit waehlt damit den
#                                      Kanal ohne Zutun
#   deploy/betrieb.sh sichern          verschluesselte Sicherung wie vor einem Update
#   deploy/betrieb.sh aktualisieren --version <v> --manifest <manifest.json> --signatur <manifest.sig>
#                                   [--bundle <datei>]
#                                      Manifest und Signatur pruefen -> Sicherung -> Commit des
#                                      Manifests (losgeloest) -> Images nur per Digest -> starten
#                                      (ohne Bau) -> Health bis 5 min -> sonst selbst zurueck
#                                      (Code, Images und Datenbank aus der eben gemachten Sicherung)
#                                      Mit --bundle (1.18.0, Repo privat): den Commit aus dem
#                                      git-Bundle des Cockpits statt von origin - origin wird nie
#                                      kontaktiert; Bundle defekt bzw. ohne den Commit -> Exit 3
#                                      Nie aelter (1.18.1): gleicher Versionskern -> git-Abstammung,
#                                      wenn beide Commits da sind (Ziel Vorfahre -> Exit 3, nur
#                                      ueber zurueck; getrennte Linien -> Exit 3)
#   deploy/betrieb.sh zurueck          auf den Stand vor dem letzten Update (einmal)
#   deploy/betrieb.sh neustart         Container neu starten (ohne Bau, ohne neue Images) + Health
#
# Eigene Automato-Instanz je Kunde (1.16.0, ADR 0061, deploy/lib-instanz.sh) - im frischen
# Checkout <basisordner>/<instanz>, den der Host-Agent auf den Commit des Manifests gestellt hat:
#   deploy/betrieb.sh installieren --instanz <n> --version <v> --manifest <m> --signatur <s>
#                                  --kunde <kurzname> --domain <domain> --issuer <realm-url>
#                                  [--kopplung <einmal-code>]
#                                      Manifest pruefen -> .env mit neuen Geheimnissen (Projekt
#                                      automato-<n>, ohne Ports, OIDC auf den Realm des Kunden) ->
#                                      Images per Digest -> starten -> Health -> Einmal-Code der
#                                      Kopplung hinterlegen; scheitert ein Schritt: aufraeumen
#   deploy/betrieb.sh entfernen --ja   nur in einer Instanz: Sicherung -> Container und Volumes
#                                      entfernen -> Ordner nach <basisordner>/archiv/<n>-<zeit>
# In einer Instanz gehen stand, sichern, aktualisieren, zurueck und neustart wie in der
# Hauptinstanz; die Antwort nennt dann zusaetzlich "instanz".
#
# Exit: 0 ok · 1 fehlgeschlagen, laeuft wieder auf dem alten Stand · 2 fehlgeschlagen, Zustand
# unklar (von Hand, die Meldung nennt den Schritt) · 3 abgelehnt, nichts geaendert · 4 belegt.
#
# Herausgeber-Schluessel (Ed25519, PEM): /etc/automato/herausgeber.pub, sonst
# deploy/release/herausgeber.pub aus dem bisherigen Checkout - gelesen, bevor umgestellt wird;
# ohne Schluessel lehnt aktualisieren ab. Einrichten: deploy/release/README.md.
# Einstellungen (Sicherung, Profile, TLS intern) wie deploy/update.sh aus .env (deploy/env.sh);
# Sperre, Stand des letzten Updates und Rueckfall teilt es mit update.sh (deploy/lib-update.sh).
# Pull-Zugang fuer private Images: der Agent gibt ihn als DOCKER_CONFIG mit - docker erbt die
# Variable; dieses Skript setzt und nennt sie nie. stand, zurueck und neustart brauchen kein origin.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
# Der Agent gibt nur PATH, LANG und BETRIEB_AUFTRAG mit - git und docker (Anmeldung an GHCR unter
# ~/.docker) brauchen HOME
if [ -z "${HOME:-}" ]; then
  HOME=$(getent passwd "$(id -u)" | cut -d: -f6)
  export HOME
fi
# shellcheck source=deploy/env.sh
. deploy/env.sh
COMPOSE=${COMPOSE:-docker compose}
HEALTH_URL=${AUTOMATO_HEALTH_URL:-$(standard_health_url)}
BACKUP_DIR=${AUTOMATO_BACKUP_DIR:-/var/backups/automato}
HERAUSGEBER_FEST=${AUTOMATO_HERAUSGEBER_FEST:-/etc/automato/herausgeber.pub}
LU_HEALTH_VERSUCHE=${AUTOMATO_HEALTH_VERSUCHE:-150} # Health bis 5 min (Vertrag betrieb/v1)
HERAUSGEBER_REPO=deploy/release/herausgeber.pub
MANIFEST_IMAGES="$IMAGES_DATEI.manifest" # Images des Manifests, bis umgestellt ist
VERSION_RE='^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]{1,40})?$'
COMMIT_RE='^[0-9a-f]{40}$'
ZEIT_RE='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'
PROFILES=(--profile core)
# Kanal eines aus git gebauten Checkouts nach Branch (wie DEV_BRANCH in .github/workflows/images.yml)
ENTWICKLUNGS_BRANCH=claude/projekt-roadmap-planung-0mk5v0
# shellcheck source=deploy/lib-update.sh
. deploy/lib-update.sh
# shellcheck source=deploy/lib-instanz.sh
. deploy/lib-instanz.sh
BUNDLE_GIT=(git)
# shellcheck source=deploy/lib-bundle.sh
. deploy/lib-bundle.sh

AKTION="?"
S_GESUND="" # true | false | leer = nicht geprueft
ANTWORT_DATEI=$(mktemp) || exit 2
trap 'rm -f "$ANTWORT_DATEI"' EXIT

# --- Antwort (Vertrag betrieb/v1) ------------------------------------------------------------
json_text() { # $1 -> JSON-String, leer -> null
  local v=${1-}
  if [ -z "$v" ]; then printf 'null'; return; fi
  v=${v//\\/\\\\}
  v=${v//\"/\\\"}
  v=${v//[$'\001'-$'\037']/ }
  printf '"%s"' "$v"
}

passt() { [[ ${1-} =~ $2 ]] && printf '%s' "$1"; } # Wert nur, wenn er dem Muster folgt

herkunft_erheben() { # Branch (oder losgeloest) und Kanal (1.18.1) - das Cockpit waehlt damit
  # den Kanal ohne Zutun. Kanal: 1. Manifest der gepinnten Images, 2. Versions-Suffix (-dev. bzw.
  # -latest., X.Y.Z aus dem Manifest = stable), 3. Branch (main = latest, Entwicklungs-Branch =
  # dev), 4. Tag v<VERSION> auf dem losgeloesten Commit = stable; sonst unbekannt
  local branch k=""
  S_BRANCH="" S_LOSGELOEST=true S_HERKUNFT_KANAL=unbekannt S_HERKUNFT_AUS=""
  if branch=$(git symbolic-ref --quiet --short HEAD 2>/dev/null) && [ -n "$branch" ]; then
    S_BRANCH=${branch:0:200} S_LOSGELOEST=false
  fi
  if [ -f "$IMAGES_DATEI" ]; then
    k=$(passt "${AUTOMATO_MANIFEST_KANAL:-}" '^(stable|latest|dev)$') && S_HERKUNFT_AUS=manifest
    if [ -z "$k" ]; then
      case $S_VERSION in
        *-dev.*) k=dev ;;
        *-latest.*) k=latest ;;
        *-* | "") ;;
        *) k=stable ;;
      esac
      [ -z "$k" ] || S_HERKUNFT_AUS=version
    fi
  fi
  if [ -z "$k" ] && [ -n "$S_BRANCH" ]; then
    case $S_BRANCH in
      main) k=latest ;;
      "$ENTWICKLUNGS_BRANCH") k=dev ;;
    esac
    [ -z "$k" ] || S_HERKUNFT_AUS=branch
  fi
  if [ -z "$k" ] && [ "$S_LOSGELOEST" = true ] && [[ $S_VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] &&
    git tag --points-at HEAD 2>/dev/null | grep -qxF "v$S_VERSION"; then
    k=stable S_HERKUNFT_AUS=tag
  fi
  [ -z "$k" ] || S_HERKUNFT_KANAL=$k
}

stand_erheben() {
  S_SICHERUNG=$(lu_letzte_sicherung)
  S_VERSION=$(passt "$(lu_laufende_version)" "$VERSION_RE")
  S_COMMIT=$(passt "$(git rev-parse HEAD 2>/dev/null)" "$COMMIT_RE")
  S_QUELLE=git S_KANAL=""
  if [ -f "$IMAGES_DATEI" ]; then
    S_QUELLE=images
    S_KANAL=$(passt "${AUTOMATO_MANIFEST_KANAL:-}" '^(stable|latest|dev)$')
  fi
  herkunft_erheben
  S_ZURUECK=false S_VORHER=null
  [ "${NOTFALL:-}" != 1 ] || return 0 # nach einem Abbruch nur das Sichere
  if lu_zurueck_moeglich; then S_ZURUECK=true; fi
  if lu_stand_lesen; then
    local v c z
    v=$(passt "${prev_version:-}" "$VERSION_RE")
    c=$(passt "${prev_rev:-}" "$COMMIT_RE")
    z=$(passt "${at:-}" "$ZEIT_RE")
    if [ -n "$v$c" ]; then
      S_VORHER="{\"version\":$(json_text "$v"),\"commit\":$(json_text "$c"),\"zeit\":$(json_text "$z")}"
    fi
  fi
}

ende() { # $1 Exit-Code, $2 Meldung (fester Text, hoechstens 300 Zeichen) - Antwort und Ende
  local erfolg=false meldung=${2:0:300}
  [ "$1" = 0 ] && erfolg=true
  stand_erheben
  printf '{"format":"betrieb/v1","produkt":"automato","aktion":%s,"ok":%s,"meldung":%s,%s%s}\n' \
    "$(json_text "$AKTION")" "$erfolg" "$(json_text "$meldung")" \
    "$(printf '"stand":{"version":%s,"commit":%s,"quelle":%s,"kanal":%s,"gesund":%s,"zurueck_moeglich":%s,"vorher":%s,"letzte_sicherung":%s,"herkunft":{"branch":%s,"losgeloest":%s,"kanal":%s,"aus":%s}}' \
      "$(json_text "$S_VERSION")" "$(json_text "$S_COMMIT")" "$(json_text "$S_QUELLE")" \
      "$(json_text "$S_KANAL")" "${S_GESUND:-null}" "$S_ZURUECK" "$S_VORHER" \
      "$(json_text "$S_SICHERUNG")" "$(json_text "$S_BRANCH")" "$S_LOSGELOEST" \
      "$(json_text "$S_HERKUNFT_KANAL")" "$(json_text "$S_HERKUNFT_AUS")")" "$(instanz_antwort)" >"$ANTWORT_DATEI"
  exit "$1"
}

gesund_kurz() { # ein Versuch, fuer stand (schnell)
  if health_curl -fsS -m 5 "$HEALTH_URL" 2>/dev/null | grep -q '"status":"ok"'; then
    S_GESUND=true
  else
    S_GESUND=false
  fi
}

lokal_geaendert() { [ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]; }

herausgeber() { # erster vorhandener Schluessel: fest auf dem Host, sonst aus dem Checkout
  local datei
  for datei in "$HERAUSGEBER_FEST" "$HERAUSGEBER_REPO"; do
    if [ -s "$datei" ]; then
      printf '%s' "$datei"
      return 0
    fi
  done
  return 1
}

stand_wiederherstellen() { # Stand des letzten erfolgreichen Updates nach einem Rueckfall
  if [ -f "$STATE.alt" ]; then mv -f "$STATE.alt" "$STATE"; else rm -f "$STATE"; fi
}

# --- Aktionen --------------------------------------------------------------------------------
stand() {
  gesund_kurz
  local quelle=git
  [ -f "$IMAGES_DATEI" ] && quelle="images${AUTOMATO_MANIFEST_KANAL:+, $AUTOMATO_MANIFEST_KANAL}"
  local health="Health ok"
  [ "$S_GESUND" = true ] || health="Health nicht ok"
  ende 0 "$(lu_laufende_version) ($quelle), $health"
}

sichern() {
  lu_sicherung_moeglich ||
    ende 3 "Keine verschlüsselte Sicherung möglich (age-Schlüssel fehlt) – deploy/aufbau.sh backup-schluessel"
  lu_compose_pruefen || ende 3 "docker compose kann das Projekt nicht lesen – deploy/aufbau.sh pruefen"
  echo "Sicherung nach $BACKUP_DIR"
  lu_sicherung || ende 2 "Sicherung fehlgeschlagen – Protokoll des Agenten bzw. deploy/aufbau.sh pruefen"
  ende 0 "Sicherung fertig"
}

abstammung_ablehnen() { # $1 Ergebnis von lu_abstammung, $2 neue, $3 laufende Version, $4 Zusatz
  case $1 in
    aelter) ende 3 "$2 ist älter als $3 – zurück nur über die Aktion zurueck" ;;
    getrennt) ende 3 "$2 liegt auf einer anderen Linie als $3 (weder Vorfahre noch Nachfolger) – $4" ;;
  esac
}

datei_ok() { # vorhanden, nicht leer, hoechstens 64 KiB
  [ -n "${1-}" ] && [ -f "$1" ] && [ -s "$1" ] && [ "$(stat -c %s "$1" 2>/dev/null || echo 0)" -le 65536 ]
}

dienst_variable() { # web-ui -> AUTOMATO_IMAGE_WEB_UI
  local d=${1^^}
  printf 'AUTOMATO_IMAGE_%s' "${d//-/_}"
}

aktualisieren() {
  local version="" manifest="" signatur="" bundle="" mit_bundle=0
  while [ $# -gt 0 ]; do
    case $1 in
      --version | --manifest | --signatur | --bundle) [ $# -ge 2 ] || ende 3 "Angabe ohne Wert" ;;
      --ref) ende 3 "Automato nimmt --version, --manifest und --signatur (kein --ref)" ;;
      *) ende 3 "Unbekannte Angabe – erwartet --version, --manifest, --signatur und ggf. --bundle" ;;
    esac
    case $1 in
      --version) version=$2 ;;
      --manifest) manifest=$2 ;;
      --signatur) signatur=$2 ;;
      --bundle) bundle=$2 mit_bundle=1 ;;
    esac
    shift 2
  done
  [[ $version =~ $VERSION_RE ]] || ende 3 "--version fehlt bzw. ist ungültig"
  datei_ok "$manifest" || ende 3 "Manifest fehlt, ist leer oder grösser als 64 KiB – nichts geändert"
  datei_ok "$signatur" || ende 3 "Signatur fehlt bzw. ist ungültig – nichts geändert"
  if [ "$mit_bundle" = 1 ]; then
    bundle=$(bundle_datei "$bundle") || ende 3 "Bundle fehlt bzw. ist leer – nichts geändert"
  fi
  command -v python3 >/dev/null 2>&1 || ende 3 "python3 fehlt – nichts geändert"

  # 1. Manifest pruefen - mit dem Schluessel und dem Skript des bisherigen Checkouts
  local schluessel
  schluessel=$(herausgeber) ||
    ende 3 "Herausgeber-Schlüssel fehlt (/etc/automato/herausgeber.pub bzw. $HERAUSGEBER_REPO) – nichts geändert"
  echo "Manifest pruefen ($schluessel)"
  local pruefung rc=0
  pruefung=$(python3 -I scripts/release_manifest.py pruefen --manifest "$manifest" \
    --signatur "$signatur" --schluessel "$schluessel" --produkt automato --version "$version") || rc=$?
  case $rc in
    0) ;;
    4) ende 3 "Signatur des Manifests fehlt bzw. ist ungültig – nichts geändert" ;;
    5) ende 3 "Herausgeber-Schlüssel ungültig (Ed25519, PEM) bzw. openssl fehlt – nichts geändert" ;;
    6) ende 3 "Manifest passt nicht zum Auftrag (Produkt bzw. Version) – nichts geändert" ;;
    *) ende 3 "Manifest ungültig – nichts geändert" ;;
  esac
  local m_kanal="" m_commit="" zeile name wert
  local -a images=()
  while IFS= read -r zeile; do
    name=${zeile%%=*} wert=${zeile#*=}
    case $name in
      kanal) m_kanal=$wert ;;
      commit) m_commit=$wert ;;
      image.*)
        [[ $wert =~ ^[a-z0-9./:_-]+@sha256:[0-9a-f]{64}$ ]] || ende 3 "Manifest ungültig – nichts geändert"
        images+=("$(dienst_variable "${name#image.}")=$wert")
        ;;
    esac
  done <<<"$pruefung"
  if ! [[ $m_commit =~ $COMMIT_RE && $m_kanal =~ ^(stable|latest|dev)$ ]] || [ "${#images[@]}" -eq 0 ]; then
    ende 3 "Manifest ungültig – nichts geändert"
  fi

  # Version: gleich -> nichts zu tun, aelter -> nur ueber zurueck
  local laufend laufend_commit vergleich
  laufend=$(passt "$(lu_laufende_version)" "$VERSION_RE") || laufend="?"
  laufend_commit=$(git rev-parse HEAD 2>/dev/null) || ende 3 "Kein git-Checkout – nichts geändert"
  vergleich=$(python3 -I scripts/release_manifest.py vergleichen "$version" "$m_commit" "$laufend" \
    "$laufend_commit") || ende 3 "Versionen nicht vergleichbar – nichts geändert"
  case $vergleich in
    gleich) gesund_kurz; ende 0 "schon auf $version" ;;
    aelter) ende 3 "$version ist älter als $laufend – zurück nur über die Aktion zurueck" ;;
    neuer) ;;
    *) ende 3 "Versionen nicht vergleichbar – nichts geändert" ;;
  esac
  lokal_geaendert && ende 3 "Lokale Änderungen im Checkout – erst sichern bzw. verwerfen (git status)"
  if [ "$mit_bundle" = 1 ]; then
    # Repo privat: Code nur aus dem Bundle des Cockpits, nie von origin - geprueft vor der
    # Sicherung (nur Objekte im Repo, nichts umgestellt)
    echo "Code aus dem Bundle des Cockpits pruefen ($m_commit, ohne origin)"
    bundle_holen "$bundle" "$m_commit" </dev/null || ende 3 "$BUNDLE_FEHLER – nichts geändert"
  fi
  # Gleicher Kern, anderer Commit (1.18.1): nie aelter, keine fremde Linie - entscheidet die
  # Abstammung, wenn beide Commits da sind (Checkout bzw. eben aus dem Bundle); sonst wie bisher
  local gleicher_kern=0 abstammung=unbekannt
  [ "${version%%-*}" != "${laufend%%-*}" ] || gleicher_kern=1
  if [ "$gleicher_kern" = 1 ]; then
    abstammung=$(lu_abstammung "$laufend_commit" "$m_commit")
    abstammung_ablehnen "$abstammung" "$version" "$laufend" "nichts geändert"
  fi
  lu_compose_pruefen || ende 3 "docker compose kann das Projekt nicht lesen – deploy/aufbau.sh pruefen"
  lu_sicherung_moeglich ||
    ende 3 "Keine verschlüsselte Sicherung möglich (age-Schlüssel fehlt) – deploy/aufbau.sh backup-schluessel"

  # 2. Sicherung, neuen Stand holen - bis hier ist nichts umgestellt
  echo "1/5 Sicherung"
  lu_sicherung || ende 1 "Sicherung fehlgeschlagen – nichts umgestellt, läuft auf $laufend"
  echo "2/5 Code holen ($m_commit)"
  if [ "$mit_bundle" = 1 ]; then
    echo "    aus dem Bundle (geprueft)"
  else
    git fetch --quiet --tags origin || ende 1 "Code nicht geholt (git fetch) – nichts umgestellt, läuft auf $laufend"
    if ! git cat-file -e "$m_commit^{commit}" 2>/dev/null; then
      git fetch --quiet origin "$m_commit" 2>/dev/null || true
      git cat-file -e "$m_commit^{commit}" 2>/dev/null ||
        ende 1 "Commit des Manifests nicht im Repo – nichts umgestellt, läuft auf $laufend"
    fi
    if [ "$gleicher_kern" = 1 ] && [ "$abstammung" = unbekannt ]; then
      # erst jetzt im Repo: Abstammung nachholen, bevor etwas umgestellt ist
      abstammung=$(lu_abstammung "$laufend_commit" "$m_commit")
      abstammung_ablehnen "$abstammung" "$version" "$laufend" "nichts umgestellt, läuft auf $laufend"
    fi
  fi
  echo "3/5 Images holen (nur per Digest, kein Bau)"
  printf '%s\n' "AUTOMATO_MANIFEST_VERSION=$version" "AUTOMATO_MANIFEST_KANAL=$m_kanal" \
    "AUTOMATO_MANIFEST_COMMIT=$m_commit" "${images[@]}" >"$MANIFEST_IMAGES" ||
    ende 1 "Images nicht vorgemerkt – nichts umgestellt, läuft auf $laufend"
  if ! lu_images_holen "$MANIFEST_IMAGES"; then
    rm -f "$MANIFEST_IMAGES"
    ende 1 "Images nicht geholt (Pull-Zugang aus dem Cockpit, GHCR öffentlich bzw. docker login ghcr.io?) – nichts umgestellt, läuft auf $laufend"
  fi

  # 3. Umstellen: Stand fuer zurueck festhalten, Commit und Images, starten
  echo "4/5 Umstellen: $laufend -> $version ($m_commit)"
  if [ -f "$STATE" ]; then cp -p "$STATE" "$STATE.alt"; else rm -f "$STATE.alt"; fi
  if ! lu_stand_schreiben "$laufend_commit" "$laufend"; then
    rm -f "$MANIFEST_IMAGES"
    stand_wiederherstellen
    ende 1 "Stand für zurück nicht geschrieben – nichts umgestellt, läuft auf $laufend"
  fi
  if ! git checkout --quiet --detach "$m_commit"; then
    rm -f "$MANIFEST_IMAGES"
    stand_wiederherstellen
    ende 1 "Checkout von $m_commit gescheitert – nichts umgestellt, läuft auf $laufend"
  fi
  local gestartet=0
  if lu_images_schreiben <"$MANIFEST_IMAGES"; then
    rm -f "$MANIFEST_IMAGES"
    lu_images_lokal_markieren
    if [ "${AUTOMATO_PULL_IMAGES:-1}" != "0" ]; then
      # Fremd-Images (Postgres, Redis, Caddy, ...) im festen Tag aktuell wie update.sh
      $COMPOSE "${PROFILES[@]}" pull --quiet --ignore-buildable ||
        echo "HINWEIS: Fremd-Images nicht geholt – weiter mit den vorhandenen"
    fi
    $COMPOSE "${PROFILES[@]}" up -d --no-build && gestartet=1
  fi

  # 4. Health - sonst selbst zurueck (Code, Images, Datenbank)
  echo "5/5 Health (bis 5 min)"
  if [ "$gestartet" = 1 ] && lu_health; then
    lu_stand_ok
    rm -f "$STATE.alt"
    S_GESUND=true
    # alte, nicht mehr benutzte Image-Staende (ohne Tag) freigeben
    docker image prune -f >/dev/null 2>&1 || true
    ende 0 "$laufend → $version, Health ok"
  fi
  echo "FEHLER: $version startet nicht bzw. meldet nicht 'ok' - Rueckfall auf $laufend" >&2
  rc=0
  lu_rueckfall || rc=$?
  if [ "$rc" = 0 ]; then
    stand_wiederherstellen
    S_GESUND=true
    ende 1 "$version nicht gesund – zurück auf $laufend (Code, Images, Datenbank)"
  fi
  S_GESUND=false
  ende 2 "$version nicht gesund, Rückfall gescheitert – von Hand: deploy/update.sh --rollback; Logs: docker compose logs migrate orchestrator"
}

zurueck() {
  lu_zurueck_moeglich ||
    ende 3 "Kein Stand für zurück (kein Update, schon zurück oder Sicherung fehlt) – nichts geändert"
  lokal_geaendert && ende 3 "Lokale Änderungen im Checkout – erst sichern bzw. verwerfen (git status)"
  lu_compose_pruefen || ende 3 "docker compose kann das Projekt nicht lesen – deploy/aufbau.sh pruefen"
  local von ziel=${prev_version:-?} rc=0
  von=$(lu_laufende_version)
  lu_rueckfall || rc=$?
  case $rc in
    0)
      lu_stand_zurueck
      S_GESUND=true
      ende 0 "$von → $ziel zurück (Code, Images, Datenbank), Health ok"
      ;;
    1)
      S_GESUND=false
      ende 2 "Zurück auf $ziel, aber Health nicht ok – Logs: docker compose logs migrate orchestrator"
      ;;
    *) ende 2 "Zurück auf $ziel gescheitert – von Hand: deploy/update.sh --rollback" ;;
  esac
}

neustart() {
  lu_compose_pruefen || ende 3 "docker compose kann das Projekt nicht lesen – deploy/aufbau.sh pruefen"
  echo "Container neu starten"
  if ! $COMPOSE "${PROFILES[@]}" restart; then
    $COMPOSE "${PROFILES[@]}" up -d --no-build ||
      ende 2 "Neustart gescheitert – docker compose ps bzw. logs prüfen"
  fi
  if lu_health; then
    S_GESUND=true
    ende 0 "Neu gestartet, Health ok"
  fi
  S_GESUND=false
  ende 2 "Neu gestartet, Health nicht ok – Logs: docker compose logs migrate orchestrator"
}

main() {
  local aktion=${1:-}
  shift || true
  case $aktion in
    stand | sichern | aktualisieren | zurueck | neustart | installieren | entfernen) ;;
    *) ende 3 "Unbekannte Aktion – stand, sichern, aktualisieren, zurueck, neustart, installieren oder entfernen" ;;
  esac
  if [[ ${BETRIEB_AUFTRAG:-} =~ ^[0-9A-Za-z-]{1,64}$ ]]; then
    echo "Auftrag $BETRIEB_AUFTRAG: $aktion"
  fi
  local rc=0
  lu_sperren || rc=$?
  case $rc in
    0) ;;
    2) ende 3 "flock fehlt (util-linux) – nichts geändert" ;;
    *) ende 4 "Belegt – auf diesem Checkout läuft schon eine Aktion" ;;
  esac
  compose_profile || ende 3 "Profile: COMPOSE_PROFILES fehlt – deploy/aufbau.sh env ordnen"
  case $aktion in
    stand) stand ;;
    sichern) sichern ;;
    aktualisieren) aktualisieren "$@" ;;
    zurueck) zurueck ;;
    neustart) neustart ;;
    installieren) installieren "$@" ;;
    entfernen) entfernen "$@" ;;
  esac
}

case ${1:-} in
  stand | sichern | aktualisieren | zurueck | neustart | installieren | entfernen) AKTION=$1 ;;
  *) [[ ${1:-} =~ ^[a-z_-]{1,30}$ ]] && AKTION=$1 ;;
esac
# Die Aktion laeuft in einer Subshell mit stdout auf stderr: kein Programm (docker, git, ...)
# erbt die echte Standardausgabe - dort steht nur die Antwort.
( main "$@" ) >&2
rc=$?
if [ ! -s "$ANTWORT_DATEI" ]; then # unerwarteter Abbruch: trotzdem eine Antwort
  (
    NOTFALL=1
    ende 2 "Unerwarteter Abbruch (Exit $rc) – Protokoll des Agenten bzw. deploy/aufbau.sh pruefen"
  ) >&2
  rc=2
  [ -s "$ANTWORT_DATEI" ] || printf '{"format":"betrieb/v1","produkt":"automato","aktion":%s,"ok":false,"meldung":"Unerwarteter Abbruch","stand":{"version":null,"commit":null,"quelle":null,"kanal":null,"gesund":null,"zurueck_moeglich":false,"vorher":null,"letzte_sicherung":null,"herkunft":null}}\n' \
    "$(json_text "$AKTION")" >"$ANTWORT_DATEI"
fi
cat "$ANTWORT_DATEI"
exit "$rc"
