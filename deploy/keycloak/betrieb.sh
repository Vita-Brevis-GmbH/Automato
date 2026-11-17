#!/usr/bin/env bash
# Betrieb von Keycloak aus dem Cockpit (1.17.0, ADR 0062, Vertrag betrieb/v1): einziger
# Einstiegspunkt für den Host-Agenten des Cockpits (Produkt `keycloak`) in einem Automato-Checkout
# auf der Keycloak-VM (z. B. /opt/automato-keycloak/automato). Fortschritt auf stderr, auf stdout
# genau eine Zeile JSON. Der Checkout liefert nur dieses Skript – der Keycloak-Stack (Keycloak,
# Postgres, Caddy; deploy/keycloak/ansible bzw. PROD-INSTALL.md) liegt in einem eigenen Ordner.
#
#   betrieb.sh stand                       Keycloak-Version (aus dem Container bzw. dem Image),
#                                          Health (Management-Port im Container), ob zurueck geht
#   betrieb.sh sichern                     pg_dump der Keycloak-Datenbank, gzip, mit age
#                                          verschlüsselt (nie Klartext auf der Platte)
#   betrieb.sh aktualisieren --version <X.Y.Z>
#                                          nie älter: Image holen und prüfen (version.txt im
#                                          Image) → Sicherung → Version in
#                                          docker-compose.override.yml (Tag mit Digest) → Keycloak
#                                          neu → Health bis 5 min → sonst selbst zurück (Image und
#                                          Datenbank aus der eben gemachten Sicherung), Exit 1
#   betrieb.sh zurueck                     auf Image und Datenbank vor dem letzten erfolgreichen
#                                          aktualisieren – einmal
#   betrieb.sh neustart                    Container des Stacks neu starten (ohne neue Images) + Health
#
# Konfiguration /etc/automato/keycloak-betrieb.conf (Zeilen NAME=Wert, gelesen, nicht ausgeführt;
# Vorlage deploy/keycloak/keycloak-betrieb.conf.example, angelegt vom Ansible-Playbook):
#   KC_DIR=/opt/automato-keycloak          Ordner des Stacks (docker-compose.yml, .env)
#   AGE_RECIPIENT=age1…                    Empfänger der Sicherung (mehrere mit Leerzeichen); fehlt
#                                          er, gilt der der Identität
#   AGE_IDENTITY=/root/automato-keycloak-backup.agekey   für den Rückfall (Datenbank einspielen)
#   BACKUP_DIR=/var/backups/automato-keycloak            BACKUP_KEEP_DAYS=14
# Ohne Konfiguration gelten die Vorgaben; ohne Stack lehnt jede Aktion ab (Exit 3).
#
# Exit: 0 ok · 1 fehlgeschlagen, läuft wieder auf dem alten Stand · 2 fehlgeschlagen, Zustand
# unklar (von Hand, die Meldung nennt den Schritt) · 3 abgelehnt, nichts geändert · 4 belegt.
# Zustand für zurueck: <KC_DIR>/.betrieb (0700; Versionen, Image, Dateiname der Sicherung, das
# frühere Override – keine Geheimnisse). Für Tests ersetzbar: KC_BETRIEB_CONF, DOCKER, AGE,
# AGE_KEYGEN, KC_HEALTH_SEK, KC_HEALTH_PAUSE.
set -uo pipefail

SKRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd) || exit 2
CHECKOUT=$(cd "$SKRIPT_DIR/../.." && pwd) || exit 2
PRODUKT=keycloak
KONF=${KC_BETRIEB_CONF:-/etc/automato/keycloak-betrieb.conf}
DOCKER=${DOCKER:-docker}
AGE=${AGE:-age}
AGE_KEYGEN=${AGE_KEYGEN:-age-keygen}
zahl() { if [[ ${1-} =~ ^[0-9]{1,5}$ ]]; then printf '%s' "$1"; else printf '%s' "$2"; fi; }
HEALTH_SEK=$(zahl "${KC_HEALTH_SEK:-}" 300)
HEALTH_PAUSE=$(zahl "${KC_HEALTH_PAUSE:-}" 5)
PFAD_RE='^/[A-Za-z0-9._/-]{1,200}$'
EMPF_RE='^age1[0-9a-z]{20,}$'
IMAGE_RE='^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,250}$'
MARKE="# Keycloak-Version aus dem Cockpit (deploy/keycloak/betrieb.sh, ADR 0062)"

# shellcheck source=deploy/lib-betrieb.sh
. "$CHECKOUT/deploy/lib-betrieb.sh" || exit 2

S_GESUND=""  # true | false | leer = nicht geprüft
S_VERSION="" # bekannt aus der Aktion
GESUND_GRUND=""

# --- Konfiguration -------------------------------------------------------------------------------
konf_wert() { # $1 Name -> Wert aus der Konfiguration (ohne Anführungszeichen) bzw. leer
  local w
  [ -f "$KONF" ] || return 0
  w=$(sed -n "s/^$1=//p" "$KONF" 2>/dev/null | tail -1)
  w=${w%\"}
  w=${w#\"}
  printf '%s' "$w"
}

pfad_wert() { # $1 Name, $2 Vorgabe -> absoluter Pfad ohne ..
  local w
  w=$(konf_wert "$1")
  if [[ $w =~ $PFAD_RE ]] && [[ $w != *..* ]]; then printf '%s' "$w"; else printf '%s' "$2"; fi
}

KC_DIR=$(pfad_wert KC_DIR /opt/automato-keycloak)
BACKUP_DIR=$(pfad_wert BACKUP_DIR /var/backups/automato-keycloak)
IDENTITAET=$(pfad_wert AGE_IDENTITY /root/automato-keycloak-backup.agekey)
BEHALTEN=$(zahl "$(konf_wert BACKUP_KEEP_DAYS)" 14)
ZUSTAND="$KC_DIR/.betrieb"
OVERRIDE="$KC_DIR/docker-compose.override.yml"
OHNE_STACK="Kein Keycloak-Stack in $KC_DIR (docker-compose.yml fehlt) – KC_DIR in $KONF, ADR 0062"

stack_da() { [ -f "$KC_DIR/docker-compose.yml" ]; }

empfaenger() { # Empfänger der Sicherung: aus der Konfiguration, sonst der der Identität
  local r alle=""
  for r in $(konf_wert AGE_RECIPIENT); do
    [[ $r =~ $EMPF_RE ]] && alle="$alle $r"
  done
  if [ -z "$alle" ] && [ -r "$IDENTITAET" ]; then
    r=$("$AGE_KEYGEN" -y "$IDENTITAET" 2>/dev/null | head -1)
    [[ $r =~ $EMPF_RE ]] && alle=$r
  fi
  [ -n "$alle" ] || return 1
  printf '%s' "${alle# }"
}

identitaet_passt() { # die Identität auf dem Host öffnet die Sicherungen
  local r e
  [ -r "$IDENTITAET" ] || return 1
  e=$(empfaenger) || return 1
  for r in $e; do
    if "$AGE_KEYGEN" -y "$IDENTITAET" 2>/dev/null | grep -qxF "$r"; then return 0; fi
  done
  return 1
}

# --- Stack -------------------------------------------------------------------------------------------
kcc() { (cd "$KC_DIR" && "$DOCKER" compose "$@") 8>&- 9>&-; } # docker compose im Stack

konfig_image() { # Image des Dienstes keycloak, wie docker compose es startet (mit Override)
  local i
  i=$(kcc config --format json 2>/dev/null | python3 -I -c '
import json, sys
print(json.load(sys.stdin)["services"]["keycloak"]["image"])' 2>/dev/null)
  passt "$i" "$IMAGE_RE"
}

repo_von() { # quay.io/keycloak/keycloak:26.0@sha256:… -> quay.io/keycloak/keycloak
  local r=${1%%@*}
  [[ ${r##*/} != *:* ]] || r=${r%:*}
  printf '%s' "$r"
}

tag_von() { local r=${1%%@*}; [[ ${r##*/} != *:* ]] || printf '%s' "${r##*:}"; }

version_aus_text() { sed -n 's/^Keycloak - Version //p' | head -1 | tr -d '\r'; }

kc_container() { kcc ps -q keycloak 2>/dev/null | head -1; } # laufender Container

laufende_version() { # aus dem laufenden Container (/opt/keycloak/version.txt)
  local id
  id=$(kc_container)
  [ -n "$id" ] || return 1
  passt "$(timeout 30 "$DOCKER" exec "$id" cat /opt/keycloak/version.txt 2>/dev/null | version_aus_text)" \
    "$BETRIEB_VERSION_RE"
}

image_version() { # $1 Image (lokal) -> Version aus dem Image selbst, ohne Keycloak zu starten
  passt "$(timeout 120 "$DOCKER" run --rm --pull never --network none --entrypoint cat "$1" \
    /opt/keycloak/version.txt 2>/dev/null | version_aus_text)" "$BETRIEB_VERSION_RE"
}

aktuelle_version() { # laufend, sonst der Tag bzw. das Image aus der Konfiguration des Stacks
  local i v
  laufende_version && return 0
  i=$(konfig_image) || return 1
  v=$(passt "$(tag_von "$i")" "$BETRIEB_VERSION_RE") && { printf '%s' "$v"; return 0; }
  image_version "$i"
}

override_eigen() { [ ! -f "$OVERRIDE" ] || [ "$(head -1 "$OVERRIDE")" = "$MARKE" ]; }

override_schreiben() { # $1 Image (mit Digest)
  (
    umask 027
    printf '%s\n' "$MARKE" \
      "# Nicht von Hand ändern: gilt vor docker-compose.yml (kc_image des Ansible-Playbooks nur ohne" \
      "# diese Datei); zurück über die Aktion zurueck." \
      "services:" "  keycloak:" "    image: $1" >"$OVERRIDE.neu"
  ) && mv -f "$OVERRIDE.neu" "$OVERRIDE"
}

# --- Health: alle Dienste laufen, Keycloak meldet „ready“ auf dem Management-Port -----------------
kc_bereit() { # $1 Container: /health/ready (9000, ältere Versionen 8080) mit HTTP 200
  # shellcheck disable=SC2016 # läuft im Container (bash mit /dev/tcp, ohne curl)
  timeout 20 "$DOCKER" exec "$1" bash -c 'for p in 9000 8080; do
    if (exec 3<>"/dev/tcp/127.0.0.1/$p" && printf "GET /health/ready HTTP/1.0\r\nHost: localhost\r\n\r\n" >&3 &&
      IFS= read -r z <&3 && [[ $z == *" 200 "* ]]) 2>/dev/null; then exit 0; fi
  done; exit 1' >/dev/null 2>&1
}

gesund_einmal() {
  local d dienste laufen id
  GESUND_GRUND=""
  dienste=$(kcc config --services 2>/dev/null)
  laufen=$(kcc ps --status running --services 2>/dev/null)
  for d in $dienste; do
    grep -qxF "$d" <<<"$laufen" || GESUND_GRUND="${GESUND_GRUND:+$GESUND_GRUND, }$d läuft nicht"
  done
  [ -z "$GESUND_GRUND" ] || return 1
  id=$(kc_container)
  if [ -z "$id" ] || ! kc_bereit "$id"; then
    GESUND_GRUND="Keycloak nicht bereit (/health/ready)"
    return 1
  fi
}

gesund_warten() { # $1 Sekunden -> 0 = gesund; setzt S_GESUND
  local sek=$1 start=$SECONDS
  echo "Health prüfen (bis $sek s)"
  while :; do
    if gesund_einmal; then
      S_GESUND=true
      return 0
    fi
    [ $((SECONDS - start + HEALTH_PAUSE)) -lt "$sek" ] || break
    sleep "$HEALTH_PAUSE"
  done
  S_GESUND=false
  echo "Health nicht ok: $GESUND_GRUND"
  return 1
}

# --- Sicherung -------------------------------------------------------------------------------------
sicherung_schreiben() { # -> SICHERUNG (Pfad); nie Klartext auf der Platte
  local e r ziel
  local -a an=()
  SICHERUNG=""
  e=$(empfaenger) || return 2
  for r in $e; do an+=(-r "$r"); done
  install -d -m 700 "$BACKUP_DIR" || return 1
  ziel="$BACKUP_DIR/keycloak-$(date -u +%Y%m%dT%H%M%SZ)-$$.sql.gz.age"
  # shellcheck disable=SC2016 # Variablen des Postgres-Containers
  if ! (umask 077 && kcc exec -T postgres sh -c 'pg_dump -c --if-exists -U "$POSTGRES_USER" "$POSTGRES_DB"' |
    gzip | "$AGE" "${an[@]}" -o "$ziel.tmp") || [ ! -s "$ziel.tmp" ]; then
    rm -f "$ziel.tmp"
    return 1
  fi
  mv -f "$ziel.tmp" "$ziel" || return 1
  SICHERUNG=$ziel
  echo "Sicherung: $(basename "$ziel")"
  # Aufbewahrung – die Sicherung für zurueck bleibt
  z_lesen
  find "$BACKUP_DIR" -maxdepth 1 -type f -name 'keycloak-*.sql.gz.age' -mtime +"$BEHALTEN" \
    ! -path "${Z_SICHERUNG:-/-}" -delete 2>/dev/null || true
}

letzte_sicherung() {
  local t
  t=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'keycloak-*.sql.gz.age' -printf '%T@\n' 2>/dev/null |
    sort -n | tail -1)
  [ -n "$t" ] || return 0
  date -u -d "@${t%.*}" +%Y-%m-%dT%H:%M:%SZ
}

db_einspielen() { # $1 Sicherung -> 0 eingespielt, 1 nichts geändert, 2 Datenbank geleert/halb
  local i=0
  # erst lesen, dann leeren: die Sicherung muss sich entschlüsseln und entpacken lassen
  identitaet_passt || return 1
  "$AGE" -d -i "$IDENTITAET" "$1" 2>/dev/null | gunzip -t 2>/dev/null || return 1
  kcc up -d postgres >/dev/null 2>&1 || return 1
  until kcc exec -T postgres pg_isready -q >/dev/null 2>&1; do
    i=$((i + 1))
    [ "$i" -lt 30 ] || return 1
    sleep 2
  done
  # shellcheck disable=SC2016 # Variablen des Postgres-Containers
  kcc exec -T postgres sh -c 'psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" "$POSTGRES_DB" -c "DROP SCHEMA public CASCADE" -c "CREATE SCHEMA public"' \
    >/dev/null || return 1
  # shellcheck disable=SC2016
  "$AGE" -d -i "$IDENTITAET" "$1" | gunzip |
    kcc exec -T postgres sh -c 'psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" "$POSTGRES_DB"' >/dev/null ||
    return 2
}

# --- Zustand für zurueck ----------------------------------------------------------------------------
z_lesen() {
  Z_VERSION="" Z_IMAGE="" Z_OVERRIDE="" Z_SICHERUNG="" Z_ZEIT="" Z_OFFEN=0
  [ -f "$ZUSTAND/stand" ] || return 0
  local k v
  while IFS='=' read -r k v; do
    case $k in
      vorher_version) Z_VERSION=$(passt "$v" "$BETRIEB_VERSION_RE") ;;
      vorher_image) Z_IMAGE=$(passt "$v" "$IMAGE_RE") ;;
      vorher_override) [[ $v =~ ^(ja|nein)$ ]] && Z_OVERRIDE=$v ;;
      sicherung) [[ $v == "$BACKUP_DIR/keycloak-"*.sql.gz.age ]] && Z_SICHERUNG=$v ;;
      vorher_zeit) Z_ZEIT=$(passt "$v" "$BETRIEB_ZEIT_RE") ;;
      zurueck_offen) [ "$v" = 1 ] && Z_OFFEN=1 ;;
    esac
  done <"$ZUSTAND/stand"
  return 0
}

z_schreiben() {
  install -d -m 700 "$ZUSTAND" || return 1
  (
    umask 077
    printf '%s\n' "# Zustand von deploy/keycloak/betrieb.sh (ADR 0062) – keine Geheimnisse" \
      "vorher_version=$Z_VERSION" "vorher_image=$Z_IMAGE" "vorher_override=$Z_OVERRIDE" \
      "sicherung=$Z_SICHERUNG" "vorher_zeit=$Z_ZEIT" "zurueck_offen=$Z_OFFEN" >"$ZUSTAND/stand.neu"
  ) && mv -f "$ZUSTAND/stand.neu" "$ZUSTAND/stand"
}

zustand_zuruecklegen() { # Stand des letzten erfolgreichen Updates nach einem Rückfall
  rm -rf "$ZUSTAND"
  if [ -d "$ZUSTAND.alt" ]; then mv "$ZUSTAND.alt" "$ZUSTAND"; fi
}

zurueck_moeglich() {
  z_lesen
  [ "$Z_OFFEN" = 1 ] || return 1
  [ -n "$Z_OVERRIDE" ] || return 1
  [ -n "$Z_SICHERUNG" ] || return 1
  [ -f "$Z_SICHERUNG" ] || return 1
  if [ "$Z_OVERRIDE" = ja ] && [ ! -f "$ZUSTAND/override.yml" ]; then return 1; fi
  identitaet_passt
}

kc_zurueck() { # Image und Datenbank aus dem Zustand -> RF_FEHLER (leer = ok)
  local rc=0
  RF_FEHLER=""
  kcc stop keycloak >/dev/null 2>&1 || true
  db_einspielen "$Z_SICHERUNG" || rc=$?
  if [ "$rc" = 1 ]; then
    # Datenbank unverändert: Keycloak läuft weiter mit dem Image, das zu ihr passt
    RF_FEHLER="Sicherung nicht lesbar bzw. Postgres nicht bereit – Datenbank unverändert"
    kcc up -d keycloak >/dev/null 2>&1 || true
    return 1
  fi
  if [ "$rc" != 0 ]; then
    RF_FEHLER="Datenbank nicht eingespielt – Keycloak bleibt gestoppt"
    return 1
  fi
  if [ "$Z_OVERRIDE" = ja ]; then
    cp "$ZUSTAND/override.yml" "$OVERRIDE" || {
      RF_FEHLER="Override nicht zurückgelegt – Keycloak bleibt gestoppt"
      return 1
    }
  else
    rm -f "$OVERRIDE"
  fi
  kcc up -d keycloak >/dev/null || {
    RF_FEHLER="Start gescheitert"
    return 1
  }
  gesund_warten "$HEALTH_SEK" || {
    RF_FEHLER="Health: $GESUND_GRUND"
    return 1
  }
}

# --- Antwort -------------------------------------------------------------------------------------------
stand_json() {
  local version=$S_VERSION zurueck=false vorher=null sicherung=""
  # shellcheck disable=SC2031 # NOTFALL setzt betrieb_einstieg nur für die Antwort nach einem Abbruch
  if [ "$NOTFALL" != 1 ]; then
    if [ -z "$version" ] && stack_da; then version=$(aktuelle_version); fi
    if zurueck_moeglich; then zurueck=true; fi
    z_lesen
    if [ -n "$Z_VERSION" ]; then
      vorher="{\"version\":$(json_text "$Z_VERSION"),\"commit\":null,\"zeit\":$(json_text "$Z_ZEIT")}"
    fi
    sicherung=$(letzte_sicherung)
  fi
  # herkunft: Branch des Automato-Checkouts, der dieses Skript liefert (1.18.2)
  printf '{"version":%s,"commit":null,"quelle":"images","kanal":null,"gesund":%s,"zurueck_moeglich":%s,"vorher":%s,"letzte_sicherung":%s,"herkunft":%s}' \
    "$(json_text "$version")" "$(json_bool "$S_GESUND")" "$zurueck" "$vorher" "$(json_text "$sicherung")" \
    "$(herkunft_json)"
}

# --- Aktionen ----------------------------------------------------------------------------------------
stand() {
  S_VERSION=$(aktuelle_version) || S_VERSION=""
  S_GESUND=false
  if gesund_einmal; then
    S_GESUND=true
    ende 0 "Keycloak ${S_VERSION:-?}, Health ok"
  fi
  ende 0 "Keycloak ${S_VERSION:-?}, Health nicht ok: $GESUND_GRUND"
}

sichern() {
  local rc=0
  echo "Sicherung der Keycloak-Datenbank nach $BACKUP_DIR"
  sicherung_schreiben || rc=$?
  case $rc in
    0) ende 0 "Sicherung der Keycloak-Datenbank fertig (verschlüsselt)" ;;
    2) ende 3 "age-Empfänger fehlt – AGE_RECIPIENT bzw. AGE_IDENTITY in $KONF; nichts gesichert" ;;
    *) ende 2 "Sicherung gescheitert – läuft postgres? (Journal des Agenten, docker compose ps in $KC_DIR)" ;;
  esac
}

aktualisieren() {
  if [ $# -ne 2 ] || [ "$1" != --version ]; then
    ende 3 "Keycloak nimmt aktualisieren --version <X.Y.Z> (Image-Tag) – nichts geändert"
  fi
  local version=$2 laufend image repo neu pin digest pv grund="" rc=0
  [[ $version =~ $BETRIEB_VERSION_RE ]] || ende 3 "--version ungültig (X.Y.Z) – nichts geändert"
  empfaenger >/dev/null || ende 3 "age-Empfänger fehlt – AGE_RECIPIENT bzw. AGE_IDENTITY in $KONF; nichts geändert"
  identitaet_passt ||
    ende 3 "Ohne age-Identität auf dem Host ($IDENTITAET, passend zum Empfänger) kein Rückfall der Datenbank – nichts geändert"
  override_eigen ||
    ende 3 "$OVERRIDE ist von Hand angelegt – Version nicht setzbar; Inhalt nach docker-compose.yml übernehmen, Datei entfernen"
  laufend=$(aktuelle_version) || ende 3 "Laufende Keycloak-Version unbekannt (Container bzw. Image) – nichts geändert"
  S_VERSION=$laufend
  if [ "$version" = "$laufend" ]; then
    S_GESUND=false
    if gesund_einmal; then S_GESUND=true; fi
    ende 0 "schon auf $version"
  fi
  kern_aelter "$version" "$laufend" &&
    ende 3 "$version ist älter als $laufend – zurück nur über die Aktion zurueck; nichts geändert"
  if ! gesund_einmal; then
    S_GESUND=false
    ende 3 "Health schon vor dem Update nicht ok ($GESUND_GRUND) – erst richten (docker compose ps/logs in $KC_DIR); nichts geändert"
  fi
  image=$(konfig_image) || ende 3 "Image von keycloak unbekannt (docker compose config in $KC_DIR) – nichts geändert"
  repo=$(repo_von "$image")
  neu="$repo:$version"

  # 1. Image holen und prüfen – bis hier ist nichts umgestellt
  echo "1/5 Image holen: $neu"
  timeout 900 "$DOCKER" pull --quiet "$neu" >/dev/null 8>&- 9>&- ||
    ende 1 "Image $neu nicht geholt (Registry erreichbar?) – nichts umgestellt, läuft auf $laufend"
  pv=$(image_version "$neu")
  [ "$pv" = "$version" ] || ende 3 "Image $neu meldet Keycloak ${pv:-?} statt $version – nichts geändert"
  digest=$("$DOCKER" image inspect -f '{{range .RepoDigests}}{{println .}}{{end}}' "$neu" 2>/dev/null |
    grep -m1 -E "^$repo@sha256:[0-9a-f]{64}$")
  pin=$neu
  [ -z "$digest" ] || pin="$neu@${digest#*@}"

  # 2. Sicherung, Zustand für zurueck (vor dem Umstellen)
  echo "2/5 Sicherung"
  sicherung_schreiben || ende 1 "Sicherung fehlgeschlagen – nichts umgestellt, läuft auf $laufend"
  rm -rf "$ZUSTAND.alt"
  if [ -d "$ZUSTAND" ]; then mv "$ZUSTAND" "$ZUSTAND.alt" || ende 1 "Zustand nicht verschoben – nichts umgestellt"; fi
  Z_VERSION=$laufend Z_IMAGE=$image Z_OVERRIDE=nein Z_SICHERUNG=$SICHERUNG Z_OFFEN=1
  Z_ZEIT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  install -d -m 700 "$ZUSTAND" || rc=1
  if [ -f "$OVERRIDE" ]; then
    Z_OVERRIDE=ja
    cp "$OVERRIDE" "$ZUSTAND/override.yml" || rc=1
  fi
  z_schreiben || rc=1
  if [ "$rc" != 0 ]; then
    zustand_zuruecklegen
    ende 1 "Stand für zurück nicht geschrieben – nichts umgestellt, läuft auf $laufend"
  fi

  # 3. Umstellen und Health
  echo "3/5 Umstellen: $laufend → $version ($pin)"
  if ! override_schreiben "$pin"; then
    grund="Override nicht geschrieben"
  elif ! kcc up -d keycloak; then
    grund="Start gescheitert"
  else
    echo "4/5 Health (bis $HEALTH_SEK s)"
    gesund_warten "$HEALTH_SEK" || grund="Health nicht ok: $GESUND_GRUND"
  fi
  if [ -z "$grund" ]; then
    rm -rf "$ZUSTAND.alt"
    S_VERSION=$version
    ende 0 "$laufend → $version, Health ok"
  fi

  # 4. Rückfall: Image und Datenbank aus der eben gemachten Sicherung
  echo "FEHLER: $grund – 5/5 Rückfall auf $laufend"
  S_VERSION=""
  if kc_zurueck; then
    zustand_zuruecklegen
    ende 1 "$version gescheitert ($grund) – zurück auf $laufend (Image, Datenbank), Health ok"
  fi
  S_GESUND=false
  ende 2 "$version gescheitert ($grund), Rückfall gescheitert ($RF_FEHLER) – von Hand: Sicherung $(basename "$Z_SICHERUNG") einspielen, ADR 0062"
}

zurueck() {
  zurueck_moeglich ||
    ende 3 "Kein Stand für zurück (kein Update aus dem Cockpit, schon zurück, Sicherung bzw. age-Identität fehlt) – nichts geändert"
  override_eigen ||
    ende 3 "$OVERRIDE ist von Hand angelegt – nichts geändert"
  local jetzt
  jetzt=$(aktuelle_version) || jetzt="?"
  echo "Zurück: $jetzt → $Z_VERSION (Datenbank aus $(basename "$Z_SICHERUNG"))"
  if kc_zurueck; then
    z_lesen
    Z_OFFEN=0
    z_schreiben || echo "HINWEIS: Zustand nicht geschrieben ($ZUSTAND)"
    S_VERSION=""
    ende 0 "$jetzt → $Z_VERSION zurück (Image, Datenbank), Health ok"
  fi
  S_GESUND=false
  ende 2 "Zurück auf $Z_VERSION gescheitert ($RF_FEHLER) – von Hand: Sicherung $(basename "$Z_SICHERUNG") einspielen, ADR 0062"
}

neustart() {
  echo "Container des Stacks neu starten (ohne neue Images)"
  local fehl=""
  kcc restart || fehl=" (docker compose restart gescheitert)"
  if gesund_warten "$HEALTH_SEK"; then
    ende 0 "Neu gestartet, Health ok"
  fi
  ende 2 "Neu gestartet$fehl, Health nicht ok: $GESUND_GRUND – von Hand: docker compose ps/logs in $KC_DIR"
}

main() {
  local aktion=${1-}
  [ $# -eq 0 ] || shift
  case $aktion in
    stand | sichern | zurueck | neustart)
      [ $# -eq 0 ] || ende 3 "$aktion nimmt keine Angaben – nichts geändert" ;;
    aktualisieren) ;;
    *) ende 3 "Unbekannte Aktion – stand, sichern, aktualisieren, zurueck oder neustart" ;;
  esac
  betrieb_beginn
  stack_da || ende 3 "$OHNE_STACK"
  case $aktion in
    stand) stand ;;
    sichern) sichern ;;
    aktualisieren) aktualisieren "$@" ;;
    zurueck) zurueck ;;
    neustart) neustart ;;
  esac
}

betrieb_einstieg "$@"; exit $?
