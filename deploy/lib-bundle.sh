# shellcheck shell=bash disable=SC2034 # BUNDLE_FEHLER und BUNDLE_KOEPFE für die einbindenden Skripte
# Code aus einem git-Bundle des Cockpits statt von origin (1.18.0, ADR 0060 und ADR 0062, je
# Nachtrag „Alles privat“; Vertrag betrieb/v1, Erweiterung `--bundle`). Die Repos sind privat, nur
# das Cockpit hat einen GitHub-Zugang: es löst die Ref auf, holt den Stand als git-Bundle und gibt
# im signierten Auftrag Commit und SHA-256 mit. Der Host-Agent lädt das Bundle über den Host-Kanal,
# prüft SHA-256 und ruft betrieb.sh mit `--bundle <datei>` auf.
#
# Mit Bundle wird origin NIE kontaktiert: `git bundle verify` (Datei und Voraussetzungen) →
# `git fetch <bundle> <commit>` → der Commit muss ein Kopf des Bundles sein bzw. von einem Kopf
# erreichbar. Bis hier ist nichts umgestellt (nur Objekte im Repo, kein Branch, kein Checkout).
#
# Eingebunden von deploy/betrieb.sh (Automato) und deploy/netbox-host/betrieb.sh. Das einbindende
# Skript setzt vorher BUNDLE_GIT (git im Checkout als Feld, Standard `git` im aktuellen Ordner).
# Die Funktionen beenden das Skript nie selbst; sie geben einen Code zurück und setzen
# BUNDLE_FEHLER (fester Text für die Meldung).

if ! declare -p BUNDLE_GIT >/dev/null 2>&1; then BUNDLE_GIT=(git); fi
BUNDLE_KOEPFE="" # git bundle list-heads: je Zeile "<commit> <name>"
BUNDLE_FEHLER=""
BUNDLE_SEK=${BUNDLE_SEK:-600} # Grenze für das Einlesen (lokale Datei, auch grosse Repos)
[[ $BUNDLE_SEK =~ ^[0-9]{1,5}$ ]] || BUNDLE_SEK=600

bundle_git() { "${BUNDLE_GIT[@]}" "$@" 8>&- 9>&-; } # Sperren (fd 8, 9) erbt git nicht

bundle_datei() { # $1 Angabe -> absoluter Pfad einer lesbaren, nicht leeren Datei (nie ein Remote-Name)
  local d=${1-} ordner
  [ -n "$d" ] && [ -f "$d" ] && [ -r "$d" ] && [ -s "$d" ] || return 1
  ordner=$(cd "$(dirname "$d")" 2>/dev/null && pwd -P) || return 1
  printf '%s/%s' "$ordner" "$(basename "$d")"
}

bundle_holen() { # $1 Bundle (absolut), $2 Commit (40 hex) -> 0 = Objekte da, Commit im Bundle
  local bundle=$1 commit=$2 kopf name
  BUNDLE_KOEPFE="" BUNDLE_FEHLER=""
  if ! bundle_git bundle verify "$bundle" >/dev/null; then
    BUNDLE_FEHLER="Bundle ungültig bzw. passt nicht zu diesem Checkout (git bundle verify)"
    return 1
  fi
  BUNDLE_KOEPFE=$(bundle_git bundle list-heads "$bundle" 2>/dev/null)
  if [ -z "$BUNDLE_KOEPFE" ]; then
    BUNDLE_FEHLER="Bundle ohne Köpfe (git bundle list-heads)"
    return 1
  fi
  if ! timeout "$BUNDLE_SEK" "${BUNDLE_GIT[@]}" fetch --quiet --no-tags "$bundle" "$commit" 8>&- 9>&- ||
    ! bundle_git cat-file -e "$commit^{commit}" 2>/dev/null; then
    BUNDLE_FEHLER="Commit ${commit:0:12} fehlt im Bundle bzw. Bundle beschädigt"
    return 1
  fi
  while read -r kopf name; do
    [[ $kopf =~ ^[0-9a-f]{40}$ ]] || continue
    if [ "$kopf" = "$commit" ] ||
      bundle_git merge-base --is-ancestor "$commit" "$kopf" </dev/null 2>/dev/null; then
      return 0
    fi
  done <<<"$BUNDLE_KOEPFE"
  BUNDLE_FEHLER="Commit ${commit:0:12} gehört nicht zum Stand des Bundles"
  return 1
}

bundle_kopf() { # $1 voller Name (refs/heads/main) -> 0, wenn das Bundle ihn als Kopf hat
  local kopf name
  while read -r kopf name; do
    [ "$name" != "$1" ] || return 0
  done <<<"$BUNDLE_KOEPFE"
  return 1
}

bundle_branch() { # $1 Ref, $2 Commit -> Name des Branches, wenn die Ref ein Branch ist; sonst
  # nichts (losgelöst): Commit bzw. Tag (im Bundle oder lokal) losgelöst, Branch nur, wenn das
  # Bundle bzw. der Checkout ihn als Branch kennt - im Zweifel losgelöst
  local ref=$1 commit=$2
  [ "$ref" != HEAD ] || return 0
  if [[ $ref =~ ^[0-9a-f]{7,40}$ && $commit == "$ref"* ]]; then return 0; fi
  bundle_git check-ref-format --branch "$ref" >/dev/null 2>&1 || return 0
  if bundle_kopf "refs/tags/$ref" ||
    bundle_git rev-parse -q --verify "refs/tags/$ref" >/dev/null 2>&1; then
    return 0
  fi
  if bundle_kopf "refs/heads/$ref" ||
    bundle_git rev-parse -q --verify "refs/heads/$ref" >/dev/null 2>&1 ||
    bundle_git rev-parse -q --verify "refs/remotes/origin/$ref" >/dev/null 2>&1; then
    printf '%s' "$ref"
  fi
  return 0
}
