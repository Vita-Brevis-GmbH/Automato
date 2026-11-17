# shellcheck shell=bash disable=SC2034 # Variablen für die einbindenden Skripte
# Gemeinsame Teile der Einstiegspunkte nach Vertrag betrieb/v1 ausserhalb einer Automato-Installation
# (1.17.0, ADR 0062): deploy/netbox-host/betrieb.sh (Produkt `netbox-host`) und
# deploy/keycloak/betrieb.sh (Produkt `keycloak`). deploy/betrieb.sh (Automato selbst, ADR 0060)
# bleibt eigenständig.
#
# Das einbindende Skript setzt PRODUKT und CHECKOUT, definiert `main` (die Aktion) und
# `stand_json` (das Objekt "stand" der Antwort; mit NOTFALL=1 nur, was sicher feststeht) und ruft
# in seiner LETZTEN Zeile `betrieb_einstieg "$@"; exit $?` auf: die Aktion läuft dann in einer
# Subshell mit stdout auf stderr und stdin aus /dev/null – kein Programm (docker, git, …) erbt die
# echte Standardausgabe, dort steht nur die Antwort. Alles steht in Funktionen, weil
# `aktualisieren` und `zurueck` den Checkout (also auch dieses Skript) austauschen, während es läuft.

BETRIEB_VERSION_RE='^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]{1,40})?$'
BETRIEB_COMMIT_RE='^[0-9a-f]{40}$'
BETRIEB_ZEIT_RE='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'
AKTION="?"
NOTFALL=0
ANTWORT_DATEI=""

# Der Agent gibt nur PATH, LANG und BETRIEB_AUFTRAG mit – git und docker brauchen HOME; git darf
# nie nach Zugangsdaten fragen
if [ -z "${HOME:-}" ]; then
  HOME=$(getent passwd "$(id -u)" 2>/dev/null | cut -d: -f6)
  HOME=${HOME:-/root}
fi
export HOME
export GIT_TERMINAL_PROMPT=0

# --- Antwort (stdout, genau eine Zeile) --------------------------------------------------------
json_text() { # $1 -> JSON-Zeichenkette, leer -> null
  local v=${1-}
  if [ -z "$v" ]; then
    printf 'null'
    return 0
  fi
  v=${v//\\/\\\\}
  v=${v//\"/\\\"}
  v=${v//[$'\001'-$'\037']/ }
  printf '"%s"' "$v"
}

json_bool() { case ${1-} in true | false) printf '%s' "$1" ;; *) printf 'null' ;; esac; }

passt() { [[ ${1-} =~ $2 ]] && printf '%s' "$1"; } # Wert nur, wenn er dem Muster folgt

ende() { # $1 Exit-Code, $2 Meldung (fester Text des Produkts, höchstens 300 Zeichen) – Antwort, Ende
  local LC_ALL=C.UTF-8 # Zeichen statt Bytes, auch ohne LANG (cron)
  local erfolg=false meldung=${2:0:300}
  [ "$1" = 0 ] && erfolg=true
  printf '{"format":"betrieb/v1","produkt":%s,"aktion":%s,"ok":%s,"meldung":%s,"stand":%s}\n' \
    "$(json_text "$PRODUKT")" "$(json_text "$AKTION")" "$erfolg" "$(json_text "$meldung")" \
    "$(stand_json)" >"$ANTWORT_DATEI"
  echo "Ende (Exit $1): $meldung"
  exit "$1"
}

# --- Eingaben (Vertrag betrieb/v1) ---------------------------------------------------------------
ref_gueltig() { # Buchstabe oder Ziffer zuerst, höchstens 100 Zeichen, ohne `..` und `@{`
  [[ ${1-} =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$ ]] || return 1
  case $1 in *..* | *@\{*) return 1 ;; esac
  return 0
}

kern() { local v=${1%%-*}; printf '%s' "${v#v}"; } # 1.17.0-dev.ab12cd3 -> 1.17.0

ist_aelter() { # $1 älter als $2? (Versionen nach sort -V, z. B. 4.6.10-5.0.2 < 4.7.0-5.1.0)
  local a=${1#v} b=${2#v}
  [ "$a" != "$b" ] && [ "$(printf '%s\n%s\n' "$a" "$b" | sort -V | head -1)" = "$a" ]
}

kern_aelter() { ist_aelter "$(kern "$1")" "$(kern "$2")"; } # nur der Kern X.Y.Z zählt

# --- Sperre: eine Aktion je Checkout (<checkout>/.betrieb.lock, gitignored) ----------------------
betrieb_sperren() { # 0 = gehalten bis zum Ende, 1 = belegt, 2 = flock fehlt
  command -v flock >/dev/null 2>&1 || return 2
  exec 9>>"$CHECKOUT/.betrieb.lock" || return 1
  flock -n 9
}

# --- git im Checkout -----------------------------------------------------------------------------
# safe.directory, damit git als root auch einen Checkout eines anderen Benutzers annimmt; Sperren
# (fd 8, 9) erbt git nicht (ein `git gc --auto` im Hintergrund hielte sie sonst fest).
git_c() { git -c safe.directory="$CHECKOUT" -C "$CHECKOUT" "$@" 8>&- 9>&-; }

lokal_geaendert() { [ -n "$(git_c status --porcelain --untracked-files=no 2>/dev/null)" ]; }

checkout_version() { # $1 Commit (leer = Arbeitsstand) -> VERSION, wenn gültig
  local v
  if [ -z "${1-}" ]; then
    v=$(head -1 "$CHECKOUT/VERSION" 2>/dev/null)
  else
    v=$(git_c show "$1:VERSION" 2>/dev/null | head -1)
  fi
  passt "$v" "$BETRIEB_VERSION_RE"
}

checkout_branch() { # angehängter Branch oder leer (losgelöst)
  local b
  b=$(git_c symbolic-ref -q --short HEAD 2>/dev/null) || return 0
  if ref_gueltig "$b"; then printf '%s' "$b"; fi
}

herkunft_json() { # Objekt "herkunft" im Stand (1.18.2, wie deploy/betrieb.sh seit 1.18.1, ohne
  # Kanal): angehängter Branch des Checkouts bzw. losgelöst – das Cockpit nimmt den Branch als
  # Vorgabe seiner Quelle. Branch mit ungültigem Namen: null, aber nicht losgelöst; ohne git-Checkout
  # wie losgelöst
  local b
  if b=$(git_c symbolic-ref -q --short HEAD 2>/dev/null) && [ -n "$b" ]; then
    if ref_gueltig "$b"; then
      printf '{"branch":%s,"losgeloest":false}' "$(json_text "$b")"
    else
      printf '{"branch":null,"losgeloest":false}'
    fi
  else
    printf '{"branch":null,"losgeloest":true}'
  fi
}

nachfahre() { # $1 laufender Commit, $2 Ziel -> 0 = Ziel baut auf dem laufenden Stand auf (bzw. ist
  # er), 1 = andere Linie, 2 = nicht prüfbar (Commit fehlt im Repo). Nur mit Objekten im Repo
  # (Checkout bzw. aus dem Bundle geholt), nie mit origin
  local rc=0
  [ "$1" != "$2" ] || return 0
  git_c merge-base --is-ancestor "$1" "$2" </dev/null 2>/dev/null || rc=$?
  case $rc in
    0) return 0 ;;
    1) return 1 ;;
    *) return 2 ;;
  esac
}

ref_aufloesen() { # $1 Ref: Branch auf origin, dann Tag, dann Commit -> ZIEL_COMMIT, ZIEL_BRANCH
  local c
  ZIEL_COMMIT="" ZIEL_BRANCH=""
  if [ "$1" != HEAD ] && git_c check-ref-format --branch "$1" >/dev/null 2>&1 &&
    c=$(git_c rev-parse -q --verify "refs/remotes/origin/$1^{commit}" 2>/dev/null); then
    ZIEL_COMMIT=$c ZIEL_BRANCH=$1
    return 0
  fi
  if c=$(git_c rev-parse -q --verify "refs/tags/$1^{commit}" 2>/dev/null); then
    ZIEL_COMMIT=$c
    return 0
  fi
  if [[ $1 =~ ^[0-9a-f]{7,40}$ ]] && c=$(git_c rev-parse -q --verify "$1^{commit}" 2>/dev/null); then
    ZIEL_COMMIT=$c
    return 0
  fi
  return 1
}

umschalten() { # $1 Commit, $2 Branch (leer = losgelöst), $3 `ohne-upstream` (Stand aus dem
  # Bundle des Cockpits, 1.18.0) -> 0, wenn HEAD dort steht
  if [ -n "${2-}" ]; then
    git_c checkout -q -B "$2" "$1" || return 1
    if [ "${3-}" = ohne-upstream ]; then
      git_c branch -q --unset-upstream "$2" >/dev/null 2>&1 || true
    else
      git_c branch -q --set-upstream-to="origin/$2" "$2" >/dev/null 2>&1 || true
    fi
  else
    git_c -c advice.detachedHead=false checkout -q --detach "$1" || return 1
  fi
  [ "$(git_c rev-parse HEAD 2>/dev/null)" = "$1" ]
}

# --- Ablauf ----------------------------------------------------------------------------------------
betrieb_einstieg() {
  local rc=0
  case ${1-} in
    stand | sichern | aktualisieren | zurueck | neustart) AKTION=$1 ;;
    *) [[ ${1-} =~ ^[a-z_-]{1,30}$ ]] && AKTION=$1 ;;
  esac
  ANTWORT_DATEI=$(mktemp) || exit 2
  # shellcheck disable=SC2064 # der Name steht jetzt fest
  trap "rm -f '$ANTWORT_DATEI'" EXIT
  (main "$@") </dev/null >&2 || rc=$?
  if [ ! -s "$ANTWORT_DATEI" ]; then # unerwarteter Abbruch: trotzdem eine Antwort
    (
      NOTFALL=1
      ende 2 "Unerwarteter Abbruch (Exit $rc) – Journal des Agenten prüfen, ADR 0062"
    ) >&2
    rc=2
    [ -s "$ANTWORT_DATEI" ] ||
      printf '{"format":"betrieb/v1","produkt":%s,"aktion":%s,"ok":false,"meldung":"Unerwarteter Abbruch","stand":{"version":null,"commit":null,"quelle":null,"kanal":null,"gesund":null,"zurueck_moeglich":false,"vorher":null,"letzte_sicherung":null}}\n' \
        "$(json_text "$PRODUKT")" "$(json_text "$AKTION")" >"$ANTWORT_DATEI"
  fi
  cat "$ANTWORT_DATEI"
  return "$rc"
}

betrieb_beginn() { # Auftrag ins Journal, Sperre – sonst Antwort 3 bzw. 4
  local rc=0
  if [[ ${BETRIEB_AUFTRAG:-} =~ ^[0-9A-Za-z-]{1,64}$ ]]; then
    echo "Auftrag $BETRIEB_AUFTRAG: $PRODUKT $AKTION"
  fi
  betrieb_sperren || rc=$?
  case $rc in
    0) ;;
    2) ende 3 "flock fehlt (util-linux) – nichts geändert" ;;
    *) ende 4 "Belegt – auf diesem Checkout läuft schon eine Aktion" ;;
  esac
}
