#!/usr/bin/env bash
# Betrieb des NetBox-Hosts aus dem Cockpit (1.17.0, ADR 0062, Vertrag betrieb/v1): einziger
# Einstiegspunkt für den Host-Agenten des Cockpits (Produkt `netbox-host`) im Automato-Checkout
# des NetBox-Hosts (z. B. /opt/netbox-host/automato). Fortschritt auf stderr, auf stdout genau
# eine Zeile JSON.
#
#   betrieb.sh stand                    Version des Checkouts, NetBox-Version, Instanzen gesund,
#                                       ob zurueck geht (nur lesen)
#   betrieb.sh sichern                  alle Instanzen und die Konfiguration (netbox-host.sh sichern alle)
#   betrieb.sh aktualisieren --ref <git-ref>
#                                       Sperre → Sicherung aller Instanzen → Checkout auf die Ref
#                                       (Branch wieder angehängt, sonst losgelöst) → init → neue
#                                       NetBox-Version je Instanz (netbox-host.sh update, mit
#                                       eigener Sicherung davor) → Health aller Instanzen bis 5 min
#                                       → sonst selbst zurück: alter Checkout und die Instanzen
#                                       mit neuer NetBox aus ihrer Sicherung, Exit 1
#   betrieb.sh zurueck                  auf den Stand vor dem letzten erfolgreichen aktualisieren
#                                       (Checkout und Instanzen aus der Sicherung jenes Updates) – einmal
#   betrieb.sh neustart                 Proxy und Instanzen neu starten (ohne neue Images) + Health
#
# NetBox-Version: Vorgabe des Checkouts (netbox-version.conf); steht sie in host.conf, ist sie
# festgehalten – dann ändert aktualisieren nur das Werkzeug. Nie eine ältere NetBox als eine
# Instanz schon hat (Schema der Datenbank). Während einer Aktion ruht der Abgleich des Agenten
# (Sperre von `netbox-host.sh sync`).
#
# Exit: 0 ok · 1 fehlgeschlagen, läuft wieder auf dem alten Stand · 2 fehlgeschlagen, Zustand
# unklar (von Hand, die Meldung nennt den Schritt) · 3 abgelehnt, nichts geändert · 4 belegt.
#
# Zustand für zurueck: $NBH_DIR/betrieb/zustand (0700; Commits, Versionen, Dateinamen der
# Sicherungen, Override je Instanz – keine Geheimnisse). Für Tests ersetzbar wie netbox-host.sh:
# NBH_DIR, DOCKER, GIT (netbox-docker), AGE, AGE_KEYGEN, NBH_WAIT; dazu NBH_HEALTH_SEK,
# NBH_HEALTH_PAUSE und NBH_SYNC_WARTEN.
set -uo pipefail

SKRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd) || exit 2
CHECKOUT=$(cd "$SKRIPT_DIR/../.." && pwd) || exit 2
PRODUKT=netbox-host
NBH="$SKRIPT_DIR/netbox-host.sh"
NBH_DIR=${NBH_DIR:-/opt/netbox-host}
DOCKER=${DOCKER:-docker}
GIT=${GIT:-git}
AGE=${AGE:-age}
AGE_KEYGEN=${AGE_KEYGEN:-age-keygen}
NBH_WAIT=${NBH_WAIT:-300} # je Instanz bis 5 min nach update bzw. wiederherstellen
export NBH_DIR DOCKER GIT AGE AGE_KEYGEN NBH_WAIT
zahl() { if [[ ${1-} =~ ^[0-9]{1,5}$ ]]; then printf '%s' "$1"; else printf '%s' "$2"; fi; }
HEALTH_SEK=$(zahl "${NBH_HEALTH_SEK:-}" 300)
HEALTH_PAUSE=$(zahl "${NBH_HEALTH_PAUSE:-}" 5)
SYNC_WARTEN=$(zahl "${NBH_SYNC_WARTEN:-}" 120)
ZUSTAND="$NBH_DIR/betrieb/zustand"
TAG_RE='^[0-9A-Za-z][0-9A-Za-z._-]{0,40}$'
NB_VERSION_RE='^v?[0-9]+(\.[0-9]+)*(-[0-9A-Za-z.]{1,40})?$'
REF_RE='^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$' # Commit von netbox-docker (bzw. Tag)
NICHT_EINGERICHTET="NetBox-Host nicht eingerichtet ($NBH_DIR/host.conf fehlt) – netbox-host.sh init, ADR 0062"

# shellcheck source=deploy/lib-betrieb.sh
. "$CHECKOUT/deploy/lib-betrieb.sh" || exit 2

S_GESUND=""  # true | false | leer = nicht geprüft
GESUND_GRUND=""

# --- NetBox-Host ---------------------------------------------------------------------------------
conf_da() { [ -f "$NBH_DIR/host.conf" ]; }

conf_wert() { # $1 Schlüssel -> Wert aus host.conf (in einer Subshell geladen wie netbox-host.sh)
  conf_da || return 0
  (
    set +eu
    # shellcheck disable=SC1091
    . "$NBH_DIR/host.conf" >/dev/null 2>&1
    printf '%s' "${!1-}"
  ) 2>/dev/null
}

vorgabe_von() { sed -n "s/^$2=//p" <<<"$1" | head -1; } # $1 Inhalt netbox-version.conf, $2 Name

nbh() { bash "$NBH" "$@" </dev/null 8>&- 9>&-; } # netbox-host.sh dieses Checkouts

kunden() { # alle Kurznamen mit NetBox-Instanz (wie netbox-host.sh)
  local d
  for d in "$NBH_DIR"/kunden/*/netbox; do
    [ -d "$d" ] && basename "$(dirname "$d")"
  done
  return 0
}

nbdir() { printf '%s/kunden/%s/netbox' "$NBH_DIR" "$1"; }

nbc() { # docker compose in der Instanz eines Kunden
  local k=$1
  shift
  (cd "$(nbdir "$k")" && "$DOCKER" compose "$@") </dev/null 8>&- 9>&-
}

instanz_version() { passt "$(sed -n 's/^VERSION=//p' "$(nbdir "$1")/.env" 2>/dev/null | head -1)" "$NB_VERSION_RE"; }

instanz_ref() { # Stand von netbox-docker der Instanz (Commit), sonst der Tag aus VERSION
  local r v
  r=$(cd "$(nbdir "$1")" 2>/dev/null && $GIT rev-parse HEAD 2>/dev/null </dev/null 8>&- 9>&-)
  if [[ $r =~ $REF_RE ]]; then
    printf '%s' "$r"
    return 0
  fi
  v=$(instanz_version "$1") || return 1
  passt "${v##*-}" "$TAG_RE"
}

backup_dir() { local b; b=$(conf_wert BACKUP_DIR); printf '%s' "${b:-/var/backups/netbox-host}"; }

neueste_sicherung() { # $1 Kunde -> Pfad der jüngsten Datenbank-Sicherung
  find "$(backup_dir)/$1" -maxdepth 1 -type f -name "$1-*.sql.gz.age" -printf '%T@ %p\n' 2>/dev/null |
    sort -n | tail -1 | cut -d' ' -f2-
}

identitaet() { # age-Identität auf dem Host, die zu AGE_RECIPIENT passt -> Pfad
  local id empf r
  id=$(conf_wert AGE_IDENTITY)
  id=${id:-/root/netbox-host-backup.agekey}
  empf=$(conf_wert AGE_RECIPIENT)
  [ -r "$id" ] || return 1
  [ -n "$empf" ] || return 1
  for r in $empf; do
    if $AGE_KEYGEN -y "$id" 2>/dev/null | grep -qxF "$r"; then
      printf '%s' "$id"
      return 0
    fi
  done
  return 1
}

netbox_jetzt() { # NetBox-Version, die netbox-host.sh jetzt nimmt: host.conf, sonst dieser Checkout
  local v
  v=$(conf_wert NETBOX_VERSION)
  [ -n "$v" ] || v=$(vorgabe_von "$(cat "$SKRIPT_DIR/netbox-version.conf" 2>/dev/null)" NETBOX_VERSION)
  passt "$v" "$NB_VERSION_RE"
}

netbox_fest() { [ -n "$(conf_wert NETBOX_VERSION)$(conf_wert NETBOX_DOCKER_TAG)" ]; }

sync_sperren() { # Abgleich des Agenten ruhen lassen (seine Sperre, bis zum Ende dieses Laufs)
  exec 8>>"$NBH_DIR/.sync.lock" || return 1
  flock -w "$SYNC_WARTEN" 8
}

# --- Health ----------------------------------------------------------------------------------------
proxy_laeuft() {
  (cd "$NBH_DIR/proxy" 2>/dev/null && timeout 60 "$DOCKER" compose ps --status running --services) \
    </dev/null 2>/dev/null 8>&- 9>&- | grep -qx proxy
}

instanz_gesund() { # Login-Seite antwortet im Container (wie netbox-host.sh pruefen)
  nbc "$1" exec -T netbox curl -fsS -o /dev/null http://localhost:8080/login/ >/dev/null 2>&1
}

gesund_einmal() { # -> 0 = Proxy läuft und jede Instanz antwortet; sonst GESUND_GRUND
  local k krank="" n=0 alle=0
  GESUND_GRUND=""
  for k in $(kunden); do
    alle=$((alle + 1))
    if ! instanz_gesund "$k"; then
      krank="$krank $k"
      n=$((n + 1))
    fi
  done
  if [ -n "$krank" ]; then
    krank=${krank# }
    [ "${#krank}" -le 120 ] || krank="${krank:0:117}…"
    GESUND_GRUND="$n von $alle Instanzen nicht gesund: $krank"
  fi
  if ! proxy_laeuft; then GESUND_GRUND="Proxy läuft nicht${GESUND_GRUND:+, $GESUND_GRUND}"; fi
  [ -z "$GESUND_GRUND" ]
}

gesund_warten() { # $1 Sekunden -> 0 = gesund; setzt S_GESUND
  local sek=$1 start=$SECONDS
  echo "Health prüfen (Proxy und Instanzen, bis $sek s)"
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

# --- Zustand für zurueck ($ZUSTAND: stand + instanzen/<kunde>[.override.yml]) ---------------------
z_lesen() {
  Z_COMMIT="" Z_BRANCH="" Z_VERSION="" Z_NETBOX="" Z_ZEIT="" Z_OFFEN=0
  [ -f "$ZUSTAND/stand" ] || return 0
  local k v
  while IFS='=' read -r k v; do
    case $k in
      vorher_commit) Z_COMMIT=$(passt "$v" "$BETRIEB_COMMIT_RE") ;;
      vorher_branch) ref_gueltig "$v" && Z_BRANCH=$v ;;
      vorher_version) Z_VERSION=$(passt "$v" "$BETRIEB_VERSION_RE") ;;
      vorher_netbox) Z_NETBOX=$(passt "$v" "$NB_VERSION_RE") ;;
      vorher_zeit) Z_ZEIT=$(passt "$v" "$BETRIEB_ZEIT_RE") ;;
      zurueck_offen) [ "$v" = 1 ] && Z_OFFEN=1 ;;
    esac
  done <"$ZUSTAND/stand"
  return 0
}

z_schreiben() {
  install -d -m 700 "$NBH_DIR/betrieb" "$ZUSTAND" "$ZUSTAND/instanzen" || return 1
  (
    umask 077
    printf '%s\n' "# Zustand von deploy/netbox-host/betrieb.sh (ADR 0062) – keine Geheimnisse" \
      "vorher_commit=$Z_COMMIT" "vorher_branch=$Z_BRANCH" "vorher_version=$Z_VERSION" \
      "vorher_netbox=$Z_NETBOX" "vorher_zeit=$Z_ZEIT" "zurueck_offen=$Z_OFFEN" >"$ZUSTAND/stand.neu"
  ) && mv -f "$ZUSTAND/stand.neu" "$ZUSTAND/stand"
}

i_lesen() { # $1 Kunde -> I_VERSION, I_REF, I_SICHERUNG aus dem Zustand
  I_VERSION="" I_REF="" I_SICHERUNG=""
  local k v
  [ -f "$ZUSTAND/instanzen/$1" ] || return 1
  while IFS='=' read -r k v; do
    case $k in
      version) I_VERSION=$(passt "$v" "$NB_VERSION_RE") ;;
      ref) I_REF=$(passt "$v" "$REF_RE") ;;
      sicherung) [[ $v == "$(backup_dir)/$1/$1-"*.sql.gz.age ]] && I_SICHERUNG=$v ;;
    esac
  done <"$ZUSTAND/instanzen/$1"
  [ -n "$I_VERSION" ] && [ -n "$I_REF" ]
}

i_schreiben() { # $1 Kunde, $2 Version, $3 Ref, $4 Sicherung
  (
    umask 077
    printf '%s\n' "version=$2" "ref=$3" "sicherung=$4" >"$ZUSTAND/instanzen/$1.neu"
  ) && mv -f "$ZUSTAND/instanzen/$1.neu" "$ZUSTAND/instanzen/$1"
}

z_instanzen() { # Kunden mit Eintrag im Zustand
  local f
  for f in "$ZUSTAND"/instanzen/*; do
    [ -f "$f" ] || continue
    case $f in *.override.yml | *.neu) continue ;; esac
    basename "$f"
  done
  return 0
}

instanz_merken() { # $1 Kunde: Stand vor dem Update (Version, netbox-docker, Override, Sicherung)
  local k=$1 v r
  v=$(instanz_version "$k") || return 1
  r=$(instanz_ref "$k") || return 1
  if [ -f "$(nbdir "$k")/docker-compose.override.yml" ]; then
    (umask 077 && cp "$(nbdir "$k")/docker-compose.override.yml" "$ZUSTAND/instanzen/$k.override.yml") ||
      return 1
  fi
  i_schreiben "$k" "$v" "$r" "$(neueste_sicherung "$k")"
}

zurueck_moeglich() { # true/false: Stand für zurueck vollständig und noch nicht benutzt
  local k
  z_lesen
  [ "$Z_OFFEN" = 1 ] || return 1
  [ -n "$Z_COMMIT" ] || return 1
  git_c cat-file -e "$Z_COMMIT^{commit}" 2>/dev/null || return 1
  for k in $(z_instanzen); do
    [ -d "$(nbdir "$k")" ] || continue
    i_lesen "$k" || return 1
    [ -n "$I_SICHERUNG" ] || return 1
    [ -f "$I_SICHERUNG" ] || return 1
    identitaet >/dev/null || return 1
  done
  return 0
}

# --- Instanz zurück auf ihre NetBox und Datenbank vor dem Update ---------------------------------
instanz_zurueck() { # $1 Kunde -> 0 = zurück bzw. unverändert; zählt ZURUECK_N
  local k=$1 nb id dienste jetzt_v jetzt_r
  nb=$(nbdir "$k")
  if [ ! -d "$nb" ]; then
    echo "HINWEIS: $k hat keine NetBox mehr – übersprungen"
    return 0
  fi
  i_lesen "$k" || return 1
  jetzt_v=$(instanz_version "$k")
  jetzt_r=$(instanz_ref "$k")
  if [ "$jetzt_v" = "$I_VERSION" ] && [ "$jetzt_r" = "$I_REF" ]; then
    echo "$k: NetBox unverändert ($I_VERSION)"
    return 0
  fi
  if [ -z "$I_SICHERUNG" ] || [ ! -f "$I_SICHERUNG" ]; then
    echo "FEHLER: $k: Sicherung fehlt"
    return 1
  fi
  id=$(identitaet) || { echo "FEHLER: age-Identität fehlt bzw. passt nicht zu AGE_RECIPIENT"; return 1; }
  echo "$k: NetBox ${jetzt_v:-?} → $I_VERSION, Datenbank aus $(basename "$I_SICHERUNG")"
  dienste=$(nbc "$k" config --services 2>/dev/null | grep '^netbox')
  # shellcheck disable=SC2086 # Dienste der Instanz, die NetBox nutzen (netbox, netbox-worker, …)
  nbc "$k" stop $dienste >/dev/null 2>&1 || true
  (cd "$nb" && $GIT -c advice.detachedHead=false checkout -q "$I_REF") </dev/null 8>&- 9>&- || return 1
  sed -i "s|^VERSION=.*|VERSION=$I_VERSION|" "$nb/.env" || return 1
  if [ -f "$ZUSTAND/instanzen/$k.override.yml" ]; then
    cp "$ZUSTAND/instanzen/$k.override.yml" "$nb/docker-compose.override.yml" || return 1
  fi
  # erst lesen, dann leeren: die Sicherung muss sich entschlüsseln und entpacken lassen
  if ! $AGE -d -i "$id" "$I_SICHERUNG" 2>/dev/null | gunzip -t 2>/dev/null; then
    echo "FEHLER: $k: Sicherung nicht lesbar – Datenbank unverändert"
    return 1
  fi
  nbc "$k" up -d postgres >/dev/null 2>&1 || return 1
  # Tabellen der neueren NetBox weg (sonst scheitert das Einspielen an ihren Fremdschlüsseln)
  # shellcheck disable=SC2016 # Variablen des Postgres-Containers
  nbc "$k" exec -T postgres sh -c 'psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" "$POSTGRES_DB" -c "DROP SCHEMA public CASCADE" -c "CREATE SCHEMA public"' \
    >/dev/null || return 1
  ZURUECK_N=$((ZURUECK_N + 1))
  nbh wiederherstellen "$k" "$I_SICHERUNG"
}

alles_zurueck() { # $1 Commit, $2 Branch: Checkout, Instanzen, init, Health -> RF_FEHLER (leer = ok)
  local k
  RF_FEHLER="" ZURUECK_N=0
  if ! umschalten "$1" "$2"; then
    RF_FEHLER="git checkout ${1:0:12}"
    return 1
  fi
  for k in $(z_instanzen); do
    instanz_zurueck "$k" || RF_FEHLER="$RF_FEHLER NetBox $k,"
  done
  nbh init || RF_FEHLER="$RF_FEHLER netbox-host.sh init,"
  [ -z "$RF_FEHLER" ] || {
    RF_FEHLER=${RF_FEHLER# }
    RF_FEHLER=${RF_FEHLER%,}
    return 1
  }
  gesund_warten "$HEALTH_SEK" || {
    RF_FEHLER="Health: $GESUND_GRUND"
    return 1
  }
}

# --- Antwort -----------------------------------------------------------------------------------------
letzte_sicherung() { # Zeit, bis zu der jede Instanz gesichert ist (ohne Instanzen: Konfiguration)
  local k t min="" b
  b=$(backup_dir)
  for k in $(kunden); do
    t=$(find "$b/$k" -maxdepth 1 -type f -name "$k-*.sql.gz.age" -printf '%T@\n' 2>/dev/null |
      sort -n | tail -1)
    [ -n "$t" ] || return 0
    t=${t%.*}
    if [ -z "$min" ] || [ "$t" -lt "$min" ]; then min=$t; fi
  done
  if [ -z "$min" ]; then
    min=$(find "$b/_konfig" -maxdepth 1 -type f -name 'konfig-*.tar.gz.age' -printf '%T@\n' \
      2>/dev/null | sort -n | tail -1)
    min=${min%.*}
  fi
  [ -n "$min" ] || return 0
  date -u -d "@$min" +%Y-%m-%dT%H:%M:%SZ
}

stand_json() {
  local version commit zurueck=false vorher=null sicherung=""
  version=$(checkout_version "")
  commit=$(passt "$(git_c rev-parse -q --verify HEAD 2>/dev/null)" "$BETRIEB_COMMIT_RE")
  # shellcheck disable=SC2031 # NOTFALL setzt betrieb_einstieg nur für die Antwort nach einem Abbruch
  if [ "$NOTFALL" != 1 ]; then
    if zurueck_moeglich; then zurueck=true; fi
    z_lesen
    if [ -n "$Z_COMMIT" ]; then
      vorher="{\"version\":$(json_text "$Z_VERSION"),\"commit\":$(json_text "$Z_COMMIT"),\"zeit\":$(json_text "$Z_ZEIT")}"
    fi
    sicherung=$(letzte_sicherung)
  fi
  printf '{"version":%s,"commit":%s,"quelle":"git","kanal":null,"gesund":%s,"zurueck_moeglich":%s,"vorher":%s,"letzte_sicherung":%s}' \
    "$(json_text "$version")" "$(json_text "$commit")" "$(json_bool "$S_GESUND")" "$zurueck" \
    "$vorher" "$(json_text "$sicherung")"
}

checkout_text() { # 1.17.0 (main) bzw. 1.17.0 (losgelöst)
  local b
  b=$(checkout_branch)
  printf '%s (%s)' "$(checkout_version "" || echo "?")" "${b:-losgelöst}"
}

netbox_text() {
  local v
  v=$(netbox_jetzt) || v="?"
  if netbox_fest; then printf 'NetBox %s (fest in host.conf)' "$v"; else printf 'NetBox %s' "$v"; fi
}

# --- Aktionen ----------------------------------------------------------------------------------------
stand() {
  conf_da || ende 3 "$NICHT_EINGERICHTET"
  local n
  n=$(kunden | wc -l)
  if gesund_einmal; then
    S_GESUND=true
    ende 0 "$(checkout_text), $(netbox_text), $n Instanzen gesund"
  fi
  S_GESUND=false
  ende 0 "$(checkout_text), $(netbox_text), $GESUND_GRUND"
}

sichern() {
  conf_da || ende 3 "$NICHT_EINGERICHTET"
  [ -n "$(conf_wert AGE_RECIPIENT)" ] ||
    ende 3 "AGE_RECIPIENT fehlt in host.conf – keine verschlüsselte Sicherung möglich (netbox-host.sh init)"
  echo "Sicherung aller Instanzen und der Konfiguration nach $(backup_dir)"
  nbh sichern alle ||
    ende 2 "Sicherung nicht vollständig – von Hand: netbox-host.sh sichern alle (Journal des Agenten)"
  ende 0 "Sicherung von $(kunden | wc -l) Instanzen und der Konfiguration fertig"
}

aktualisieren() {
  if [ $# -ne 2 ] || [ "$1" != --ref ]; then
    ende 3 "Der NetBox-Host wird aus git aktualisiert – aktualisieren --ref <git-ref>; nichts geändert"
  fi
  local ref=$2
  if ! ref_gueltig "$ref" || [ "$ref" = HEAD ]; then
    ende 3 "Ungültige Ref – erlaubt sind Branch, Tag oder Commit (Buchstaben, Ziffern, . _ / -, ohne ..); nichts geändert"
  fi
  conf_da || ende 3 "$NICHT_EINGERICHTET"
  local alt_commit alt_branch alt_version neu_version alt_nb ziel_conf ziel_nb ziel_tag k v grund=""
  local -a neu=()
  alt_commit=$(git_c rev-parse -q --verify HEAD 2>/dev/null) || ende 3 "Kein git-Checkout in $CHECKOUT – nichts geändert"
  alt_branch=$(checkout_branch)
  alt_version=$(checkout_version "") || alt_version="?"
  lokal_geaendert && ende 3 "Lokale Änderungen im Checkout – erst sichern bzw. verwerfen (git status); nichts geändert"
  [ -n "$(conf_wert AGE_RECIPIENT)" ] ||
    ende 3 "AGE_RECIPIENT fehlt in host.conf – ohne Sicherung kein Update; nichts geändert"
  alt_nb=$(netbox_jetzt) || ende 3 "NetBox-Version des Checkouts unbekannt (netbox-version.conf) – nichts geändert"

  echo "Neuen Stand holen (git fetch origin)"
  timeout 180 git -c safe.directory="$CHECKOUT" -C "$CHECKOUT" fetch --quiet --prune --tags --force origin \
    </dev/null 8>&- 9>&- ||
    ende 3 "git fetch von origin gescheitert (Netz bzw. Deploy-Key des Hosts) – nichts geändert"
  ref_aufloesen "$ref" || ende 3 "$ref ist weder Branch auf origin noch Tag noch Commit – nichts geändert"
  neu_version=$(checkout_version "$ZIEL_COMMIT") ||
    ende 3 "$ref hat keine gültige VERSION – nichts geändert"
  if [ "$ZIEL_COMMIT" = "$alt_commit" ]; then
    S_GESUND=false
    if gesund_einmal; then S_GESUND=true; fi
    ende 0 "schon auf $alt_version"
  fi
  if git_c merge-base --is-ancestor "$ZIEL_COMMIT" "$alt_commit" 2>/dev/null ||
    { [ "$alt_version" != "?" ] && kern_aelter "$neu_version" "$alt_version"; }; then
    ende 3 "$ref ($neu_version) ist älter als $alt_version – zurück nur über die Aktion zurueck; nichts geändert"
  fi
  if [ -n "$ZIEL_BRANCH" ] && git_c rev-parse -q --verify "refs/heads/$ZIEL_BRANCH" >/dev/null 2>&1 &&
    ! git_c merge-base --is-ancestor "refs/heads/$ZIEL_BRANCH" "$ZIEL_COMMIT" 2>/dev/null; then
    ende 3 "Der lokale Branch $ZIEL_BRANCH hat Commits, die origin/$ZIEL_BRANCH nicht hat – nichts geändert"
  fi
  git_c cat-file -e "$ZIEL_COMMIT:deploy/netbox-host/betrieb.sh" 2>/dev/null ||
    ende 3 "$ref hat keinen Einstiegspunkt für das Cockpit (deploy/netbox-host/betrieb.sh) – nichts geändert"
  ziel_conf=$(git_c show "$ZIEL_COMMIT:deploy/netbox-host/netbox-version.conf" 2>/dev/null) ||
    ende 3 "$ref nennt keine NetBox-Version (deploy/netbox-host/netbox-version.conf) – nichts geändert"

  # NetBox: festgehalten in host.conf, sonst die Vorgabe des neuen Stands - nie älter als eine Instanz
  ziel_tag=$(conf_wert NETBOX_DOCKER_TAG)
  ziel_nb=$(conf_wert NETBOX_VERSION)
  [ -n "$ziel_tag" ] || ziel_tag=$(vorgabe_von "$ziel_conf" NETBOX_DOCKER_TAG)
  [ -n "$ziel_nb" ] || ziel_nb=$(vorgabe_von "$ziel_conf" NETBOX_VERSION)
  if ! [[ $ziel_tag =~ $TAG_RE && $ziel_nb =~ $NB_VERSION_RE ]]; then
    ende 3 "NetBox-Version von $ref ungültig (netbox-version.conf bzw. host.conf) – nichts geändert"
  fi
  for k in $(kunden); do
    v=$(instanz_version "$k") || ende 3 "NetBox-Version der Instanz $k unbekannt (.env) – nichts geändert"
    [ "$v" != "$ziel_nb" ] || continue
    if ist_aelter "$ziel_nb" "$v"; then
      ende 3 "NetBox $ziel_nb wäre älter als $v der Instanz $k – nichts geändert"
    fi
    neu+=("$k")
  done
  if [ "${#neu[@]}" -gt 0 ] && ! identitaet >/dev/null; then
    ende 3 "Ohne age-Identität auf dem Host (AGE_IDENTITY, passend zu AGE_RECIPIENT) kein Rückfall der Datenbanken – nichts geändert"
  fi
  sync_sperren || ende 4 "Belegt – der Abgleich des Agenten (netbox-host.sh sync) läuft noch"
  # schon vorher krank: das Update könnte nur scheitern und Datenbanken zurückholen
  if ! gesund_einmal; then
    S_GESUND=false
    ende 3 "Health schon vor dem Update nicht ok ($GESUND_GRUND) – erst richten (netbox-host.sh pruefen); nichts geändert"
  fi

  # 1. Sicherung - bis hier ist nichts umgestellt
  echo "1/4 Sicherung aller Instanzen"
  nbh sichern alle || ende 1 "Sicherung fehlgeschlagen – nichts umgestellt, läuft auf $alt_version"

  # 2. Zustand für zurueck VOR dem Umschalten (bricht der Agent ab, geht zurueck trotzdem)
  rm -rf "$ZUSTAND.alt"
  if [ -d "$ZUSTAND" ]; then mv "$ZUSTAND" "$ZUSTAND.alt" || ende 1 "Zustand nicht verschoben – nichts umgestellt"; fi
  Z_COMMIT=$alt_commit Z_BRANCH=$alt_branch Z_VERSION=$alt_version Z_NETBOX=$alt_nb
  Z_ZEIT=$(date -u +%Y-%m-%dT%H:%M:%SZ) Z_OFFEN=1
  local gemerkt=1
  z_schreiben || gemerkt=0
  for k in "${neu[@]}"; do instanz_merken "$k" || gemerkt=0; done
  if [ "$gemerkt" = 0 ]; then
    zustand_zuruecklegen
    ende 1 "Stand für zurück nicht geschrieben – nichts umgestellt, läuft auf $alt_version"
  fi

  # 3. Umschalten, init, neue NetBox je Instanz, Health
  echo "2/4 Umschalten: $alt_version → $neu_version (${ZIEL_BRANCH:-losgelöst})"
  if ! umschalten "$ZIEL_COMMIT" "$ZIEL_BRANCH"; then
    if [ "$(git_c rev-parse HEAD 2>/dev/null)" = "$alt_commit" ]; then
      zustand_zuruecklegen
      ende 1 "Checkout von $ref gescheitert – nichts umgestellt, läuft auf $alt_version"
    fi
    grund="Umschalten gescheitert"
  elif ! nbh init; then
    grund="netbox-host.sh init gescheitert"
  else
    echo "3/4 NetBox $ziel_nb für ${#neu[@]} Instanz(en)"
    for k in "${neu[@]}"; do
      nbh update "$k" || grund="Update der NetBox $k gescheitert"
      # die Sicherung, die update eben vor dem Umstellen gemacht hat (die jüngste)
      i_lesen "$k" && i_schreiben "$k" "$I_VERSION" "$I_REF" "$(neueste_sicherung "$k")"
      [ -z "$grund" ] || break
    done
    if [ -z "$grund" ]; then
      echo "4/4 Health (bis $HEALTH_SEK s)"
      gesund_warten "$HEALTH_SEK" || grund="Health nicht ok: $GESUND_GRUND"
    fi
  fi
  local nb_text="NetBox $ziel_nb unverändert"
  [ "${#neu[@]}" -eq 0 ] || nb_text="NetBox $alt_nb → $ziel_nb (${#neu[@]} Instanzen)"
  if [ -z "$grund" ]; then
    rm -rf "$ZUSTAND.alt"
    ende 0 "$alt_version → $neu_version, $nb_text, Health ok"
  fi

  # 4. Rückfall: alter Checkout, Instanzen mit neuer NetBox aus ihrer Sicherung
  echo "FEHLER: $grund – Rückfall auf $alt_version"
  if alles_zurueck "$alt_commit" "$alt_branch"; then
    zustand_zuruecklegen
    ende 1 "$neu_version gescheitert ($grund) – zurück auf $alt_version, $ZURUECK_N Instanzen aus der Sicherung, Health ok"
  fi
  S_GESUND=false
  ende 2 "$neu_version gescheitert ($grund), Rückfall gescheitert ($RF_FEHLER) – von Hand: netbox-host.sh pruefen bzw. wiederherstellen, ADR 0062"
}

zustand_zuruecklegen() { # Stand des letzten erfolgreichen Updates nach einem Rückfall
  rm -rf "$ZUSTAND"
  if [ -d "$ZUSTAND.alt" ]; then mv "$ZUSTAND.alt" "$ZUSTAND"; fi
}

zurueck() {
  conf_da || ende 3 "$NICHT_EINGERICHTET"
  zurueck_moeglich ||
    ende 3 "Kein Stand für zurück (kein Update aus dem Cockpit, schon zurück, Sicherung bzw. age-Identität fehlt) – nichts geändert"
  lokal_geaendert && ende 3 "Lokale Änderungen im Checkout – erst sichern bzw. verwerfen (git status); nichts geändert"
  sync_sperren || ende 4 "Belegt – der Abgleich des Agenten (netbox-host.sh sync) läuft noch"
  local jetzt_version jetzt_commit
  jetzt_version=$(checkout_version "") || jetzt_version="?"
  jetzt_commit=$(git_c rev-parse HEAD 2>/dev/null)
  z_lesen
  echo "Zurück: $jetzt_version → ${Z_VERSION:-?} (${Z_COMMIT:0:12})"
  if alles_zurueck "$Z_COMMIT" "$Z_BRANCH"; then
    Z_OFFEN=0
    z_schreiben || echo "HINWEIS: Zustand nicht geschrieben ($ZUSTAND)"
    ende 0 "$jetzt_version → ${Z_VERSION:-?} zurück (NetBox ${Z_NETBOX:-?}, $ZURUECK_N Instanzen aus der Sicherung), Health ok"
  fi
  S_GESUND=false
  if [ "$RF_FEHLER" = "git checkout ${Z_COMMIT:0:12}" ] &&
    [ "$(git_c rev-parse HEAD 2>/dev/null)" = "$jetzt_commit" ]; then
    ende 3 "git checkout auf ${Z_VERSION:-?} gescheitert – nichts geändert"
  fi
  ende 2 "Zurück auf ${Z_VERSION:-?} gescheitert ($RF_FEHLER) – von Hand: netbox-host.sh pruefen bzw. wiederherstellen, ADR 0062"
}

neustart() {
  conf_da || ende 3 "$NICHT_EINGERICHTET"
  sync_sperren || ende 4 "Belegt – der Abgleich des Agenten (netbox-host.sh sync) läuft noch"
  local k fehl="" n=0
  echo "Proxy und Instanzen neu starten (ohne neue Images)"
  if [ -f "$NBH_DIR/proxy/docker-compose.yml" ]; then
    (cd "$NBH_DIR/proxy" && "$DOCKER" compose restart) </dev/null 8>&- 9>&- || fehl="Proxy"
  fi
  for k in $(kunden); do
    n=$((n + 1))
    nbc "$k" restart || fehl="${fehl:+$fehl, }$k"
  done
  if gesund_warten "$HEALTH_SEK"; then
    ende 0 "Proxy und $n Instanzen neu gestartet, Health ok"
  fi
  ende 2 "Neu gestartet${fehl:+ (gescheitert: $fehl)}, Health nicht ok: $GESUND_GRUND – von Hand: netbox-host.sh pruefen"
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
  case $aktion in
    stand) stand ;;
    sichern) sichern ;;
    aktualisieren) aktualisieren "$@" ;;
    zurueck) zurueck ;;
    neustart) neustart ;;
  esac
}

betrieb_einstieg "$@"; exit $?
