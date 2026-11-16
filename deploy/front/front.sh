#!/usr/bin/env bash
# shellcheck disable=SC1111 # deutsche Anführungszeichen „…“ in Meldungen sind gewollt
# Front-Proxy der Automato-VM (1.2.0, ADR 0044 Nachtrag): Startseiten je Kunde, internes Portal und
# Weiterleitung je Werkzeug – im Proxy von Automato (Profil "proxy", deploy/proxy/Caddyfile).
#
# Adressen (<domain> = AUTOMATO_FRONT_DOMAIN, <intern> = AUTOMATO_FRONT_DOMAIN_INT):
#   <kunde>.<domain>        Startseite des Kunden (Kacheln zu seinen Werkzeugen)
#   <kunde>-nb.<domain>     NetBox des Kunden (NetBox-Host, AUTOMATO_FRONT_NETBOX) – ohne --ohne-nb
#   <kunde>-doc.<domain>    Automato (diese VM) – nur mit --doc
#   <kunde>-um.<domain>     Magister des Kunden – mit --um, Ziel AUTOMATO_FRONT_MAGISTER bzw. _UEBRIGE
#   <kunde>-cmdb.<domain>   CMDB – nur mit --cmdb und AUTOMATO_FRONT_CMDB
#   start.<intern>          Portal mit allen Kunden (nur Management-Netz)
#   <intern>                Verwaltung (Cockpit, Version 2) – bis dahin Weiterleitung aufs Portal
# Kunden-Adressen nur von der WAF und aus dem Management-Netz (AUTOMATO_FRONT_KUNDEN_ALLOW), der
# Angabe X-Forwarded-For wird nur von der WAF geglaubt; interne Adressen nur aus dem Management-Netz
# und nie über die WAF.
#
# Verwendung (im Repo-Ordner, mit sudo):
#   front.sh kunde <kunde> "<Anzeigename>" [--doc] [--um] [--cmdb] [--ohne-nb] [--realm <r>]
#                  [--zugriff ueberall|intern|"<netz> …"]           anlegen/ändern
#   front.sh link <kunde> <titel> <url> [beschreibung]               weitere Kachel auf der Startseite
#   front.sh entfernen <kunde> --ja                                 Kunde aus dem Front-Proxy nehmen
#     (kunde, link, entfernen: mit verbundenem Cockpit nur mit --trotzdem, siehe unten)
#   front.sh liste                                                  Kunden mit ihren Werkzeugen
#   front.sh erzeugen                                               alles neu erzeugen, Proxy neu laden
#   front.sh pruefen                                                Einstellungen, Zertifikate, Adressen,
#                                                                   Ziele und Wege
#   front.sh adressen                                               nur die Adressen: DNS, Proxy, Zertifikat
#   front.sh sync [--immer]                                         Kunden aus der Oberfläche übernehmen
#   front.sh agent                                                  Host-Agent einrichten (systemd)
#   front.sh tor [<realm>]                                          Anmelde-Tor: Stand bzw. Client-Secret
#   front.sh tor-empfang [neu]                                      Empfangsschlüssel für Client-Secrets
#                                                                   aus dem Cockpit: Kennung bzw. neu
#
# Seit 1.3.0 (ADR 0050) pflegt die Oberfläche die Kunden (Administration → Kunden-Portal): der
# Orchestrator schreibt den Soll-Stand nach deploy/front/state/soll.json, der Host-Agent
# (systemd, `front.sh sync`) übernimmt ihn und meldet das Ergebnis in status.json. Kunden aus der
# Oberfläche ändert nur die Oberfläche; von Hand angelegte bleiben unberührt.
#
# Seit 1.4.0 (ADR 0051) das Anmelde-Tor (AUTOMATO_FRONT_TOR=an): vor jeder Adresse eines Kunden
# und vor dem Portal fragt der Proxy oauth2-proxy (je Realm ein Container, Client automato-front),
# ob jemand angemeldet ist – sonst geht es zuerst zu Keycloak im Realm des Kunden. Danach melden
# sich die Werkzeuge über die Keycloak-Sitzung ohne zweite Maske an; NetBox, Magister und CMDB
# bekommen den Benutzer als Kopfzeile (X-Automato-User/-Realm/-Groups, nur vom Tor). Kunden ohne
# Realm bzw. ohne Client-Secret sind gesperrt; die NetBox-API mit Token bleibt erreichbar.
#
# Seit 1.5.0 der Zugriff je Kunde (Kunden-Portal bzw. --zugriff): ueberall (Vorgabe), intern (nur
# Management-Netz, nie über die WAF) oder nur aus bestimmten Netzen (Adresse des Benutzers, über
# die WAF aus X-Forwarded-For). Allen anderen antworten alle Adressen des Kunden wie eine
# unbekannte Adresse (404) – auch vor dem Anmelde-Tor und für die NetBox-API.
#
# Seit 1.11.0 (ADR 0054) ist NetBox ein wählbares Werkzeug (NB=ja|nein, --ohne-nb; fehlt der Wert
# in kunden/<k>.conf: ja) und Kunden kommen auch aus dem Cockpit (QUELLE=cockpit, über die
# Oberfläche und soll.json) – sie gelten wie Kunden aus der Oberfläche als nicht von Hand änderbar.
#
# Seit 1.13.0 (ADR 0055) liefert das Cockpit das Client-Secret je Realm versiegelt: mit dem
# Empfangsschlüssel dieser VM (tor/empfang.key, RSA 3072, nur root; angelegt, sobald das Tor an ist)
# verschlüsselt, Automato hält nur das Chiffrat (soll.json, tor_geheimnisse). Nur `sync` entschlüsselt,
# prüft (Realm eines Kunden aus dem Cockpit, Client automato-front) und übernimmt es; das Geheimnis
# erscheint nie in Ausgabe, Log oder status.json. `front.sh tor <realm>` von Hand geht weiterhin und
# gilt, bis das Cockpit ein anderes Chiffrat schickt.
#
# Seit 1.14.0 (ADR 0058) entstehen und ändern sich Kunden nur noch im Cockpit, sobald Automato mit
# einem Cockpit verbunden ist (soll.json: "verwaltung": "cockpit"): `kunde`, `link` und `entfernen`
# lehnen dann ab; nur mit ausdrücklichem --trotzdem (Notweg, mit Warnung) ändern sie von Hand – das
# Cockpit kennt solche Kunden nicht (Altlast), bis es sie übernimmt.
#
# Ebenfalls seit 1.14.0 (ADR 0059, Abbau aus dem Cockpit) sperrt `gesperrt` im Soll-Stand alle
# Adressen eines Kunden („Zugang gesperrt“, SPERRE=ja in kunden/<k>.conf; Daten bleiben), und
# `tor_entfernen` lässt `sync` das Tor-Geheimnis eines Realms entfernen, den kein Kunde mehr nutzt
# (nie den Realm von Automato, je Auftrag nur einmal: tor/<realm>.entfernt). Verwaiste Tor-Dateien
# ohne Auftrag meldet der Front-Proxy nur (status.json → ist, front.sh pruefen) – nie still gelöscht.
#
# Einstellungen: AUTOMATO_FRONT_* in .env (siehe .env.example). Kunden: deploy/front/kunden/,
# erzeugt: deploy/front/generated/, Client-Secrets und Empfangsschlüssel: deploy/front/tor/ (alles
# nicht im Repo).
# Zertifikate: deploy/proxy/pki/mgmt.crt|key (Kunden-Adressen) und mgmt-int.crt|key (intern).
# Kurzname: Kleinbuchstaben, Ziffern und Bindestrich (DNS-Label), nicht auf -nb/-doc/-um/-cmdb endend.
# Für Tests ersetzbar: DOCKER, CURL, IPTABLES, SYSTEMCTL, GETENT, FRONT_DIR, FRONT_PKI, FRONT_STATE,
# FRONT_TOR_DIR, FRONT_SYSTEMD_DIR, AUTOMATO_ENV_FILE, FRONT_LOKALE_IPS, FRONT_SKIP_ROOT=1.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
FRONT_DIR=${FRONT_DIR:-$SCRIPT_DIR}
FRONT_PKI=${FRONT_PKI:-$REPO_DIR/deploy/proxy/pki}
DOCKER=${DOCKER:-docker}
IPTABLES=${IPTABLES:-iptables}
CURL=${CURL:-curl}
GETENT=${GETENT:-getent}  # DNS dieser VM (1.9.2: pruefen, adressen)
SYSTEMCTL=${SYSTEMCTL:-systemctl}
FRONT_STATE=${FRONT_STATE:-$FRONT_DIR/state}  # Soll-Stand der Oberfläche (1.3.0)
FRONT_TOR_DIR=${FRONT_TOR_DIR:-$FRONT_DIR/tor}  # Client-Secrets des Anmelde-Tors (1.4.0, nur root)
FRONT_SYSTEMD_DIR=${FRONT_SYSTEMD_DIR:-/etc/systemd/system}
ORCH_UID=1001  # Benutzer im Orchestrator-Container (orchestrator/Dockerfile)
TOR_CLIENT=automato-front  # Client des Anmelde-Tors in jedem Realm (deploy/keycloak/make_realm.py)
TOR_IMAGE_STANDARD=quay.io/oauth2-proxy/oauth2-proxy:v7.15.6
TOR_EMPFANG_ALG=RSA-OAEP-256  # Siegel des Cockpits (1.13.0, ADR 0055): RSA-OAEP, SHA-256, MGF1-SHA-256
cd "$REPO_DIR"
# shellcheck source=deploy/env.sh
. "$REPO_DIR/deploy/env.sh"

ok() { printf '  OK      %s\n' "$*"; }
info() { printf '          %s\n' "$*"; }
warn() { printf '  WARNUNG %s\n' "$*"; WARNUNGEN=$((WARNUNGEN + 1)); }
fehler() { printf '  FEHLER  %s\n' "$*" >&2; FEHLER=$((FEHLER + 1)); }
titel() { printf '\n==> %s\n' "$*"; }
abbruch() { printf 'FEHLER: %s\n' "$*" >&2; exit 1; }
WARNUNGEN=0
FEHLER=0

root_noetig() {
  [ "${FRONT_SKIP_ROOT:-0}" = "1" ] || [ "$(id -u)" -eq 0 ] || abbruch "als root bzw. mit sudo ausführen"
}

# --- Einstellungen -----------------------------------------------------------------------------
netze() {  # Liste von Adressen/Netzen: nur Zeichen von IPv4/IPv6 und CIDR (nichts für den Caddyfile)
  [[ "$2" =~ ^[0-9A-Fa-f:./\ ]+$ ]] || abbruch "$1 ungültig: nur Adressen/Netze mit Leerzeichen getrennt ('$2')"
}

lade_conf() {
  env_aus_datei
  DOMAIN=${AUTOMATO_FRONT_DOMAIN:-}
  INTERN=${AUTOMATO_FRONT_DOMAIN_INT:-}
  WAF=${AUTOMATO_FRONT_WAF:-}
  MGMT=${AUTOMATO_FRONT_MGMT:-}
  NETBOX=${AUTOMATO_FRONT_NETBOX:-}
  NETBOX_CA=${AUTOMATO_FRONT_NETBOX_CA:-}
  MAGISTER=${AUTOMATO_FRONT_MAGISTER:-}
  CMDB=${AUTOMATO_FRONT_CMDB:-}
  [ -n "$DOMAIN" ] || abbruch "AUTOMATO_FRONT_DOMAIN fehlt in .env (z. B. mgmt.example.ch)"
  [[ "$DOMAIN" =~ ^[a-z0-9.-]+$ ]] || abbruch "AUTOMATO_FRONT_DOMAIN ungültig ('$DOMAIN')"
  [ -z "$INTERN" ] || [[ "$INTERN" =~ ^[a-z0-9.-]+$ ]] || abbruch "AUTOMATO_FRONT_DOMAIN_INT ungültig ('$INTERN')"
  [ -n "$MGMT" ] || abbruch "AUTOMATO_FRONT_MGMT fehlt in .env (Management-Netz)"
  netze AUTOMATO_FRONT_MGMT "$MGMT"
  [ -z "$WAF" ] || netze AUTOMATO_FRONT_WAF "$WAF"
  # Kunden-Adressen: WAF und Management-Netz (zum Testen vor der Umstellung); nie leer = nie offen
  KUNDEN_ALLOW=${AUTOMATO_FRONT_KUNDEN_ALLOW:-"$WAF $MGMT"}
  netze AUTOMATO_FRONT_KUNDEN_ALLOW "$KUNDEN_ALLOW"
  [ -z "$NETBOX" ] || [[ "$NETBOX" =~ ^[A-Za-z0-9.:-]+$ ]] || abbruch "AUTOMATO_FRONT_NETBOX ungültig (Name oder IP, optional :Port)"
  [ -z "$NETBOX_CA" ] || [[ "$NETBOX_CA" =~ ^[A-Za-z0-9._-]+$ ]] || abbruch "AUTOMATO_FRONT_NETBOX_CA ungültig (Dateiname in deploy/proxy/pki)"
  # Übrige Namen der Kunden-Domain (1.10.0, ADR 0053): an die Plattform, der sie sonst gehören
  # (z. B. Kundenseiten von Magister) statt „Unbekannte Adresse“
  UEBRIGE=${AUTOMATO_FRONT_UEBRIGE:-}
  UEBRIGE_CA=${AUTOMATO_FRONT_UEBRIGE_CA:-}
  [ -z "$UEBRIGE" ] || [[ "$UEBRIGE" =~ ^[A-Za-z0-9.:-]+$ ]] || abbruch "AUTOMATO_FRONT_UEBRIGE ungültig (Name oder IP, optional :Port)"
  [ -z "$UEBRIGE_CA" ] || [[ "$UEBRIGE_CA" =~ ^[A-Za-z0-9._-]+$ ]] || abbruch "AUTOMATO_FRONT_UEBRIGE_CA ungültig (Dateiname in deploy/proxy/pki)"
  local u
  for u in "$MAGISTER" "$CMDB"; do
    [ -z "$u" ] || [[ "$u" =~ ^https?://[A-Za-z0-9.:/_-]+$ ]] || abbruch "Adresse ungültig: '$u' (http(s)://name[:port][/pfad])"
  done
  # Magister des Kunden (<kunde>-um, 1.12.0): Magister findet den Mandanten am Namen – also wie die
  # übrigen Namen mit Host und SNI des Aufrufs. Ziel AUTOMATO_FRONT_MAGISTER, sonst das der übrigen
  # Namen (dort steht Magister ohnehin); CA AUTOMATO_FRONT_MAGISTER_CA bzw. die der übrigen Namen
  MAGISTER_CA=${AUTOMATO_FRONT_MAGISTER_CA:-}
  [ -z "$MAGISTER_CA" ] || [[ "$MAGISTER_CA" =~ ^[A-Za-z0-9._-]+$ ]] || abbruch "AUTOMATO_FRONT_MAGISTER_CA ungültig (Dateiname in deploy/proxy/pki)"
  UM_SCHEMA=https UM_ZIEL="" UM_CA=""
  if [ -n "$MAGISTER" ]; then
    UM_SCHEMA=${MAGISTER%%://*}
    UM_ZIEL=${MAGISTER#*://}
    UM_ZIEL=${UM_ZIEL%%/*}
    UM_CA=$MAGISTER_CA
  elif [ -n "$UEBRIGE" ]; then
    UM_ZIEL=$UEBRIGE
    UM_CA=$UEBRIGE_CA
  fi
  lade_tor
}

lade_tor() {  # Anmelde-Tor (1.4.0, ADR 0051)
  local u
  case "${AUTOMATO_FRONT_TOR:-aus}" in
    an | on | ja | true | 1) TOR=an ;;
    aus | off | nein | false | 0 | "") TOR=aus ;;
    *) abbruch "AUTOMATO_FRONT_TOR ungültig ('$AUTOMATO_FRONT_TOR': an oder aus)" ;;
  esac
  TOR_IMAGE=${AUTOMATO_FRONT_TOR_IMAGE:-$TOR_IMAGE_STANDARD}
  [[ "$TOR_IMAGE" =~ ^[A-Za-z0-9./:@_-]+$ ]] || abbruch "AUTOMATO_FRONT_TOR_IMAGE ungültig ('$TOR_IMAGE')"
  # Keycloak: Adresse für den Browser (Anmeldung, Issuer) und für den Proxy (Token, Schlüssel) –
  # ohne Angabe aus AUTOMATO_OIDC_ISSUER bzw. AUTOMATO_OIDC_JWKS_URL (Realm von Automato)
  HAUPT_REALM="" KC=${AUTOMATO_FRONT_KEYCLOAK:-} KC_INTERN=${AUTOMATO_FRONT_KEYCLOAK_INTERN:-}
  if [[ "${AUTOMATO_OIDC_ISSUER:-}" =~ ^(https?://[^/]+(/[^?#]*)?)/realms/([^/?#]+)/?$ ]]; then
    HAUPT_REALM=${BASH_REMATCH[3]}
    [ -n "$KC" ] || KC=${BASH_REMATCH[1]}
  fi
  if [ -z "$KC_INTERN" ] && [[ "${AUTOMATO_OIDC_JWKS_URL:-}" =~ ^(https?://[^/]+(/[^?#]*)?)/realms/[^/]+/ ]]; then
    KC_INTERN=${BASH_REMATCH[1]}
  fi
  KC=${KC%/}
  KC_INTERN=${KC_INTERN%/}
  [ -n "$KC_INTERN" ] || KC_INTERN=$KC
  for u in "$KC" "$KC_INTERN"; do
    [ -z "$u" ] || [[ "$u" =~ ^https?://[A-Za-z0-9.:/_-]+$ ]] || abbruch "Keycloak-Adresse ungültig: '$u' (http(s)://name[:port][/pfad])"
  done
  [ -z "$HAUPT_REALM" ] || realm_ok "$HAUPT_REALM" || abbruch "Realm in AUTOMATO_OIDC_ISSUER ungültig ('$HAUPT_REALM')"
  KC_CA=${AUTOMATO_FRONT_KEYCLOAK_CA:-}
  [ -z "$KC_CA" ] || [[ "$KC_CA" =~ ^[A-Za-z0-9._-]+$ ]] || abbruch "AUTOMATO_FRONT_KEYCLOAK_CA ungültig (Dateiname in deploy/proxy/pki)"
  # Portal (start.<intern>) nur für diese Rollen des Realms von Automato
  INTERN_ROLLEN=${AUTOMATO_FRONT_INTERN_ROLLEN:-admin operator}
  [[ "$INTERN_ROLLEN" =~ ^[A-Za-z0-9._\ -]+$ ]] || abbruch "AUTOMATO_FRONT_INTERN_ROLLEN ungültig ('$INTERN_ROLLEN')"
  if [ "$TOR" = an ] && [ -z "$KC" ]; then
    abbruch "AUTOMATO_FRONT_TOR=an braucht Keycloak: AUTOMATO_OIDC_ISSUER (https://…/realms/<realm>) oder AUTOMATO_FRONT_KEYCLOAK in .env"
  fi
  return 0
}

# Interne Domain = Domain der Kunden (1.10.0, Anwender 2026-11-13: Portal unter start.<domain>):
# dann nur das Portal start.<domain>, keine „Verwaltung“ auf der Domain selbst und kein zweiter
# Auffang-Eintrag (*.<domain> gibt es schon für die Kunden bzw. die übrigen Namen)
intern_geteilt() { [ -n "${INTERN:-}" ] && [ "$INTERN" = "$DOMAIN" ]; }

realm_ok() {  # wie das Kunden-Portal (store/portal.py): Name eines Keycloak-Realms, nie "master"
  [[ "${1:-}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] && [ "$1" != master ]
}

realm_pruefen() {
  realm_ok "${1:-}" || abbruch "Realm '${1:-}' ungültig: Buchstaben, Ziffern, '.', '_', '-' (höchstens 64 Zeichen, nicht 'master')"
}

tor_an() { [ "$TOR" = an ]; }
tor_env() { printf '%s/%s.env' "$FRONT_TOR_DIR" "$1"; }
tor_bereit() { [ -s "$(tor_env "$1")" ]; }  # Client-Secret des Realms hinterlegt
tor_dienst() {  # Container des Tors für einen Realm (Docker-Name, eindeutig)
  local s
  s=$(printf '%s' "$1" | tr 'A-Z._' 'a-z--' | cut -c1-20)
  printf 'tor-%s-%s' "$s" "$(printf '%s' "$1" | sha256sum | cut -c1-8)"
}

kurzname() {  # wie deploy/netbox-host/netbox-host.sh (DNS-Label, Platz für "-cmdb")
  local k=${1:-}
  [ -n "$k" ] || abbruch "Kurzname des Kunden fehlt"
  [[ "$k" =~ ^[a-z0-9]([a-z0-9-]{0,40}[a-z0-9])?$ ]] \
    || abbruch "Kurzname '$k' ungültig: nur a-z, 0-9 und '-', nicht am Anfang/Ende, höchstens 42 Zeichen"
  case "$k" in start | www | proxy | alle | konfig | mgmt | konsole | connect) abbruch "Kurzname '$k' ist reserviert" ;; esac
  case "$k" in *-nb | *-doc | *-um | *-cmdb) abbruch "Kurzname '$k' ist reserviert (endet wie ein Werkzeug: -nb, -doc, -um, -cmdb)" ;; esac
}

kdir() { printf '%s/kunden' "$FRONT_DIR"; }
aus_oberflaeche() {  # Kunde kommt über soll.json (QUELLE=oberflaeche bzw. seit 1.11.0 cockpit)?
  [ -f "$(kdir)/$1.conf" ] && grep -qxE 'QUELLE=(oberflaeche|cockpit)' "$(kdir)/$1.conf"
}
aus_cockpit() { [ -f "$(kdir)/$1.conf" ] && grep -qx 'QUELLE=cockpit' "$(kdir)/$1.conf"; }
aus_cockpit_realm() {  # gehört der Realm einem Kunden aus dem Cockpit? (1.13.0)
  local k
  for k in $(kunden); do
    lies_kunde "$k"
    if [ "$QUELLE" = cockpit ] && [ "$REALM" = "$1" ]; then return 0; fi
  done
  return 1
}
cockpit_verbunden() {  # 1.14.0 (ADR 0058): Automato mit dem Cockpit verbunden (soll.json, verwaltung)?
  [ -f "$FRONT_STATE/soll.json" ] || return 1
  python3 -I -c 'import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
sys.exit(0 if isinstance(d, dict) and d.get("verwaltung") == "cockpit" else 1)' "$FRONT_STATE/soll.json" 2>/dev/null
}
TROTZDEM=nein  # --trotzdem bei kunde, link, entfernen (1.14.0)
am_cockpit_vorbei() {  # $1 Befehl, $2 Kunde: von Hand nur ohne Cockpit bzw. mit --trotzdem (Notweg)
  cockpit_verbunden || return 0
  [ "$TROTZDEM" = ja ] || abbruch "Automato ist mit dem Cockpit verbunden: Kunden entstehen und ändern sich nur noch im Cockpit (Kundenregister, ADR 0058) – dort anlegen bzw. ändern. Nur im Notfall von Hand: front.sh $1 … --trotzdem"
  warn "am Cockpit vorbei (--trotzdem): $1 $2 von Hand – das Cockpit führt diesen Kunden nicht (Altlast); im Cockpit übernehmen bzw. dort aufräumen"
}
nur_befehl() {
  ! aus_cockpit "$1" || abbruch "Kunde '$1' wird im Cockpit gepflegt (Kundenregister, ADR 0054)"
  ! aus_oberflaeche "$1" || abbruch "Kunde '$1' wird in der Oberfläche gepflegt (Administration → Kunden-Portal)"
}
gen() { printf '%s/generated' "$FRONT_DIR"; }

kunden() {  # alle Kurznamen
  local f
  for f in "$(kdir)"/*.conf; do
    [ -f "$f" ] && basename "$f" .conf
  done
  return 0
}

lies_kunde() {  # setzt NAME, DOC, NB, UM, CMDB_AN, QUELLE, REALM, ZUGRIFF, NETZE, SPERRE aus kunden/<k>.conf
  local k=$1
  # NB fehlt in Dateien vor 1.11.0: NetBox wie bisher an; SPERRE (ADR 0059) fehlt: nicht gesperrt
  NAME=$k DOC=nein NB=ja UM=nein CMDB_AN=nein QUELLE=befehl REALM="" ZUGRIFF=ueberall NETZE="" SPERRE=nein
  # shellcheck disable=SC1090 # mit printf %q geschrieben (kunde)
  . "$(kdir)/$k.conf"
}

zugriff_pruefen() {  # $1 Zugriff, $2 Netze (mit Leerzeichen): gültig für Caddy und Datei?
  case "$1" in
    ueberall | intern) ;;
    netze)
      [ -n "${2// /}" ] || abbruch "Zugriff „netze“ ohne Netz"
      netze "Netze des Kunden" "$2"
      local n
      for n in $2; do  # je Eintrag IPv4 bzw. IPv6 (mit Doppelpunkt), optional mit Maske
        [[ "$n" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}(/[0-9]{1,2})?$ || "$n" =~ ^[0-9A-Fa-f:.]*:[0-9A-Fa-f:.]*(/[0-9]{1,3})?$ ]] \
          || abbruch "Netz '$n' ungültig (z. B. 203.0.113.0/24 oder 2001:db8::/48)"
      done
      ;;
    *) abbruch "Zugriff '$1' ungültig (ueberall, intern oder Netze)" ;;
  esac
}

zugriff_text() {  # für liste und pruefen
  case "$ZUGRIFF" in
    intern) printf 'nur intern' ;;
    netze) printf 'nur %s' "$NETZE" ;;
    *) printf 'überall' ;;
  esac
}

zugriff_matcher() {  # auf Ebene der Adresse: wer diesen Kunden nicht sehen darf (1.5.0)
  case "$ZUGRIFF" in
    intern)
      # Management-Netz nach der Adresse der Gegenstelle, nie über die WAF (wie das Portal)
      printf '\t@front_kunde_fremd not remote_ip %s\n' "$MGMT"
      [ -z "$WAF" ] || printf '\t@front_kunde_waf remote_ip %s\n' "$WAF"
      ;;
    netze)
      # Adresse des Benutzers: über die WAF aus X-Forwarded-For (trusted_proxies), sonst direkt
      printf '\t@front_kunde_fremd not client_ip %s\n' "$NETZE"
      ;;
  esac
}

zugriff_sperre() {  # in route direkt nach der Sperre nach Absender: wie eine unbekannte Adresse
  case "$ZUGRIFF" in
    intern | netze)
      printf '\t\trespond @front_kunde_fremd "Unbekannte Adresse" 404\n'
      [ "$ZUGRIFF" != intern ] || [ -z "$WAF" ] || printf '\t\trespond @front_kunde_waf "Unbekannte Adresse" 404\n'
      ;;
  esac
  return 0
}

html() {  # Text für HTML maskieren
  local s=$1
  # Ersatz in Anführungszeichen: sonst setzt bash ≥ 5.2 (patsub_replacement) für & den Treffer ein
  s=${s//&/"&amp;"}
  s=${s//</"&lt;"}
  s=${s//>/"&gt;"}
  s=${s//\"/"&quot;"}
  printf '%s' "$s"
}

# --- Seiten ------------------------------------------------------------------------------------
# Abmelden mit Anmelde-Tor (1.12.2): das Tor löscht sein Cookie und beendet die Sitzung in
# Keycloak selbst (--backend-logout-url), dann zeigt $1 die Seite „Abgemeldet“ ausserhalb des Tors.
# 1.12.1 schickte den Browser noch über die Abmeldeseite von Keycloak zurück auf die Startseite –
# im Dev-Test blieb er dort stehen, ohne dass sichtbar war, dass er abgemeldet ist.
abmelde_url() {  # $1 Adresse der Seite „Abgemeldet“ (Domain des Tors, --whitelist-domain)
  printf '/oauth2/sign_out?rd=%s' "$(url_kodiert "$1")"
}

ABGEMELDET=/abgemeldet

# Route „Abgemeldet“ vor dem Tor: $1 Ordner der statischen Seiten
abgemeldet_route() {
  printf '\t\thandle %s {\n\t\t\troot * %s\n\t\t\trewrite * %s.html\n\t\t\timport front-seite\n\t\t}' \
    "$ABGEMELDET" "$1" "$ABGEMELDET"
}

abgemeldet_seite() {  # HTML der Seite „Abgemeldet“: $1 Titel (Kunde bzw. Portal)
  cat <<SEITE
<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Abgemeldet · signed out – $(html "$1")</title>
<style>
:root { color-scheme: light dark; --bg:#f6f7f9; --card:#fff; --fg:#1d2330; --muted:#5b6473; --line:#dfe3ea; --accent:#2457c5; }
@media (prefers-color-scheme: dark) { :root { --bg:#14171d; --card:#1d222b; --fg:#e7eaf0; --muted:#9aa3b2; --line:#2c3340; --accent:#7aa2ff; } }
body { margin:0; font: 16px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; background:var(--bg); color:var(--fg); }
main { max-width: 520px; margin: 12vh auto 0; padding: 28px 24px; background:var(--card); border:1px solid var(--line); border-radius:10px; }
h1 { margin: 0 0 8px; font-size: 1.4rem; }
p { margin: 0 0 18px; color: var(--muted); }
a.knopf { display:inline-block; padding:8px 14px; border-radius:8px; background:var(--accent); color:#fff; text-decoration:none; }
</style>
</head>
<body>
<main>
<h1>Abgemeldet · signed out</h1>
<p>$(html "$1"): Sie sind abgemeldet, auch bei der Anmeldung (Keycloak). · You are signed out.</p>
<a class="knopf" href="/">Wieder anmelden · sign in again</a>
</main>
</body>
</html>
SEITE
}

url_kodiert() { python3 -I -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"; }

seite() {  # HTML-Gerüst: $1 Titel, $2 Untertitel, $3 Adresse zum Abmelden (Anmelde-Tor), Kacheln auf stdin
  local t=$1 sub=$2 fuss="Anmeldung je Werkzeug · sign-in per tool" kt ku kb
  # mit Anmelde-Tor: die Anmeldung gilt für alle Werkzeuge, Abmelden beendet auch die Keycloak-Sitzung
  [ -z "${3:-}" ] || fuss="<a href=\"$3\">Abmelden · sign out</a>"
  cat <<EOF
<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>$(html "$t")</title>
<style>
:root { color-scheme: light dark; --bg:#f6f7f9; --card:#fff; --fg:#1d2330; --muted:#5b6473; --line:#dfe3ea; --accent:#2457c5; }
@media (prefers-color-scheme: dark) { :root { --bg:#14171d; --card:#1d222b; --fg:#e7eaf0; --muted:#9aa3b2; --line:#2c3340; --accent:#7aa2ff; } }
* { box-sizing: border-box; }
body { margin:0; font: 16px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; background:var(--bg); color:var(--fg); }
main { max-width: 960px; margin: 0 auto; padding: 32px 16px; }
h1 { margin: 0 0 4px; font-size: 1.6rem; }
p.sub { margin: 0 0 24px; color: var(--muted); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 14px; }
a.tile { display:block; padding:18px; background:var(--card); border:1px solid var(--line); border-radius:10px; color:inherit; text-decoration:none; }
a.tile:hover, a.tile:focus { border-color: var(--accent); outline: none; }
a.tile b { display:block; font-size:1.1rem; color: var(--accent); margin-bottom: 4px; }
a.tile span { color: var(--muted); font-size: .92rem; }
footer { margin-top: 28px; color: var(--muted); font-size: .85rem; }
footer a { color: var(--accent); }
</style>
</head>
<body>
<main>
<h1>$(html "$t")</h1>
<p class="sub">$(html "$sub")</p>
<div class="grid">
EOF
  while IFS=$'\t' read -r kt ku kb; do
    [ -n "$kt" ] || continue
    printf '<a class="tile" href="%s"><b>%s</b><span>%s</span></a>\n' "$(html "$ku")" "$(html "$kt")" "$(html "$kb")"
  done
  printf '</div>\n<footer>%s</footer>\n</main>\n</body>\n</html>\n' "$fuss"
}

kacheln() {  # Kacheln der Startseite eines Kunden (Titel\tURL\tBeschreibung)
  local k=$1
  lies_kunde "$k"
  [ "$NB" != ja ] || printf 'NetBox\thttps://%s-nb.%s\tIPAM, Geräte, Verkabelung\n' "$k" "$DOMAIN"
  [ "$DOC" != ja ] || printf 'Dokumentation\thttps://%s-doc.%s\tFirewall-Regeln, Applikationen, Wege\n' "$k" "$DOMAIN"
  [ "$UM" != ja ] || [ -z "$UM_ZIEL" ] || printf 'Magister\thttps://%s-um.%s\tBenutzer, Gruppen und Rechte\n' "$k" "$DOMAIN"
  [ "$CMDB_AN" != ja ] || [ -z "$CMDB" ] || printf 'CMDB\thttps://%s-cmdb.%s\tLizenzen und Geräte\n' "$k" "$DOMAIN"
  [ ! -f "$(kdir)/$k.links.tsv" ] || cat "$(kdir)/$k.links.tsv"
}

# --- Caddy ---------------------------------------------------------------------------------------
# Jede Adresse besteht aus einem Teil auf Ebene der Adresse (Zertifikat, Kopfzeilen der Antwort,
# Matcher) und einem `route { … }`, das in der geschriebenen Reihenfolge läuft: zuerst die Sperre
# nach Absender, dann das Anmelde-Tor, dann die Weiterleitung (seit 1.4.0; Caddy ordnet sonst
# selbst – forward_auth, redir und handle liefen vor respond).
schreibe_snippets() {
  local waf_matcher="" waf_sperre=""
  if [ -n "$WAF" ]; then
    # Interne Adressen nie über die WAF – auch wenn das Management-Netz die WAF einschliesst (sonst
    # käme ein Kunde mit gesetztem Host-Kopf über die WAF aufs Portal)
    waf_matcher=$'\n\t'"@front_waf remote_ip $WAF"
    waf_sperre=$'\n\t''respond @front_waf "Zugriff nur aus dem Management-Netz" 403'
  fi
  cat <<EOF
# Erzeugt von deploy/front/front.sh – nicht von Hand ändern (front.sh erzeugen)
# Kunden-Adressen: Zertifikat für *.$DOMAIN, nur WAF und Management-Netz
(front-kunden) {
	tls /etc/caddy/pki/mgmt.crt /etc/caddy/pki/mgmt.key {
		protocols tls1.2 tls1.3
	}
	encode zstd gzip
	header {
		Strict-Transport-Security "max-age=31536000"
		X-Content-Type-Options "nosniff"
		Referrer-Policy "strict-origin-when-cross-origin"
		X-Frame-Options "SAMEORIGIN"
		-Server
	}
	@front_fremd not remote_ip $KUNDEN_ALLOW
}

# Übrige Namen unter $DOMAIN (AUTOMATO_FRONT_UEBRIGE): wie Kunden-Adressen, aber ohne eigene
# Kopfzeilen der Antwort – die setzt die Plattform dahinter (z. B. Magister) selbst
(front-uebrige) {
	tls /etc/caddy/pki/mgmt.crt /etc/caddy/pki/mgmt.key {
		protocols tls1.2 tls1.3
	}
	@front_fremd not remote_ip $KUNDEN_ALLOW
}

# Interne Adressen: Zertifikat der eigenen CA für $INTERN und *.$INTERN, nur Management-Netz
(front-intern) {
	tls /etc/caddy/pki/mgmt-int.crt /etc/caddy/pki/mgmt-int.key {
		protocols tls1.2 tls1.3
	}
	encode zstd gzip
	header {
		Strict-Transport-Security "max-age=31536000"
		X-Content-Type-Options "nosniff"
		Referrer-Policy "no-referrer"
		X-Frame-Options "DENY"
		-Server
	}
	@front_fremd not remote_ip $MGMT$waf_matcher
}

# Wer angemeldet ist, sagt nur das Anmelde-Tor: gleichnamige Kopfzeilen vom Absender weg
(front-ohne-identitaet) {
	request_header -X-Auth-Request-User
	request_header -X-Auth-Request-Email
	request_header -X-Auth-Request-Preferred-Username
	request_header -X-Auth-Request-Groups
	request_header -X-Automato-User
	request_header -X-Automato-Realm
	request_header -X-Automato-Groups
}

# Erster Schritt in route: Sperre nach Absender
(front-kunden-sperre) {
	respond @front_fremd "Zugriff nur über die WAF" 403
	import front-ohne-identitaet
}

(front-intern-sperre) {
	respond @front_fremd "Zugriff nur aus dem Management-Netz" 403$waf_sperre
	import front-ohne-identitaet
}

# Anmelde-Tor (1.4.0, ADR 0051) – {args[0]} = Container des Tors (oauth2-proxy, ein Realm): ohne
# Anmeldung leitet er zu Keycloak weiter, danach gibt er den Benutzer als X-Auth-Request-* zurück
(front-tor) {
	handle /oauth2/* {
		reverse_proxy {args[0]}:4180
	}
	forward_auth {args[0]}:4180 {
		uri /
		copy_headers X-Auth-Request-User X-Auth-Request-Email X-Auth-Request-Preferred-Username X-Auth-Request-Groups
	}
}

# Angemeldeter Benutzer für NetBox, Magister und CMDB (in reverse_proxy) – {args[0]} = Realm; der
# Benutzername trägt den Realm, damit er nie ein lokales Konto (admin, Token-Benutzer) trifft
(front-identitaet) {
	header_up X-Automato-User "{http.request.header.X-Auth-Request-Preferred-Username}@{args[0]}"
	header_up X-Automato-Realm "{args[0]}"
	header_up X-Automato-Groups "automato-lesen,{http.request.header.X-Auth-Request-Groups}"
}

# Kunde ohne Realm bzw. Realm ohne Client-Secret: gesperrt (Einzelheiten: front.sh pruefen)
(front-gesperrt) {
	respond "Anmeldung nicht eingerichtet · sign-in not set up" 403
}

# Kunde zum Entfernen vorgemerkt (Cockpit, ADR 0059): alle Adressen gesperrt, Daten bleiben
(front-zugang-gesperrt) {
	respond "Zugang gesperrt · access blocked" 403
}

# Statische Seite: nichts nachladen, nichts einbetten
(front-seite) {
	header Content-Security-Policy "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
	file_server
}
EOF
}

tor_fuer_kunde() {  # setzt TOR_DIENST (Container des Tors; leer = ohne Tor bzw. gesperrt) und GESPERRT
  TOR_DIENST="" GESPERRT=""
  tor_an || return 0
  if [ -n "$REALM" ] && tor_bereit "$REALM"; then
    TOR_DIENST=$(tor_dienst "$REALM")
  else
    GESPERRT=1
  fi
}

route_kunde() {  # route einer Kunden-Adresse: $1 Ziel, $2 vor dem Tor (immer), $3 vor dem Tor (nur mit Tor),
  # $4 noch vor der Sperre nach Absender (Automato selbst an seine NetBox, 1.10.0). Kunde gesperrt
  # (SPERRE=ja, ADR 0059): nach der Sperre nach Absender und dem Zugriff nur „Zugang gesperrt“ –
  # auch für die NetBox-API mit Token und Automato selbst
  printf '\troute {\n'
  [ "${SPERRE:-nein}" = ja ] || [ -z "${4:-}" ] || printf '%s\n' "$4"
  printf '\t\timport front-kunden-sperre\n'
  zugriff_sperre
  if [ "${SPERRE:-nein}" = ja ]; then
    printf '\t\timport front-zugang-gesperrt\n\t}\n'
    return
  fi
  [ -z "${2:-}" ] || printf '%s\n' "$2"
  if [ -n "$GESPERRT" ]; then
    printf '\t\timport front-gesperrt\n\t}\n'
    return
  fi
  if [ -n "$TOR_DIENST" ]; then
    [ -z "${3:-}" ] || printf '%s\n' "$3"
    printf '\t\timport front-tor %s\n' "$TOR_DIENST"
  fi
  printf '%s\n\t}\n' "$1"
}

schreibe_kunde() {  # Caddy-Einträge eines Kunden
  local k=$1 trust="" ident="" nb_ziel
  lies_kunde "$k"
  tor_fuer_kunde
  [ -z "$TOR_DIENST" ] || ident=$'\n\t\t\t'"import front-identitaet $REALM"
  [ -z "$NETBOX_CA" ] || trust=$'\n\t\t\t\ttls_trust_pool file /etc/caddy/pki/'"$NETBOX_CA"
  printf '# Kunde %s (front.sh) – nicht von Hand ändern\n' "$k"
  printf '%s.%s {\n\timport front-kunden\n' "$k" "$DOMAIN"
  zugriff_matcher
  route_kunde $'\t\t'"root * /srv/front/$k"$'\n\t\timport front-seite' "" "$(abgemeldet_route "/srv/front/$k")"
  printf '}\n'
  if [ -n "$NETBOX" ] && [ "$NB" = ja ]; then
    nb_ziel() {  # NetBox auf dem NetBox-Host; $1 = Zeile(n) für den Benutzer
      printf '\t\treverse_proxy https://%s {\n\t\t\theader_up Host {host}%s\n\t\t\ttransport http {\n\t\t\t\ttls_server_name {host}%s\n\t\t\t}\n\t\t}' \
        "$NETBOX" "${1:-}" "$trust"
    }
    printf '\n%s-nb.%s {\n\timport front-kunden\n' "$k" "$DOMAIN"
    zugriff_matcher
    # API mit Token (NetBox-Sync von Automato, Skripte): NetBox prüft den Token selbst – ohne Tor
    printf '\t@front_nb_token {\n\t\tpath /api/* /graphql/*\n\t\theader_regexp Authorization ^(Token|Bearer)\\s\n\t}\n'
    local eigen=""
    if [ -n "$EIGENE_NETZE" ]; then
      # Automato selbst (Orchestrator, Worker) an die NetBox seines Kunden (1.10.0, ADR 0053): kommt
      # über den Hairpin mit einer Adresse seiner Docker-Netze – nur die API mit Token, ohne
      # Kopfzeilen der Anmeldung, vor der Sperre nach Absender und dem Zugriff je Kunde
      printf '\t@front_eigen {\n\t\tremote_ip %s\n\t\tpath /api/* /graphql/*\n\t\theader_regexp Authorization ^(Token|Bearer)\\s\n\t}\n' "$EIGENE_NETZE"
      eigen=$'\t\timport front-ohne-identitaet\n\t\thandle @front_eigen {\n'"$(nb_ziel "" | sed 's/^/\t/')"$'\n\t\t}'
    fi
    route_kunde "$(nb_ziel "$ident")" \
      $'\t\thandle @front_nb_token {\n'"$(nb_ziel "" | sed 's/^/\t/')"$'\n\t\t}' \
      $'\t\tredir /logout/ '"$(abmelde_url "https://$k.$DOMAIN$ABGEMELDET")"' 302' "$eigen"
    printf '}\n'
  fi
  if [ "$DOC" = ja ]; then
    local ui=$'\t\treverse_proxy web-ui:5173 {\n\t\t\timport /etc/caddy/automato-tls/upstream-{$AUTOMATO_INTERNAL_TLS:off}.caddy\n\t\t}'
    printf '\n%s-doc.%s {\n\timport front-kunden\n' "$k" "$DOMAIN"
    zugriff_matcher
    printf '\t@internal path /api/internal /api/internal/*\n\t@front_api path /api/*\n'
    # die API prüft das Token von Automato selbst (Kunden-Realm, ADR 0050) – nur die Oberfläche hinter dem Tor
    route_kunde "$ui" $'\t\trespond @internal 404' \
      $'\t\thandle @front_api {\n'"$(printf '%s' "$ui" | sed 's/^/\t/')"$'\n\t\t}'
    printf '}\n'
  fi
  if [ "$UM" = ja ] && [ -n "$UM_ZIEL" ]; then
    # Magister des Kunden: Name bleibt (Magister findet daran den Mandanten), hinter dem Tor
    local um_trust="" um_ziel
    [ -z "$UM_CA" ] || um_trust=$'\n\t\t\t\ttls_trust_pool file /etc/caddy/pki/'"$UM_CA"
    if [ "$UM_SCHEMA" = https ]; then
      um_ziel=$'\t\t'"reverse_proxy https://$UM_ZIEL {"$'\n\t\t\theader_up Host {host}'"$ident"$'\n\t\t\ttransport http {\n\t\t\t\ttls_server_name {host}'"$um_trust"$'\n\t\t\t}\n\t\t}'
    else
      um_ziel=$'\t\t'"reverse_proxy http://$UM_ZIEL {"$'\n\t\t\theader_up Host {host}'"$ident"$'\n\t\t}'
    fi
    printf '\n%s-um.%s {\n\timport front-kunden\n' "$k" "$DOMAIN"
    zugriff_matcher
    route_kunde "$um_ziel"
    printf '}\n'
  fi
  if [ "$CMDB_AN" = ja ] && [ -n "$CMDB" ]; then
    printf '\n%s-cmdb.%s {\n\timport front-kunden\n' "$k" "$DOMAIN"
    zugriff_matcher
    route_kunde $'\t\t'"reverse_proxy $CMDB {$ident"$'\n\t\t}'
    printf '}\n'
  fi
}

eigene_netze() {  # Docker-Netze von Orchestrator, Worker und Proxy (1.10.0): Absender von Automato selbst
  if [ -n "${FRONT_EIGENE_NETZE+x}" ]; then  # Tests bzw. von Hand
    printf '%s' "$FRONT_EIGENE_NETZE"
    return 0
  fi
  local svc id net netze=""
  for svc in orchestrator collector proxy; do
    id=$($DOCKER compose --profile core --profile collectors --profile proxy ps -q "$svc" 2>/dev/null | head -1 || true)
    [ -n "$id" ] || continue
    for net in $($DOCKER inspect -f '{{range .NetworkSettings.Networks}}{{.NetworkID}} {{end}}' "$id" 2>/dev/null || true); do
      netze+=" $($DOCKER network inspect -f '{{range .IPAM.Config}}{{.Subnet}} {{end}}' "$net" 2>/dev/null || true)"
    done
  done
  local liste=()
  read -ra liste <<<"$netze"
  printf '%s\n' "${liste[@]}" | grep -E '^[0-9A-Fa-f:.]+/[0-9]{1,3}$' | sort -u | tr '\n' ' ' | sed 's/ $//' || true
}

# Der Proxy hängt an der Web-UI (Profil core): Compose braucht beide Profile, sonst lehnt es das
# Projekt ab („depends on undefined service web-ui“) und der laufende Proxy gälte als aus (1.2.1)
dc() { $DOCKER compose --profile core --profile proxy "$@"; }
proxy_laeuft() { dc ps --status running --services 2>/dev/null | grep -qx proxy; }

proxy_reload() {
  if proxy_laeuft; then
    local meldung
    if meldung=$(dc exec -T proxy caddy reload --config /etc/caddy/Caddyfile 2>&1); then
      ok "Proxy neu geladen"
    else
      fehler "Proxy-Konfiguration ungültig – alte bleibt aktiv: ${meldung##*$'\n'}"
      info "Einzelheiten: docker compose --profile core --profile proxy logs --since 5m proxy"
    fi
  else
    info "Proxy läuft nicht – Start: docker compose --profile core --profile proxy up -d"
  fi
}

# Adressen und Ziele für den Orchestrator (1.10.0, ADR 0053): das Cockpit holt daraus sein
# Inventar (GET /api/inventory). Nur Namen und Adressen, nur mit eingerichtetem Host-Agenten.
schreibe_topologie() {
  [ -d "$FRONT_STATE" ] || return 0
  local k tmp ui=${AUTOMATO_UI_HOST:-} portal="" vm sep=$'\x1f' pub="" kennung=""
  [ -z "$INTERN" ] || portal="https://start.$INTERN"
  # Empfangsschlüssel fürs Cockpit (1.13.0, ADR 0055): nur der öffentliche, nur mit Anmelde-Tor
  if tor_an; then
    kennung=$(empfang_kennung)
    [ -z "$kennung" ] || pub=$(empfang_pub)
  fi
  vm=${FRONT_HOSTNAME:-$(hostname -f 2>/dev/null || hostname 2>/dev/null || true)}
  tmp=$(mktemp "$FRONT_STATE/.topologie-XXXXXX") || return 0
  for k in $(kunden); do
    lies_kunde "$k"
    local nb="" doc="" um="" cm="" w=""
    [ -z "$NETBOX" ] || [ "$NB" != ja ] || nb="https://$k-nb.$DOMAIN"
    [ "$DOC" != ja ] || doc="https://$k-doc.$DOMAIN"
    [ "$UM" != ja ] || [ -z "$UM_ZIEL" ] || um="https://$k-um.$DOMAIN"
    [ "$CMDB_AN" != ja ] || [ -z "$CMDB" ] || cm="https://$k-cmdb.$DOMAIN"
    # 1.11.0 (ADR 0054): Herkunft und gewählte Werkzeuge, Zugriff, Netze, Realm – fürs Cockpit
    [ "$DOC" != ja ] || w+=" doc"
    [ "$NB" != ja ] || w+=" nb"
    [ "$UM" != ja ] || w+=" um"
    [ "$CMDB_AN" != ja ] || w+=" cmdb"
    printf '%s\n' "$k$sep${NAME//$sep/}${sep}https://$k.$DOMAIN$sep$nb$sep$doc$sep$um$sep$cm$sep$QUELLE$sep${w# }$sep$ZUGRIFF$sep$NETZE$sep$REALM$sep$SPERRE"
  done | python3 -I -c "$TOPOLOGIE_PY" "$vm" "$(eigene_ips)" "${ui:+https://$ui}" "$portal" "$DOMAIN" \
    "$NETBOX" "$UEBRIGE" "${UM_ZIEL:+$UM_SCHEMA://$UM_ZIEL}" "$CMDB" "$TOR" "$pub" "$kennung" \
    "$TOR_EMPFANG_ALG" >"$tmp" || { rm -f "$tmp"; return 0; }
  chmod 644 "$tmp"
  mv -f "$tmp" "$FRONT_STATE/topologie.json"
}

# Python für schreibe_topologie: Kunden auf stdin (Trenner 0x1f), Werte als Argumente
TOPOLOGIE_PY=$(cat <<'PY'
import json, sys
from datetime import datetime, timezone
vm, ips, ui, portal, domain, netbox, uebrige, magister, cmdb, tor, pub, kennung, alg = sys.argv[1:14]
felder = ("kurzname", "name", "startseite", "netbox", "automato", "magister", "cmdb",
          "quelle", "werkzeuge", "zugriff", "netze", "realm", "gesperrt")
listen = ("werkzeuge", "netze")  # mit Leerzeichen getrennt
def wert(feld, w):
    if feld in listen:
        return w.split()
    if feld == "gesperrt":  # ADR 0059: zum Entfernen vorgemerkt
        return w == "ja"
    return w or None
kunden = []
for zeile in sys.stdin.read().splitlines():
    teile = zeile.split("\x1f")
    if len(teile) == len(felder):
        kunden.append({f: wert(f, w) for f, w in zip(felder, teile)})
empfang = None  # 1.13.0 (ADR 0055): öffentlicher Schlüssel, mit dem das Cockpit Client-Secrets versiegelt
if pub and kennung:
    try:
        pem = open(pub, encoding="ascii").read()
    except (OSError, ValueError):
        pem = ""
    if pem.startswith("-----BEGIN PUBLIC KEY-----") and len(pem) <= 1000:
        empfang = {"alg": alg, "pem": pem, "kennung": kennung}
print(json.dumps({
    "zeit": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "vm": {"name": vm, "adressen": ips.split()},
    "oberflaeche": ui or None,
    "portal": portal or None,
    "domain": domain,
    "netbox_host": netbox or None,
    "uebrige": uebrige or None,
    "magister": magister or None,
    "cmdb": cmdb or None,
    "tor": tor == "an",
    "tor_empfang": empfang,
    "kunden": kunden,
}, ensure_ascii=False, indent=1))
PY
)

ca_pruefen() {  # $1 Einstellung, $2 Datei in deploy/proxy/pki: da, nicht leer, ein Zertifikat (PEM)?
  [ -n "$2" ] || return 0
  local f=$FRONT_PKI/$2
  [ -s "$f" ] || abbruch "$1: deploy/proxy/pki/$2 fehlt oder ist leer – nichts geändert"
  openssl x509 -in "$f" -noout 2>/dev/null ||
    abbruch "$1: deploy/proxy/pki/$2 ist kein Zertifikat im PEM-Format – nichts geändert"
}

cmd_erzeugen() {
  lade_conf
  # eine fehlende oder leere CA-Datei liesse den Proxy jede Verbindung dorthin ablehnen (1.10.0)
  ca_pruefen AUTOMATO_FRONT_NETBOX_CA "$NETBOX_CA"
  ca_pruefen AUTOMATO_FRONT_UEBRIGE_CA "$UEBRIGE_CA"
  ca_pruefen AUTOMATO_FRONT_MAGISTER_CA "$MAGISTER_CA"
  if tor_an; then ca_pruefen AUTOMATO_FRONT_KEYCLOAK_CA "$KC_CA"; fi
  local g k www name abmelden beschreibung
  EIGENE_NETZE=$(eigene_netze)
  [ -z "$EIGENE_NETZE" ] || netze "Docker-Netze von Automato" "$EIGENE_NETZE"
  g=$(gen)
  www=$g/www
  install -d -m 755 "$g" "$g/sites" "$www"
  rm -f "$g"/sites/*.caddy
  find "$www" -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +
  schreibe_snippets >"$g/snippets.caddy"
  for k in $(kunden); do
    schreibe_kunde "$k" >"$g/sites/$k.caddy"
    install -d -m 755 "$www/$k"
    lies_kunde "$k"
    tor_fuer_kunde
    abmelden=""
    [ -z "$TOR_DIENST" ] || abmelden=$(abmelde_url "https://$k.$DOMAIN$ABGEMELDET")
    kacheln "$k" | seite "$NAME" "Werkzeuge · tools" "$abmelden" >"$www/$k/index.html"
    abgemeldet_seite "$NAME" >"$www/$k$ABGEMELDET.html"
  done
  # Unbekannte Namen unter den Domains (z. B. <kunde>-doc ohne --doc): 404 statt einer leeren
  # Antwort; die bekannten Namen sind genauer und gehen vor. Mit AUTOMATO_FRONT_UEBRIGE (1.10.0,
  # ADR 0053) gehen die übrigen Namen der Kunden-Domain an die Plattform dahinter (z. B. Magister):
  # Name und SNI bleiben, die Sperre nach Absender gilt, Kopfzeilen der Anmeldung nie vom Absender
  {
    printf '# Unbekannte Adressen (front.sh) – nicht von Hand ändern\n'
    if [ -n "$UEBRIGE" ]; then
      local trust_u=""
      [ -z "$UEBRIGE_CA" ] || trust_u=$'\n\t\t\t\ttls_trust_pool file /etc/caddy/pki/'"$UEBRIGE_CA"
      printf '*.%s {\n\timport front-uebrige\n\troute {\n\t\timport front-kunden-sperre\n' "$DOMAIN"
      printf '\t\treverse_proxy https://%s {\n\t\t\theader_up Host {host}\n' "$UEBRIGE"
      printf '\t\t\ttransport http {\n\t\t\t\ttls_server_name {host}%s\n\t\t\t}\n\t\t}\n\t}\n}\n' "$trust_u"
    else
      printf '*.%s {\n\timport front-kunden\n\troute {\n\t\timport front-kunden-sperre\n\t\trespond "Unbekannte Adresse" 404\n\t}\n}\n' "$DOMAIN"
    fi
    if [ -n "$INTERN" ] && ! intern_geteilt; then
      printf '\n*.%s {\n\timport front-intern\n\troute {\n\t\timport front-intern-sperre\n\t\trespond "Unbekannte Adresse" 404\n\t}\n}\n' "$INTERN"
    fi
  } >"$g/sites/_unbekannt.caddy"
  if [ -n "$INTERN" ]; then
    # Portal: mit Anmelde-Tor nur für die Rollen AUTOMATO_FRONT_INTERN_ROLLEN im Realm von Automato
    local portal=$'\t\troot * /srv/front/_portal\n\t\timport front-seite'
    abmelden=""
    if tor_an; then
      if [ -n "$HAUPT_REALM" ] && tor_bereit "$HAUPT_REALM"; then
        portal="$(abgemeldet_route /srv/front/_portal)"$'\n\t\timport front-tor tor-intern\n'"$portal"
        abmelden=$(abmelde_url "https://start.$INTERN$ABGEMELDET")
      else
        portal=$'\t\timport front-gesperrt'
      fi
    fi
    install -d -m 755 "$www/_portal"
    {
      for k in $(kunden); do
        lies_kunde "$k"
        name=$NAME
        beschreibung=$(werkzeuge "$k")
        # ADR 0059: zum Entfernen vorgemerkt
        [ "$SPERRE" != ja ] || beschreibung="Zugang gesperrt (zum Entfernen vorgemerkt) · $beschreibung"
        printf '%s\thttps://%s.%s\t%s\n' "$name" "$k" "$DOMAIN" "$beschreibung"
      done
    } | seite "Kunden" "Portal · alle Kunden" "$abmelden" >"$www/_portal/index.html"
    abgemeldet_seite "Portal" >"$www/_portal$ABGEMELDET.html"
    cat >"$g/sites/_intern.caddy" <<EOF
# Interne Adressen (front.sh) – nicht von Hand ändern
start.$INTERN {
	import front-intern
	route {
		import front-intern-sperre
$portal
	}
}
EOF
    intern_geteilt || cat >>"$g/sites/_intern.caddy" <<EOF

# Verwaltung (Cockpit, Version 2) – bis dahin das Portal (nach der Sperre: route hält die Reihenfolge)
$INTERN {
	import front-intern
	route {
		import front-intern-sperre
		redir https://start.$INTERN{uri} 302
	}
}
EOF
  else
    warn "AUTOMATO_FRONT_DOMAIN_INT fehlt – kein internes Portal"
  fi
  schreibe_tor_compose
  chmod -R a+rX "$g"
  if tor_an; then empfang_anlegen; fi  # 1.13.0 (ADR 0055): Client-Secrets aus dem Cockpit
  schreibe_topologie
  # Stand des Tors auch nach Änderungen von Hand (kunde, tor) – Ergebnis des letzten sync bleibt
  [ ! -f "$FRONT_STATE/status.json" ] || status_schreiben behalten
  ok "Front-Proxy erzeugt ($(kunden | wc -l | tr -d ' ') Kunden)"
  tor_starten
  proxy_reload
}

# --- Anmelde-Tor (1.4.0, ADR 0051) ---------------------------------------------------------------
tor_realms() {  # Realms der Kunden mit hinterlegtem Client-Secret (je einer ein Container)
  local k
  for k in $(kunden); do
    lies_kunde "$k"
    [ -n "$REALM" ] && tor_bereit "$REALM" && printf '%s\n' "$REALM"
  done | sort -u
  return 0
}

tor_dienst_yaml() {  # $1 Container, $2 Realm, $3 erlaubte Rollen (leer = alle Benutzer des Realms)
  local dienst=$1 realm=$2 rollen=${3:-} oeffentlich innen arg rolle
  oeffentlich="$KC/realms/$realm"
  innen="$KC_INTERN/realms/$realm/protocol/openid-connect"
  local args=(
    --http-address=0.0.0.0:4180
    --provider=keycloak-oidc
    "--client-id=$TOR_CLIENT"
    "--oidc-issuer-url=$oeffentlich"
    --skip-oidc-discovery=true
    "--login-url=$oeffentlich/protocol/openid-connect/auth"
    "--redeem-url=$innen/token"
    "--oidc-jwks-url=$innen/certs"
    "--profile-url=$innen/userinfo"
    "--backend-logout-url=$innen/logout?id_token_hint={id_token}"
    --code-challenge-method=S256
    "--scope=openid profile email"
    # Kennung = Benutzername: Konten ohne E-Mail (AD ohne mail, lokale Benutzer) kommen auch an
    --oidc-email-claim=preferred_username
    "--email-domain=*"
    --insecure-oidc-allow-unverified-email=true
    --upstream=static://202
    --reverse-proxy=true
    # nur der Proxy erreicht das Tor (eigenes Netz, keine Ports): seine X-Forwarded-* gelten
    --trusted-proxy-ip=0.0.0.0/0
    --trusted-proxy-ip=::/0
    --real-client-ip-header=X-Forwarded-For
    --skip-provider-button=true
    --set-xauthrequest=true
    --cookie-name=_automato_tor
    --cookie-secure=true
    --cookie-samesite=lax
    --cookie-expire=12h
    --cookie-refresh=5m
    "--whitelist-domain=.$DOMAIN"
    --silence-ping-logging=true
  )
  [ -z "$INTERN" ] || args+=("--whitelist-domain=$INTERN" "--whitelist-domain=.$INTERN")
  for rolle in $rollen; do args+=("--allowed-role=$rolle"); done
  [ -z "$KC_CA" ] || args+=(--provider-ca-file=/etc/automato-tor/ca.crt --use-system-trust-store=true)
  printf '  %s:\n    <<: *tor\n    env_file: ["%s"]\n' "$dienst" "$(tor_env "$realm")"
  [ -z "$KC_CA" ] || printf '    volumes: ["%s/%s:/etc/automato-tor/ca.crt:ro"]\n' "$FRONT_PKI" "$KC_CA"
  printf '    command:\n'
  for arg in "${args[@]}"; do printf '      - "%s"\n' "$arg"; done
}

schreibe_tor_compose() {  # je Realm ein oauth2-proxy (Client automato-front), im Netz des Proxys
  local f realm n=0
  f="$(gen)/tor/compose.yml"
  if ! tor_an; then
    [ ! -f "$f" ] || mv -f "$f" "$f.aus"  # tor_starten hält die Container an
    return 0
  fi
  install -d -m 755 "$(gen)/tor"
  {
    printf '# Erzeugt von deploy/front/front.sh (Anmelde-Tor, ADR 0051) – nicht von Hand ändern\n'
    printf 'name: automato-tor\n'
    printf 'x-tor: &tor\n  image: %s\n  restart: unless-stopped\n  read_only: true\n' "$TOR_IMAGE"
    printf '  security_opt: ["no-new-privileges:true"]\n  cap_drop: ["ALL"]\n  networks: [front]\n'
    printf 'services:\n'
    for realm in $(tor_realms); do
      tor_dienst_yaml "$(tor_dienst "$realm")" "$realm" ""
    done
    if [ -n "$INTERN" ] && [ -n "$HAUPT_REALM" ] && tor_bereit "$HAUPT_REALM"; then
      tor_dienst_yaml tor-intern "$HAUPT_REALM" "$INTERN_ROLLEN"
    fi
    printf 'networks:\n  front:\n    name: automato-front-tor\n    external: true\n'
  } >"$f.neu"
  n=$(grep -c '^  tor-' "$f.neu" || true)
  if [ "$n" -gt 0 ]; then mv -f "$f.neu" "$f"; else rm -f "$f.neu"; [ ! -f "$f" ] || mv -f "$f" "$f.aus"; fi
}

tor_starten() {  # Container des Tors nach compose.yml (bzw. anhalten, wenn keiner mehr nötig ist)
  local f aus
  f="$(gen)/tor/compose.yml"
  aus="$f.aus"
  if [ -f "$aus" ]; then
    $DOCKER compose -p automato-tor -f "$aus" down --remove-orphans >/dev/null 2>&1 || true
    rm -f "$aus"
    ok "Anmelde-Tor: nicht mehr benötigte Container angehalten"
  fi
  tor_an || return 0
  if [ ! -f "$f" ]; then
    warn "Anmelde-Tor an, aber kein Realm mit Client-Secret – alle Adressen gesperrt (front.sh tor)"
    return 0
  fi
  if ! proxy_laeuft; then
    info "Anmelde-Tor startet mit dem Proxy (danach: front.sh erzeugen)"
    return 0
  fi
  local meldung r d neu=()
  # neues Client-Secret (von Hand bzw. aus dem Cockpit, 1.13.0): den Container dieses Realms neu
  # erzeugen – oauth2-proxy liest seine env_file nur beim Start; zuerst, damit der Rest danach
  # nichts mehr neu startet
  for r in ${TOR_NEU:-}; do
    for d in "$(tor_dienst "$r")" tor-intern; do
      [ "$d" != tor-intern ] || [ "$r" = "$HAUPT_REALM" ] || continue
      if grep -qx "  $d:" "$f"; then neu+=("$d"); fi
    done
  done
  if [ "${#neu[@]}" -gt 0 ]; then
    if meldung=$($DOCKER compose -p automato-tor -f "$f" up -d --no-deps --force-recreate "${neu[@]}" 2>&1); then
      ok "Anmelde-Tor: ${neu[*]} mit neuem Client-Secret gestartet"
    else
      fehler "Anmelde-Tor: ${neu[*]} nicht neu gestartet: ${meldung##*$'\n'}"
    fi
  fi
  if meldung=$($DOCKER compose -p automato-tor -f "$f" up -d --remove-orphans 2>&1); then
    ok "Anmelde-Tor läuft ($(grep -c '^  tor-' "$f") Container)"
  else
    fehler "Anmelde-Tor nicht gestartet: ${meldung##*$'\n'}"
    info "Netz automato-front-tor fehlt? Proxy mit ≥ 1.4.0 neu starten: docker compose --profile core --profile proxy up -d proxy"
  fi
}

werkzeuge() {  # Kurzliste der Werkzeuge eines Kunden
  local k=$1 w=""
  lies_kunde "$k"
  [ -z "$NETBOX" ] || [ "$NB" != ja ] || w="NetBox"
  [ "$DOC" != ja ] || w="${w:+$w, }Doku"
  if [ "$UM" = ja ]; then
    if [ -n "$UM_ZIEL" ]; then w="${w:+$w, }Magister"; else w="${w:+$w, }Magister (aus)"; fi
  fi
  if [ "$CMDB_AN" = ja ]; then
    if [ -n "$CMDB" ]; then w="${w:+$w, }CMDB"; else w="${w:+$w, }CMDB (aus)"; fi
  fi
  printf '%s' "${w:-–}"
}

# --- Befehle -----------------------------------------------------------------------------------
cmd_kunde() {
  root_noetig
  lade_conf
  local k=${1:-} name=${2:-} doc=nein nb=ja um=nein cmdb=nein realm="" zugriff=ueberall netz=""
  kurzname "$k"
  am_cockpit_vorbei kunde "$k"
  nur_befehl "$k"
  [ -n "$name" ] && [[ "$name" != --* ]] || abbruch "Verwendung: kunde <kunde> \"<Anzeigename>\" [--doc] [--um] [--cmdb] [--ohne-nb] [--realm <realm>] [--zugriff ueberall|intern|\"<netz> …\"]"
  shift 2
  while [ $# -gt 0 ]; do
    case "$1" in
      --doc) doc=ja ;;
      --um) um=ja ;;
      --cmdb) cmdb=ja ;;
      --ohne-nb) nb=nein ;;
      --realm) realm=${2:-}; realm_pruefen "$realm"; shift ;;
      --zugriff)
        case "${2:-}" in
          ueberall | intern) zugriff=$2 ;;
          *) zugriff=netze netz=$(printf '%s' "${2:-}" | tr ',' ' ' | xargs) ;;
        esac
        zugriff_pruefen "$zugriff" "$netz"
        shift
        ;;
      *) abbruch "unbekannte Angabe '$1' (--doc, --um, --cmdb, --ohne-nb, --realm <realm>, --zugriff …)" ;;
    esac
    shift
  done
  install -d -m 750 "$(kdir)"
  printf 'NAME=%q\nDOC=%q\nNB=%q\nUM=%q\nCMDB_AN=%q\nREALM=%q\nZUGRIFF=%q\nNETZE=%q\n' \
    "$name" "$doc" "$nb" "$um" "$cmdb" "$realm" "$zugriff" "$netz" >"$(kdir)/$k.conf"
  titel "Kunde $k im Front-Proxy"
  ZUGRIFF=$zugriff NETZE=$netz
  ok "Zugriff: $(zugriff_text)"
  ok "Startseite https://$k.$DOMAIN"
  if [ "$nb" != ja ]; then
    info "NetBox: aus (--ohne-nb)"
  elif [ -n "$NETBOX" ]; then
    ok "NetBox https://$k-nb.$DOMAIN → $NETBOX"
  else
    warn "AUTOMATO_FRONT_NETBOX fehlt – keine Weiterleitung zur NetBox"
  fi
  if [ "$doc" = ja ]; then
    ok "Dokumentation https://$k-doc.$DOMAIN"
    info "Keycloak (Client automato-web): Valid redirect URI https://$k-doc.$DOMAIN/* und Web origin https://$k-doc.$DOMAIN ergänzen"
  fi
  [ "$um" != ja ] || { [ -n "$UM_ZIEL" ] && ok "Magister https://$k-um.$DOMAIN → $UM_ZIEL (Mandant mit dieser Adresse)" || warn "--um gesetzt, aber weder AUTOMATO_FRONT_MAGISTER noch _UEBRIGE – bleibt aus"; }
  [ "$cmdb" != ja ] || { [ -n "$CMDB" ] && ok "CMDB https://$k-cmdb.$DOMAIN → $CMDB" || warn "--cmdb gesetzt, aber AUTOMATO_FRONT_CMDB leer – bleibt aus"; }
  if tor_an; then
    if [ -z "$realm" ]; then warn "ohne --realm gesperrt (Anmelde-Tor an)"
    elif ! tor_bereit "$realm"; then warn "Realm $realm ohne Client-Secret – gesperrt bis: front.sh tor $realm"
    else ok "Anmeldung über Realm $realm"; fi
  fi
  cmd_erzeugen
}

cmd_link() {
  root_noetig
  lade_conf
  local k=${1:-} t=${2:-} u=${3:-} b=${4:-}
  kurzname "$k"
  am_cockpit_vorbei link "$k"
  nur_befehl "$k"
  [ -f "$(kdir)/$k.conf" ] || abbruch "Kunde '$k' unbekannt (erst: kunde $k \"<Name>\")"
  [ -n "$t" ] && [ -n "$u" ] || abbruch "Verwendung: link <kunde> <titel> <url> [beschreibung]"
  [[ "$u" =~ ^https?:// ]] || abbruch "URL muss mit https:// beginnen"
  [[ "$t$u$b" != *$'\t'* ]] || abbruch "keine Tabulatoren in Titel, URL oder Beschreibung"
  printf '%s\t%s\t%s\n' "$t" "$u" "$b" >>"$(kdir)/$k.links.tsv"
  ok "Kachel „$t“ auf https://$k.$DOMAIN"
  cmd_erzeugen
}

cmd_entfernen() {
  root_noetig
  lade_conf
  local k=${1:-} ja=${2:-}
  kurzname "$k"
  am_cockpit_vorbei entfernen "$k"
  nur_befehl "$k"
  [ -f "$(kdir)/$k.conf" ] || abbruch "Kunde '$k' unbekannt"
  [ "$ja" = "--ja" ] || abbruch "nimmt Startseite und Weiterleitungen von '$k' aus dem Front-Proxy (NetBox und Daten bleiben) – bestätigen mit: entfernen $k --ja"
  rm -f "$(kdir)/$k.conf" "$(kdir)/$k.links.tsv"
  ok "$k aus dem Front-Proxy genommen"
  cmd_erzeugen
}

cmd_liste() {
  lade_conf
  local k
  local quelle gesperrt=""
  printf '%-20s %-28s %-16s %-14s %-8s %s\n' KUNDE WERKZEUGE REALM ZUGRIFF QUELLE STARTSEITE
  for k in $(kunden); do
    lies_kunde "$k"
    [ "$SPERRE" != ja ] || gesperrt+="$k "
    case "$QUELLE" in
      oberflaeche) quelle=Portal ;;
      cockpit) quelle=Cockpit ;;
      *) quelle=Hand ;;
    esac
    printf '%-20s %-28s %-16s %-14s %-8s https://%s.%s\n' "$k" "$(werkzeuge "$k")" "${REALM:-–}" \
      "$(zugriff_text | cut -c1-14)" "$quelle" "$k" "$DOMAIN"
  done
  [ -z "$gesperrt" ] || printf '\nZugang gesperrt (zum Entfernen vorgemerkt, ADR 0059): %s\n' "$gesperrt"
}

pruefe_zertifikat() {  # $1 Datei-Name ohne Endung, $2.. Namen, die es abdecken muss
  local n=$1 crt key san name
  shift
  crt=$FRONT_PKI/$n.crt
  key=$FRONT_PKI/$n.key
  if [ ! -s "$crt" ] || [ ! -s "$key" ]; then
    fehler "Zertifikat fehlt: deploy/proxy/pki/$n.crt und $n.key"
    return
  fi
  if [ "$(openssl x509 -in "$crt" -noout -pubkey 2>/dev/null)" != "$(openssl pkey -in "$key" -pubout 2>/dev/null)" ]; then
    fehler "$n.crt passt nicht zu $n.key (eigenes Zertifikat muss zuerst in der Datei stehen)"
    return
  fi
  san=$(openssl x509 -in "$crt" -noout -ext subjectAltName 2>/dev/null || true)
  for name in "$@"; do
    [[ "$san" == *"DNS:$name"* ]] || fehler "$n.crt deckt $name nicht ab"
  done
  if openssl x509 -in "$crt" -noout -checkend $((30 * 86400)) >/dev/null 2>&1; then
    ok "$n.crt gültig bis $(openssl x509 -in "$crt" -noout -enddate | cut -d= -f2)"
  else
    warn "$n.crt läuft in weniger als 30 Tagen ab (bzw. ist abgelaufen)"
  fi
}

# --- Adressen und Ziele (1.9.2) --------------------------------------------------------------------
# Kommt eine Adresse hier an (DNS), kennt der Proxy sie (sonst „Unbekannte Adresse“), deckt das
# Zertifikat sie ab – und erreicht der Proxy, wohin er weiterleitet?
eigene_ips() {  # IPv4-Adressen dieser VM ohne Docker-Brücken, mit Leerzeichen
  local ips=${FRONT_LOKALE_IPS:-} liste=()
  if [ -z "$ips" ]; then
    ips=$(ip -o -4 addr show scope global 2>/dev/null |
      awk '$2 !~ /^(docker|br-|veth|virbr)/ {split($4, a, "/"); print a[1]}' | tr '\n' ' ' || true)
  fi
  [ -n "$ips" ] || ips=$(hostname -I 2>/dev/null || true)
  read -ra liste <<<"$ips"
  printf '%s\n' "${liste[@]}" | grep -E '^[0-9]+(\.[0-9]+){3}$' | tr '\n' ' ' | sed 's/ $//' || true
}

dns_v4() {  # A-Einträge eines Namens aus Sicht dieser VM, mit Leerzeichen (leer = unbekannt)
  $GETENT ahostsv4 "$1" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ' | sed 's/ $//' || true
}

ist_lokal() {  # $1 Adressen (mit Leerzeichen): ist eine davon diese VM?
  local a
  for a in $1; do [[ " $LOKAL " == *" $a "* ]] && return 0; done
  return 1
}

deckt() {  # $1 Zertifikat, $2 Name: steht der Name oder sein Wildcard im Zertifikat?
  local namen
  namen=$(openssl x509 -in "$1" -noout -ext subjectAltName 2>/dev/null | tr ',' '\n' | sed -n 's/^ *DNS://p' || true)
  grep -qxF "$2" <<<"$namen" || grep -qxF "*.${2#*.}" <<<"$namen"
}

ui_zertifikat() {  # Zertifikat der Oberfläche auf der VM (leer = von Caddy selbst erzeugt)
  local crt=${AUTOMATO_UI_TLS:-}
  crt=${crt%% *}
  case "$crt" in
    /etc/caddy/pki/*) printf '%s/%s' "$FRONT_PKI" "${crt#/etc/caddy/pki/}" ;;
  esac
}

adressen() {  # je Zeile Name<TAB>Zweck<TAB>Zertifikat: jede Adresse, die dieser Proxy bedient
  local k ui=${AUTOMATO_UI_HOST:-} c=$FRONT_PKI/mgmt.crt
  if [ -n "$ui" ] && [ "$ui" != localhost ] && ! [[ "$ui" =~ ^[0-9.]+$|: ]]; then
    printf '%s\tOberfläche von Automato\t%s\n' "$ui" "$(ui_zertifikat)"
  fi
  if [ -n "$INTERN" ]; then
    printf 'start.%s\tPortal\t%s\n' "$INTERN" "$FRONT_PKI/mgmt-int.crt"
    intern_geteilt ||
      printf '%s\tVerwaltung, leitet aufs Portal\t%s\n' "$INTERN" "$FRONT_PKI/mgmt-int.crt"
  fi
  for k in $(kunden); do
    lies_kunde "$k"
    printf '%s.%s\t%s: Startseite\t%s\n' "$k" "$DOMAIN" "$k" "$c"
    if [ -n "$NETBOX" ] && [ "$NB" = ja ]; then printf '%s-nb.%s\t%s: NetBox\t%s\n' "$k" "$DOMAIN" "$k" "$c"; fi
    if [ "$DOC" = ja ]; then printf '%s-doc.%s\t%s: Automato\t%s\n' "$k" "$DOMAIN" "$k" "$c"; fi
    if [ "$UM" = ja ] && [ -n "$UM_ZIEL" ]; then
      printf '%s-um.%s\t%s: Magister\t%s\n' "$k" "$DOMAIN" "$k" "$c"
    fi
    if [ "$CMDB_AN" = ja ] && [ -n "$CMDB" ]; then
      printf '%s-cmdb.%s\t%s: CMDB\t%s\n' "$k" "$DOMAIN" "$k" "$c"
    fi
  done
  return 0
}

im_proxy() {  # $1 Name: hat der Proxy einen eigenen Eintrag (sonst antwortet „Unbekannte Adresse“)?
  [ "$1" != "${AUTOMATO_UI_HOST:-}" ] || return 0  # Caddyfile von Automato
  grep -qxF "$1 {" "$(gen)"/sites/*.caddy 2>/dev/null
}

pruefe_wildcard() {  # $1 Domain, $2 kunden|intern: wohin zeigt ein Name, den es nicht gibt?
  local ips
  ips=$(dns_v4 "gibt-es-nicht-front-pruefen.$1")
  if [ -z "$ips" ]; then
    info "kein Wildcard *.$1 im DNS – jede Adresse oben braucht einen eigenen Eintrag"
  elif ! ist_lokal "$ips"; then
    info "*.$1 → $ips (nicht diese VM) – die Adressen oben brauchen je einen eigenen Eintrag"
    [ "$2" != kunden ] || [ -z "$UEBRIGE" ] ||
      info "übrige Namen gehen so direkt an $ips, nicht über diesen Proxy (AUTOMATO_FRONT_UEBRIGE ungenutzt)"
  elif [ "$2" = kunden ] && [ -n "$UEBRIGE" ]; then
    ok "*.$1 zeigt auf diese VM – übrige Namen gehen an $UEBRIGE (AUTOMATO_FRONT_UEBRIGE)"
    pruefe_direkt "$1"
  elif [ "$2" = kunden ]; then
    warn "*.$1 zeigt auf diese VM: jeder andere Name unter $1 landet hier und bekommt „Unbekannte Adresse“"
    info "richtig nur, wenn die Domain allein diesem Front-Proxy gehört. Gehören andere Namen einer"
    info "Plattform dahinter (z. B. Kundenseiten von Magister): AUTOMATO_FRONT_UEBRIGE=<ip>[:port]"
    info "(übrige Namen an sie) – oder den Wildcard dort lassen und je Adresse oben einen Eintrag"
  else
    ok "*.$1 zeigt auf diese VM"
  fi
}

# Namen der Plattform dahinter, die nicht über den Front-Proxy gehen (Magister: Konsole :4444 nur
# intern, Connector :46200 für die AD-Agenten) – fängt der Wildcard sie ein, sind sie weg (1.10.0)
DIREKT_NAMEN="konsole connect"

pruefe_direkt() {  # $1 Domain
  local n ips ziel=${UEBRIGE%:*}
  for n in $DIREKT_NAMEN; do
    ips=$(dns_v4 "$n.$1")
    [ -n "$ips" ] || continue
    if ist_lokal "$ips"; then
      warn "$n.$1 zeigt auf diese VM (Wildcard) – gehört direkt zur Plattform dahinter (Magister: Konsole :4444, Connector :46200): eigener A-Eintrag auf $ziel"
    else
      ok "$n.$1 → $ips (direkt, nicht über den Front-Proxy)"
    fi
  done
}

pruefe_adressen() {
  local name zweck crt ips anzahl=0
  LOKAL=$(eigene_ips)
  titel "Adressen (DNS dieser VM; diese VM: ${LOKAL:-unbekannt})"
  while IFS=$'\t' read -r name zweck crt; do
    anzahl=$((anzahl + 1))
    if ! im_proxy "$name"; then
      fehler "$name ($zweck): fehlt im Front-Proxy – front.sh erzeugen"
      continue
    fi
    if [ -n "$crt" ] && [ -s "$crt" ] && ! deckt "$crt" "$name"; then
      fehler "$name ($zweck): Zertifikat ${crt##*/} deckt den Namen nicht ab"
      continue
    fi
    ips=$(dns_v4 "$name")
    if [ -z "$ips" ]; then
      warn "$name ($zweck): nicht im DNS – A-Eintrag auf ${LOKAL:-diese VM}"
    elif ist_lokal "$ips"; then
      ok "$name → $ips ($zweck)"
    elif [ -n "$WAF" ]; then
      info "$name → $ips ($zweck; über WAF bzw. VIP)"
    else
      warn "$name → $ips, nicht diese VM – $zweck kommt hier nicht an"
    fi
  done < <(adressen)
  [ "$anzahl" -gt 0 ] || info "noch keine Adressen"
  pruefe_wildcard "$DOMAIN" kunden
  [ -z "$INTERN" ] || intern_geteilt || pruefe_wildcard "$INTERN" intern
}

vom_proxy() {  # $1 URL: Antwort aus dem Proxy-Container – ok, HTTP-Code, exec (geht nicht) oder aus
  local aus
  if aus=$(dc exec -T proxy wget -q -T 5 -O /dev/null --no-check-certificate "$1" 2>&1); then
    echo ok
  elif [[ "$aus" =~ HTTP/[0-9.]+\ ([0-9]{3}) ]]; then
    echo "${BASH_REMATCH[1]}"
  elif [[ "$aus" == *"OCI runtime exec failed"* ]]; then
    echo exec
  else
    echo aus
  fi
}

ziel_melden() {  # $1 Ziel, $2 URL
  local code
  code=$(vom_proxy "$2")
  case "$code" in
    ok) ok "$1 erreichbar ($2)" ;;
    exec) warn "$1: im Proxy-Container lässt sich nichts starten (docker exec) – Proxy neu erzeugen: docker compose up -d --force-recreate proxy" ;;
    aus) fehler "$1 nicht erreichbar ($2) – Name, Firewall bzw. läuft der Dienst?" ;;
    5*) fehler "$1 antwortet mit HTTP $code ($2)" ;;
    *) ok "$1 erreichbar ($2, HTTP $code)" ;;
  esac
}

# Weg des NetBox-Syncs (1.10.0): aus dem Orchestrator über DNS und Front-Proxy bis zur NetBox – mit
# einem absichtlich ungültigen Token, NetBox antwortet dann selbst (JSON-403); Vertrauen wie der
# Dienst (zusätzliche CAs aus deploy/trust)
NB_WEG_PY=$(cat <<'PY'
import ssl, sys
try:
    from sot_gateway import trust
    trust.zusaetzliche_cas()
except Exception:
    pass
import httpx
try:
    r = httpx.get(sys.argv[1] + "/api/status/", timeout=8,
                  headers={"Authorization": "Token automato-statuspruefung", "Accept": "application/json"})
except httpx.ConnectError as exc:
    tls = isinstance(exc.__cause__, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(exc)
    print("tls" if tls else "weg")
    sys.exit(0)
except httpx.HTTPError:
    print("weg")
    sys.exit(0)
try:
    body = r.json()
except ValueError:
    body = None
if r.status_code in (401, 403) and isinstance(body, dict) and "detail" in body:
    print("ok")
elif "Keine NetBox" in r.text or (r.status_code == 200 and not r.text.strip()):
    print("fehlt")
elif "Unbekannte Adresse" in r.text or "Zugriff nur" in r.text:
    print("gesperrt")
else:
    print(f"http_{r.status_code}")
PY
)

automato_zur_netbox() {  # $1 Kunde: erreicht Automato (Orchestrator) seine NetBox über den Front-Proxy?
  local k=$1 url="https://$1-nb.$DOMAIN" antwort
  antwort=$(dc exec -T orchestrator python -c "$NB_WEG_PY" "$url" 2>/dev/null | tail -1 || true)
  case "$antwort" in
    ok) ok "$k: Automato erreicht seine NetBox über den Front-Proxy (Weg des NetBox-Syncs)" ;;
    tls) fehler "$k: Automato vertraut dem Zertifikat von $url nicht – CA nach deploy/trust/, dann docker compose up -d --force-recreate orchestrator collector" ;;
    weg) fehler "$k: Automato erreicht $url nicht (DNS im Container bzw. Weg zur eigenen Adresse)" ;;
    gesperrt) fehler "$k: Front-Proxy lässt Automato nicht zur NetBox – front.sh erzeugen (Docker-Netze von Automato)" ;;
    fehlt) fehler "$k: auf dem NetBox-Host gibt es keine NetBox für $k – dort: netbox-host.sh neu $k" ;;
    http_*) warn "$k: NetBox über den Front-Proxy antwortet unerwartet (${antwort#http_}) – $url/api/status/" ;;
    *) info "$k: Weg Automato → NetBox nicht geprüft (Orchestrator läuft nicht bzw. docker exec geht nicht)" ;;
  esac
}

pruefe_uebrige() {  # Ziel der übrigen Namen: mit einem Namen, den es nicht gibt, wie der Proxy (SNI, CA)
  local host=${UEBRIGE%:*} port=443 name="gibt-es-nicht-front-pruefen.$DOMAIN" ca=() code
  [ "$host" = "$UEBRIGE" ] || port=${UEBRIGE##*:}
  if [ -n "$UEBRIGE_CA" ]; then
    [ -s "$FRONT_PKI/$UEBRIGE_CA" ] || { fehler "Übrige Namen: CA fehlt oder ist leer (deploy/proxy/pki/$UEBRIGE_CA)"; return; }
    ca=(--cacert "$FRONT_PKI/$UEBRIGE_CA")
  fi
  code=$($CURL -s --noproxy '*' "${ca[@]}" -o /dev/null -w '%{http_code}' --resolve "$name:$port:$host" \
    "https://$name:$port/" 2>/dev/null || true)
  case "$code" in
    000 | "") fehler "Übrige Namen: $UEBRIGE nicht erreichbar bzw. Zertifikat nicht vertraut (Firewall, AUTOMATO_FRONT_UEBRIGE_CA)" ;;
    5*) warn "Übrige Namen: $UEBRIGE antwortet mit HTTP $code (für einen Namen, den es nicht gibt)" ;;
    *) ok "Übrige Namen: $UEBRIGE erreichbar, Zertifikat vertraut (HTTP $code)" ;;
  esac
}

pruefe_ziele() {
  titel "Ziele (vom Proxy aus)"
  if ! proxy_laeuft; then
    info "Proxy läuft nicht – Ziele nicht geprüft"
    return 0
  fi
  local schema=http
  [ "${AUTOMATO_INTERNAL_TLS:-off}" != on ] || schema=https
  ziel_melden "Automato (Oberfläche, -doc)" "$schema://web-ui:5173/api/health"
  [ -z "$MAGISTER" ] || ziel_melden "Magister (-um)" "$MAGISTER/"
  [ -z "$CMDB" ] || ziel_melden "CMDB (-cmdb)" "$CMDB/"
  [ -z "$UEBRIGE" ] || pruefe_uebrige
  [ -z "$NETBOX" ] || info "NetBox-Host $NETBOX: je Kunde unter „Kunden“"
  tor_an || return 0
  info "Keycloak: unter „Anmelde-Tor“"
}

cmd_adressen() {
  root_noetig
  lade_conf
  pruefe_adressen
  echo
  if [ "$FEHLER" -gt 0 ]; then echo "Es gibt Fehler (siehe oben)."; return 1; fi
  if [ "$WARNUNGEN" -gt 0 ]; then echo "Alles in Ordnung ($WARNUNGEN Warnungen)."; else echo "Alles in Ordnung."; fi
}

cmd_pruefen() {
  root_noetig
  lade_conf
  titel "Einstellungen"
  ok "Kunden-Adressen *.$DOMAIN nur aus: $KUNDEN_ALLOW"
  [ -n "$WAF" ] && ok "WAF: $WAF (X-Forwarded-For wird übernommen)" || warn "AUTOMATO_FRONT_WAF leer – Adresse der Benutzer unbekannt"
  if intern_geteilt; then
    ok "Portal start.$INTERN (Domain der Kunden) nur aus: $MGMT${WAF:+ (nie über die WAF)}"
  elif [ -n "$INTERN" ]; then
    ok "Intern start.$INTERN und $INTERN nur aus: $MGMT${WAF:+ (nie über die WAF)}"
  else
    warn "AUTOMATO_FRONT_DOMAIN_INT leer – kein Portal"
  fi
  [ -n "$NETBOX" ] && ok "NetBox-Host: $NETBOX" || warn "AUTOMATO_FRONT_NETBOX leer – keine Weiterleitung zur NetBox"
  if [ -n "$MAGISTER" ]; then
    ok "Magister (-um): $UM_ZIEL${UM_CA:+ (CA $UM_CA)}"
  elif [ -n "$UEBRIGE" ]; then
    ok "Magister (-um): wie die übrigen Namen an $UEBRIGE"
  else
    info "Magister (-um) aus (AUTOMATO_FRONT_MAGISTER bzw. _UEBRIGE leer)"
  fi
  if [ -n "$UEBRIGE" ]; then
    ok "Übrige Namen unter $DOMAIN an: $UEBRIGE${UEBRIGE_CA:+ (CA $UEBRIGE_CA)}"
  else
    info "Übrige Namen unter $DOMAIN: „Unbekannte Adresse“ (AUTOMATO_FRONT_UEBRIGE leer)"
  fi
  [ -n "$CMDB" ] && ok "CMDB: $CMDB" || info "CMDB (-cmdb) aus"
  # Kunden-Adressen aus jedem Netz (z. B. FortiGate-VIP, die die Adresse des Besuchers weitergibt):
  # dann muss die Oberfläche selbst auf das Management-Netz beschränkt sein (1.5.0)
  if [[ " $KUNDEN_ALLOW " == *" 0.0.0.0/0 "* ]] && [ -z "${AUTOMATO_UI_ALLOW:-}" ]; then
    warn "Kunden-Adressen aus jedem Netz, aber AUTOMATO_UI_ALLOW leer – die Oberfläche wäre über ihre Adresse auch von aussen erreichbar"
  fi

  titel "Host-Agent (Kunden aus dem Kunden-Portal)"
  local hand="" k owner
  if $SYSTEMCTL is-active --quiet automato-front-sync.path 2>/dev/null; then
    ok "läuft (automato-front-sync.path)"
  else
    warn "nicht eingerichtet bzw. aus – Änderungen im Kunden-Portal kommen nicht an: sudo deploy/front/front.sh agent"
  fi
  if [ -d "$FRONT_STATE" ]; then
    owner=$(stat -c %u "$FRONT_STATE" 2>/dev/null || true)
    [ "$owner" = "$ORCH_UID" ] || warn "$FRONT_STATE gehört nicht dem Orchestrator (uid $ORCH_UID) – sudo deploy/front/front.sh agent"
  fi
  for k in $(kunden); do aus_oberflaeche "$k" || hand+="$k "; done
  [ -z "$hand" ] || info "von Hand angelegt (nicht im Kunden-Portal): $hand– übernimmt das Portal, sobald dort ein Kunde mit diesem Kurznamen gespeichert wird"

  titel "Zertifikate"
  pruefe_zertifikat mgmt "*.$DOMAIN"
  if intern_geteilt; then
    pruefe_zertifikat mgmt-int "*.$INTERN"
  elif [ -n "$INTERN" ]; then
    pruefe_zertifikat mgmt-int "$INTERN" "*.$INTERN"
  fi

  titel "Proxy"
  if proxy_laeuft; then
    ok "Proxy läuft"
  else
    fehler "Proxy läuft nicht – docker compose --profile core --profile proxy up -d"
  fi
  [ -f "$(gen)/snippets.caddy" ] && ok "Front-Proxy erzeugt" || fehler "noch nicht erzeugt – front.sh erzeugen"
  # Von Docker veröffentlichte Ports laufen an ufw vorbei; ohne Sperre in DOCKER-USER sind 443/8443
  # aus jedem Netz erreichbar (INSTALLATION-PROD 4.3)
  local regeln
  if regeln=$($IPTABLES -S DOCKER-USER 2>/dev/null); then
    if grep -qE -- '-j (DROP|REJECT)' <<<"$regeln"; then
      ok "Firewall: DOCKER-USER sperrt nicht freigegebene Netze"
    else
      warn "Firewall: DOCKER-USER ohne Sperre – 443/8443 aus jedem Netz erreichbar (INSTALLATION-PROD 4.3)"
    fi
  else
    info "Firewall: DOCKER-USER nicht lesbar – Sperre von Hand prüfen (iptables -L DOCKER-USER)"
  fi

  pruefe_adressen
  pruefe_ziele

  titel "Anmelde-Tor"
  if tor_an; then
    ok "an – Keycloak $KC (Browser), $KC_INTERN (Proxy)"
    tor_uebersicht
    local r laufend soll kennung zeile
    for r in $TOR_BEREIT; do ok "Realm $r: Client-Secret hinterlegt ($(tor_herkunft "$r"))"; done
    for r in $TOR_FEHLT; do fehler "Realm $r: Client-Secret fehlt – front.sh tor $r"; done
    # 1.13.0 (ADR 0055): Empfangsschlüssel und was der letzte sync aus dem Cockpit verworfen hat
    kennung=$(empfang_kennung)
    if [ -n "$kennung" ]; then
      ok "Empfangsschlüssel für Client-Secrets aus dem Cockpit: ${kennung:0:16}…"
    else
      warn "kein Empfangsschlüssel für Client-Secrets aus dem Cockpit – front.sh erzeugen"
    fi
    while IFS= read -r zeile; do
      [ -z "$zeile" ] || warn "Client-Secret aus dem Cockpit verworfen: $zeile – im Cockpit neu versiegeln lassen"
    done < <(python3 -I -c 'import json, re, sys
try:
    tor = json.load(open(sys.argv[1], encoding="utf-8")).get("tor") or {}
except (OSError, ValueError, AttributeError):
    tor = {}
for v in tor.get("verworfen") or [] if isinstance(tor, dict) else []:
    if isinstance(v, dict):
        r, g = str(v.get("realm") or "?"), str(v.get("grund") or "")
        if re.fullmatch(r"[A-Za-z0-9._?-]{1,64}", r) and re.fullmatch(r"[a-z_]{1,40}", g):
            print(f"Realm {r} ({g})")' "$FRONT_STATE/status.json" 2>/dev/null || true)
    for r in $TOR_GESPERRT; do warn "Kunde $r gesperrt (kein Realm bzw. kein Client-Secret) – front.sh tor"; done
    # die Container des Tors holen Token und Schlüssel bei Keycloak – wie der Proxy (gleiches Netz,
    # gleiche Namensauflösung, ohne extra_hosts des Orchestrators)
    if proxy_laeuft; then
      for r in $TOR_BEREIT; do
        if dc exec -T proxy wget -q -T 5 -O /dev/null "$KC_INTERN/realms/$r/.well-known/openid-configuration" >/dev/null 2>&1; then
          ok "Keycloak für Realm $r vom Proxy aus erreichbar"
        else
          fehler "Keycloak $KC_INTERN (Realm $r) vom Proxy aus nicht erreichbar – Name, Firewall bzw. AUTOMATO_FRONT_KEYCLOAK_INTERN"
        fi
      done
    fi
    if [ -f "$(gen)/tor/compose.yml" ]; then
      soll=$(grep -c '^  tor-' "$(gen)/tor/compose.yml" || true)
      laufend=$($DOCKER compose -p automato-tor -f "$(gen)/tor/compose.yml" ps --status running --services 2>/dev/null | grep -c '^tor-' || true)
      if [ "$laufend" -ge "$soll" ]; then
        ok "Anmelde-Tor läuft ($laufend Container)"
      else
        fehler "Anmelde-Tor: $laufend von $soll Containern laufen – front.sh erzeugen; Log: docker compose -p automato-tor logs"
      fi
    fi
  else
    warn "aus – Startseiten und Werkzeuge ohne Anmeldung davor (AUTOMATO_FRONT_TOR=an, ADR 0051)"
  fi

  # ADR 0059: gesperrte Kunden und Altlasten – nur gemeldet, nie still entfernt
  titel "Abbau und Altlasten"
  local zeile_abbau
  front_ist
  for k in $IST_GESPERRT; do info "$k: Zugang gesperrt – zum Entfernen vorgemerkt (Cockpit), Daten bleiben"; done
  for r in $IST_VERWAIST; do
    warn "Realm $r: Tor-Geheimnis ohne Kunden ($FRONT_TOR_DIR/$r.env) – bleibt; entfernt der Abbau im Cockpit bzw. von Hand"
  done
  for r in $IST_FREMD; do
    warn "Container $r des Anmelde-Tors gehört zu keinem Realm – front.sh erzeugen hält ihn an (bei laufendem Proxy)"
  done
  while IFS= read -r zeile_abbau; do
    [ -z "$zeile_abbau" ] || warn "Tor-Geheimnis nicht entfernt: $zeile_abbau"
  done < <(python3 -I -c 'import json, re, sys
try:
    ist = json.load(open(sys.argv[1], encoding="utf-8")).get("ist") or {}
except (OSError, ValueError, AttributeError):
    ist = {}
for v in ist.get("tor_entfernen_abgelehnt") or [] if isinstance(ist, dict) else []:
    if isinstance(v, dict):
        r, g = str(v.get("realm") or "?"), str(v.get("grund") or "")
        if re.fullmatch(r"[A-Za-z0-9._?-]{1,64}", r) and re.fullmatch(r"[a-z_]{1,40}", g):
            print(f"Realm {r} ({g})")' "$FRONT_STATE/status.json" 2>/dev/null || true)
  [ -n "$IST_GESPERRT$IST_VERWAIST$IST_FREMD" ] || ok "keine gesperrten Kunden, keine verwaisten Tor-Dateien oder -Container"

  titel "Kunden"
  local code ca=() orch coll
  [ -z "$NETBOX_CA" ] || ca=(--cacert "$FRONT_PKI/$NETBOX_CA")
  for k in $(kunden); do
    lies_kunde "$k"
    code=$($CURL -sk --noproxy '*' -o /dev/null -w '%{http_code}' --resolve "$k.$DOMAIN:443:127.0.0.1" "https://$k.$DOMAIN/" 2>/dev/null || true)
    case "$code" in
      200 | 403) ok "$k: Startseite antwortet (HTTP $code; 403 = gesperrt bzw. diese VM nicht freigegeben)" ;;
      302) ok "$k: Startseite leitet zur Anmeldung (Anmelde-Tor)" ;;
      404) if [ "$ZUGRIFF" != ueberall ]; then
             ok "$k: Startseite antwortet (HTTP 404 – diese VM ausserhalb des Zugriffs: $(zugriff_text))"
           else fehler "$k: Startseite nicht gefunden (HTTP 404) – front.sh erzeugen"; fi ;;
      *) fehler "$k: Startseite antwortet nicht (HTTP ${code:-–})" ;;
    esac
    [ "$ZUGRIFF" = ueberall ] || info "$k: Zugriff $(zugriff_text)"
    if [ "$SPERRE" = ja ]; then  # ADR 0059: alle Adressen antworten „Zugang gesperrt“ (403)
      info "$k: Zugang gesperrt (zum Entfernen vorgemerkt) – Wege zur NetBox nicht geprüft"
      continue
    fi
    [ -n "$NETBOX" ] || continue
    if [ "$NB" != ja ]; then
      info "$k: ohne NetBox (NB=nein)"
      continue
    fi
    # Orchestrator (Prüfen, NetBox-Sync) und zentraler Worker (Einlesen, Abgleich) müssen die
    # NetBox gleich erreichen – extra_hosts in docker-compose.override.yml für beide (1.5.0)
    orch=$(dc exec -T orchestrator getent hosts "$k-nb.$DOMAIN" 2>/dev/null | awk 'NR==1 {print $1}' || true)
    coll=$($DOCKER compose --profile core --profile collectors exec -T collector getent hosts "$k-nb.$DOMAIN" 2>/dev/null | awk 'NR==1 {print $1}' || true)
    if [ -n "$orch" ] && [ -n "$coll" ] && [ "$orch" != "$coll" ]; then
      warn "$k-nb.$DOMAIN: Orchestrator → $orch, zentraler Worker (collector) → $coll – in docker-compose.override.yml extra_hosts für beide gleich setzen (INSTALLATION-FRONT 8)"
    fi
    local nbhost=${NETBOX%:*} nbport=443
    [ "$nbhost" = "$NETBOX" ] || nbport=${NETBOX##*:}
    # wie die Prüfung des Cockpits: ein absichtlich ungültiges Token, NetBox antwortet selbst mit
    # JSON-403 – eine leere Seite bzw. 404 heisst, es gibt die Instanz auf dem NetBox-Host nicht
    local antwort
    antwort=$(mktemp)
    code=$($CURL -s --noproxy '*' "${ca[@]}" -o "$antwort" -w '%{http_code}' \
      -H "Authorization: Token automato-statuspruefung" -H "Accept: application/json" \
      --resolve "$k-nb.$DOMAIN:$nbport:$nbhost" "https://$k-nb.$DOMAIN:$nbport/api/status/" 2>/dev/null || true)
    if [ "$code" = 403 ] && grep -q '"detail"' "$antwort"; then
      ok "$k: NetBox vom Front-Proxy aus erreichbar"
    elif [ "$code" = 403 ]; then
      fehler "$k: NetBox-Host lehnt diese VM ab (403) – ALLOW/FRONT in /opt/netbox-host/host.conf"
    elif [ "$code" = 404 ] || [ "$code" = 200 ]; then
      fehler "$k: auf dem NetBox-Host gibt es keine NetBox für $k – dort: netbox-host.sh neu $k"
      code=fehlt
    else
      fehler "$k: NetBox nicht erreichbar (HTTP ${code:-–}; Zertifikat, Firewall)"
    fi
    rm -f "$antwort"
    # fehlt die NetBox schon auf dem NetBox-Host, käme dieselbe Meldung vom Orchestrator noch einmal (1.11.0)
    if [ "$code" = fehlt ]; then
      info "$k: Weg Automato → NetBox nicht geprüft (erst die NetBox auf dem NetBox-Host anlegen)"
    else
      automato_zur_netbox "$k"
    fi
  done
  [ -n "$(kunden)" ] || info "noch keine Kunden – front.sh kunde <kunde> \"<Name>\""
  echo
  if [ "$FEHLER" -gt 0 ]; then echo "Es gibt Fehler (siehe oben)."; return 1; fi
  if [ "$WARNUNGEN" -gt 0 ]; then echo "Alles in Ordnung ($WARNUNGEN Warnungen)."; else echo "Alles in Ordnung."; fi
}


# --- Host-Agent: Kunden aus der Oberfläche (1.3.0, ADR 0050) ---------------------------------
sync_anwenden() {  # läuft in einer Subshell: abbruch beendet nur diese
  lade_conf
  local soll=$1 zeilen art k a b c d e f g h i j
  local -A gewollt=()
  install -d -m 750 "$(kdir)"
  rm -f "$(kdir)"/*.neu
  # JSON nur lesen und in Zeilen wandeln (Trenner 0x1f) – geprüft wird danach wie bei `kunde`
  zeilen=$(python3 -I - "$soll" <<'PY'
import json, sys
SEP = "\x1f"
def clean(value):
    text = "" if value is None else str(value)
    return "".join(" " if ch in "\t\r\n\x1f" else ch for ch in text)
data = json.load(open(sys.argv[1], encoding="utf-8"))
for kunde in data.get("kunden") or []:
    k = clean(kunde.get("kurzname"))
    flags = ["ja" if kunde.get(t) else "nein" for t in ("doc", "um", "cmdb")]
    zugriff = clean(kunde.get("zugriff") or "ueberall")  # 1.5.0; ältere Oberfläche: überall
    netze = " ".join(clean(n) for n in kunde.get("netze") or [])
    nb = "ja" if kunde.get("nb", True) else "nein"  # 1.11.0; ältere Oberfläche: NetBox an
    quelle = "cockpit" if kunde.get("quelle") == "cockpit" else "oberflaeche"  # 1.11.0
    gesperrt = "ja" if kunde.get("gesperrt") is True else "nein"  # ADR 0059
    print(SEP.join(["K", k, clean(kunde.get("name"))] + flags
                   + [clean(kunde.get("realm")), zugriff, netze, nb, quelle, gesperrt]))
    for link in kunde.get("links") or []:
        print(SEP.join(["L", k, clean(link.get("title")), clean(link.get("url")), clean(link.get("text"))]))
PY
  ) || abbruch "soll.json nicht lesbar ($soll)"
  while IFS=$'\x1f' read -r art k a b c d e f g h i j; do
    case "$art" in
      K)
        kurzname "$k"
        [ -n "$a" ] || abbruch "Kunde '$k' ohne Anzeigename"
        [ -z "$e" ] || realm_pruefen "$e"
        [ "$f" = netze ] || g=""
        zugriff_pruefen "${f:-ueberall}" "$g"
        [ "$h" = nein ] || h=ja
        [ "$i" = cockpit ] || i=oberflaeche
        [ "$j" = ja ] || j=nein
        printf 'NAME=%q\nDOC=%q\nNB=%q\nUM=%q\nCMDB_AN=%q\nREALM=%q\nZUGRIFF=%q\nNETZE=%q\nQUELLE=%q\nSPERRE=%q\n' \
          "$a" "$b" "$h" "$c" "$d" "$e" "${f:-ueberall}" "$g" "$i" "$j" >"$(kdir)/$k.conf.neu"
        : >"$(kdir)/$k.links.tsv.neu"
        gewollt[$k]=1
        ;;
      L)
        [ -n "${gewollt[$k]:-}" ] || continue
        [ -n "$a" ] || abbruch "Kachel von '$k' ohne Titel"
        [[ "$b" =~ ^https:// ]] || abbruch "Kachel „$a“ von '$k': Adresse muss mit https:// beginnen"
        printf '%s\t%s\t%s\n' "$a" "$b" "$c" >>"$(kdir)/$k.links.tsv.neu"
        ;;
      "") ;;
      *) abbruch "soll.json: unbekannte Zeile" ;;
    esac
  done <<<"$zeilen"
  local f
  for f in "$(kdir)"/*.neu; do
    [ -e "$f" ] && mv -f "$f" "${f%.neu}"
  done
  for k in $(kunden); do  # in der Oberfläche entfernt
    if aus_oberflaeche "$k" && [ -z "${gewollt[$k]:-}" ]; then
      rm -f "$(kdir)/$k.conf" "$(kdir)/$k.links.tsv"
      ok "$k aus dem Front-Proxy genommen (in der Oberfläche entfernt)"
    fi
  done
  # 1.13.0 (ADR 0055): versiegelte Client-Secrets aus dem Cockpit – nach den Kunden (Realm eines
  # Kunden aus dem Cockpit), vor dem Erzeugen (Container des Tors mit dem neuen Secret)
  if tor_an; then tor_geheimnisse_uebernehmen "$soll"; fi
  # ADR 0059: Tor-Geheimnisse von Realms ohne Kunden auf Auftrag des Cockpits – nach den Kunden
  # (welche Realms noch genutzt werden), auch mit Tor aus (Reste von früher)
  tor_entfernen_uebernehmen "$soll"
  cmd_erzeugen
  [ "$FEHLER" -eq 0 ] || exit 1
}

tor_uebersicht() {  # setzt TOR_BEREIT (Realms mit Client-Secret), TOR_FEHLT (ohne), TOR_GESPERRT (Kunden),
                    # TOR_COCKPIT (Client-Secret aus dem Cockpit)
  local k
  local -A bereit=() fehlt=()
  TOR_GESPERRT=""
  for k in $(kunden); do
    lies_kunde "$k"
    if [ -z "$REALM" ]; then TOR_GESPERRT+="$k "
    elif tor_bereit "$REALM"; then bereit[$REALM]=1
    else fehlt[$REALM]=1; TOR_GESPERRT+="$k "; fi
  done
  if [ -n "$INTERN" ] && [ -n "$HAUPT_REALM" ]; then
    if tor_bereit "$HAUPT_REALM"; then bereit[$HAUPT_REALM]=1; else fehlt[$HAUPT_REALM]=1; fi
  fi
  TOR_BEREIT="${!bereit[*]}"
  TOR_FEHLT="${!fehlt[*]}"
  TOR_COCKPIT=""  # 1.13.0: Realms, deren Client-Secret zuletzt aus dem Cockpit kam
  for k in "${!bereit[@]}"; do
    if tor_vom_cockpit "$k"; then TOR_COCKPIT+="$k "; fi
  done
}

status_schreiben() {  # $1 = ok|fehler|behalten (nur den Stand des Tors erneuern), $2 = Prüfsumme, $3 = Meldung
  local ziel="$FRONT_STATE/status.json" tmp
  [ -d "$FRONT_STATE" ] || return 0
  tmp=$(mktemp "$FRONT_STATE/.status-XXXXXX")
  # sync wendet in einer Subshell an: Einstellungen hier noch einmal lesen (ohne abzubrechen)
  if [ -z "${TOR:-}" ] && (lade_conf) >/dev/null 2>&1; then lade_conf; fi
  TOR=${TOR:-aus} TOR_BEREIT="" TOR_FEHLT="" TOR_GESPERRT="" TOR_COCKPIT=""
  if [ "$TOR" = an ]; then tor_uebersicht; fi
  front_ist  # ADR 0059: Ist-Stand fürs Inventar (Kunden, gesperrt, Tor-Dateien und -Container)
  # TOR_VERWORFEN (nur in sync): Datei mit „Realm<TAB>Grund“ je verworfenem Geheimnis aus dem Cockpit;
  # TOR_ABBAU (nur in sync, ADR 0059): „entfernt<TAB>Realm“ bzw. „abgelehnt<TAB>Realm<TAB>Grund“
  if ! python3 -I - "$1" "${2:-}" "${3:-}" "$(kunden | tr '\n' ' ')" "$TOR" "$TOR_BEREIT" "$TOR_FEHLT" \
    "$TOR_GESPERRT" "${HAUPT_REALM:-}" "$TOR_COCKPIT" "${TOR_VERWORFEN:-}" "$ziel" \
    "$IST_GESPERRT" "$IST_DATEIEN" "$IST_DIENSTE" "$IST_FREMD" "$IST_VERWAIST" "${TOR_ABBAU:-}" >"$tmp" <<'PY'
import json, sys
from datetime import datetime, timezone
art, summe, meldung, kunden, tor, bereit, fehlt, gesperrt, haupt, cockpit, verworfen_datei, ziel = sys.argv[1:13]
ist_gesperrt, dateien, dienste, fremd, verwaist, abbau_datei = sys.argv[13:19]
abbau = None  # ADR 0059: was dieser sync auf Auftrag entfernt bzw. abgelehnt hat
if abbau_datei:
    try:
        zeilen = open(abbau_datei, encoding="utf-8").read().splitlines()
    except OSError:
        zeilen = []
    abbau = {"tor_entfernt": [], "tor_entfernen_abgelehnt": []}
    for zeile in zeilen:
        teile = zeile.split("\t")
        if teile[0] == "entfernt" and len(teile) == 2:
            abbau["tor_entfernt"].append(teile[1])
        elif teile[0] == "abgelehnt" and len(teile) == 3:
            abbau["tor_entfernen_abgelehnt"].append({"realm": teile[1], "grund": teile[2]})
# Ist-Stand fürs Inventar (ADR 0059): nur Namen, nie Inhalte
ist = {
    "kunden": kunden.split(),
    "gesperrt": ist_gesperrt.split(),
    "tor_realms_dateien": sorted(set(dateien.split())),
    "tor_dienste": sorted(set(dienste.split())),
    "tor_dienste_fremd": sorted(set(fremd.split())),
    "tor_verwaist": sorted(set(verwaist.split())),
    **(abbau or {"tor_entfernt": [], "tor_entfernen_abgelehnt": []}),
}
verworfen = None
if verworfen_datei:
    try:
        zeilen = open(verworfen_datei, encoding="utf-8").read().splitlines()
    except OSError:
        zeilen = []
    verworfen = []
    for zeile in zeilen:
        realm, _, grund = zeile.partition("\t")
        if grund:
            verworfen.append({"realm": realm or None, "grund": grund})
# Anmelde-Tor (1.4.0): welche Realms bereit sind, welchen das Client-Secret fehlt, wer gesperrt ist;
# 1.13.0 (ADR 0055): welche Client-Secrets aus dem Cockpit kamen bzw. verworfen wurden (nie Inhalte)
stand_tor = {
    "an": tor == "an",
    "realms_bereit": sorted(bereit.split()),
    "realms_ohne_secret": sorted(fehlt.split()),
    "gesperrt": gesperrt.split(),
    "haupt_realm": haupt,
    "vom_cockpit": sorted(cockpit.split()),
    "verworfen": verworfen or [],
}
if art == "behalten":  # nach erzeugen bzw. tor-empfang: das Ergebnis des letzten sync bleibt
    try:
        alt = json.load(open(ziel, encoding="utf-8"))
    except (OSError, ValueError):
        sys.exit(1)
    if not isinstance(alt, dict):
        sys.exit(1)
    if verworfen is None:
        vorher = alt.get("tor") if isinstance(alt.get("tor"), dict) else {}
        liste = vorher.get("verworfen")
        stand_tor["verworfen"] = liste if isinstance(liste, list) else []
    if abbau is None:  # was der letzte sync auf Auftrag entfernt hat, bleibt
        vorher = alt.get("ist") if isinstance(alt.get("ist"), dict) else {}
        for feld in ("tor_entfernt", "tor_entfernen_abgelehnt"):
            liste = vorher.get(feld)
            ist[feld] = liste if isinstance(liste, list) else []
    alt["tor"] = stand_tor
    alt["ist"] = ist
    print(json.dumps(alt, ensure_ascii=False))
    sys.exit(0)
print(json.dumps({
    "ok": art == "ok",
    "zeit": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "soll_sha256": summe,
    "meldung": meldung,
    "kunden": kunden.split(),
    "tor": stand_tor,
    "ist": ist,
}, ensure_ascii=False))
PY
  then
    rm -f "$tmp"
    return 0
  fi
  chmod 644 "$tmp"
  mv -f "$tmp" "$ziel"
}

# --- Abbau aus dem Cockpit und Ist-Stand (ADR 0059) ---------------------------------------------
front_ist() {  # setzt IST_GESPERRT (Kunden), IST_DATEIEN (Realms mit tor/<realm>.env), IST_DIENSTE (Realms
               # mit Container des Tors), IST_FREMD (Container ohne bekannten Realm), IST_VERWAIST (Realms
               # mit Datei, aber ohne Kunden – nie der Realm von Automato); mit Leerzeichen
  local f r k dienst
  local -A realm_von=() benutzt=()
  IST_GESPERRT="" IST_DATEIEN="" IST_DIENSTE="" IST_FREMD="" IST_VERWAIST=""
  for k in $(kunden); do
    lies_kunde "$k"
    [ "$SPERRE" != ja ] || IST_GESPERRT+="$k "
    if [ -n "$REALM" ]; then
      benutzt[$REALM]=1
      realm_von[$(tor_dienst "$REALM")]=$REALM
    fi
  done
  for f in "$FRONT_TOR_DIR"/*.env; do
    [ -f "$f" ] || continue
    r=$(basename "$f" .env)
    realm_ok "$r" || continue
    IST_DATEIEN+="$r "
    realm_von[$(tor_dienst "$r")]=$r
    if [ -z "${benutzt[$r]:-}" ] && [ "$r" != "${HAUPT_REALM:-}" ]; then IST_VERWAIST+="$r "; fi
  done
  # Container des Projekts automato-tor (laufend oder nicht); tor-intern ist das Tor des Portals
  while IFS= read -r dienst; do
    [[ "$dienst" =~ ^tor-[a-z0-9-]{1,60}$ ]] && [ "$dienst" != tor-intern ] || continue
    if [ -n "${realm_von[$dienst]:-}" ]; then IST_DIENSTE+="${realm_von[$dienst]} "; else IST_FREMD+="$dienst "; fi
  done < <($DOCKER ps -a --filter label=com.docker.compose.project=automato-tor \
    --format '{{.Label "com.docker.compose.service"}}' 2>/dev/null | sort -u || true)
  return 0
}

tor_abbau() {  # $1 entfernt|abgelehnt, $2 Realm, $3 Grund – für status.json (nur in sync)
  [ -z "${TOR_ABBAU:-}" ] || printf '%s\t%s%s\n' "$1" "$2" "${3:+$'\t'$3}" >>"$TOR_ABBAU"
}

tor_entfernen_uebernehmen() {  # $1 soll.json: Tor-Geheimnisse auf Auftrag des Cockpits entfernen
  local soll=$1 zeilen realm auftrag k marke
  local -A benutzt=()
  zeilen=$(python3 -I - "$soll" <<'PY'
import json, re, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
eintraege = data.get("tor_entfernen") if isinstance(data, dict) else None
for e in eintraege if isinstance(eintraege, list) else []:
    if not isinstance(e, dict):
        continue
    realm, auftrag = e.get("realm"), e.get("auftrag")
    if isinstance(realm, str) and isinstance(auftrag, str) and re.fullmatch(r"[0-9a-f]{32}", auftrag) \
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", realm):
        print(realm + "\t" + auftrag)
PY
  ) || { warn "soll.json: tor_entfernen nicht lesbar – nichts entfernt"; return 0; }
  [ -n "$zeilen" ] || return 0
  for k in $(kunden); do  # nach dem Übernehmen der Kunden: wer nutzt welchen Realm (auch von Hand)
    lies_kunde "$k"
    [ -z "$REALM" ] || benutzt[$REALM]=1
  done
  while IFS=$'\t' read -r realm auftrag; do
    realm_ok "$realm" || continue
    if [ "$realm" = "${HAUPT_REALM:-}" ]; then
      warn "Realm $realm ist der Realm von Automato – Tor-Geheimnis bleibt (Auftrag aus dem Cockpit abgelehnt)"
      tor_abbau abgelehnt "$realm" haupt_realm
      continue
    fi
    if [ -n "${benutzt[$realm]:-}" ]; then
      warn "Realm $realm wird noch von einem Kunden genutzt – Tor-Geheimnis bleibt (Auftrag aus dem Cockpit abgelehnt)"
      tor_abbau abgelehnt "$realm" in_verwendung
      continue
    fi
    # je Auftrag nur einmal: ein danach von Hand bzw. für einen neuen Kunden hinterlegtes bleibt
    marke="$FRONT_TOR_DIR/$realm.entfernt"
    if [ -f "$marke" ] && [ "$(cat "$marke")" = "$auftrag" ]; then continue; fi
    if [ -e "$(tor_env "$realm")" ] || [ -e "$FRONT_TOR_DIR/$realm.siegel" ]; then
      rm -f "$(tor_env "$realm")" "$FRONT_TOR_DIR/$realm.siegel"
      ok "Realm $realm: Tor-Geheimnis entfernt (Abbau aus dem Cockpit, kein Kunde nutzt den Realm)"
      tor_abbau entfernt "$realm"
    fi
    install -d -m 700 "$FRONT_TOR_DIR"
    (umask 077 && printf '%s\n' "$auftrag" >"$marke")
  done <<<"$zeilen"
  return 0
}

cmd_sync() {
  root_noetig
  local soll="$FRONT_STATE/soll.json" summe alt fehlertext rc=0
  if [ ! -f "$soll" ]; then
    info "kein Soll-Stand aus der Oberfläche ($soll)"
    return 0
  fi
  summe=$(sha256sum "$soll" | cut -d' ' -f1)
  alt=$(python3 -I -c 'import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
print(d.get("soll_sha256", "") if d.get("ok") else "")' "$FRONT_STATE/status.json" 2>/dev/null || true)
  if [ "$summe" = "$alt" ] && [ "${1:-}" != "--immer" ]; then
    info "Soll-Stand unverändert – nichts zu tun"
    return 0
  fi
  fehlertext=$(mktemp)
  TOR_VERWORFEN=$(mktemp)  # Geheimnisse aus dem Cockpit, die sync verwirft (Realm und Grund, 1.13.0)
  TOR_ABBAU=$(mktemp)  # Tor-Geheimnisse, die sync auf Auftrag entfernt bzw. ablehnt (ADR 0059)
  if (sync_anwenden "$soll") 2>"$fehlertext"; then rc=0; else rc=$?; fi
  cat "$fehlertext" >&2
  if [ "$rc" -eq 0 ]; then
    status_schreiben ok "$summe" "übernommen"
    rm -f "$fehlertext" "$TOR_VERWORFEN" "$TOR_ABBAU"
    ok "Kunden aus der Oberfläche übernommen"
  else
    status_schreiben fehler "$summe" "$(grep -m1 'FEHLER' "$fehlertext" | sed -E 's/^ *FEHLER:? *//' || true)"
    rm -f "$fehlertext" "$TOR_VERWORFEN" "$TOR_ABBAU"
    abbruch "Kunden aus der Oberfläche nicht übernommen (Meldung in status.json)"
  fi
}

cmd_agent() {
  root_noetig
  local skript="$SCRIPT_DIR/front.sh"
  install -d -m 750 "$FRONT_STATE"
  chown "$ORCH_UID:$ORCH_UID" "$FRONT_STATE" 2>/dev/null || warn "Besitzer von $FRONT_STATE nicht gesetzt (uid $ORCH_UID)"
  install -d -m 755 "$FRONT_SYSTEMD_DIR"
  printf '%s\n' \
    '[Unit]' \
    'Description=Automato Front-Proxy: Kunden aus der Oberfläche übernehmen (ADR 0050)' \
    'After=docker.service' \
    '' \
    '[Service]' \
    'Type=oneshot' \
    "ExecStart=$skript sync" >"$FRONT_SYSTEMD_DIR/automato-front-sync.service"
  printf '%s\n' \
    '[Unit]' \
    'Description=Automato Front-Proxy: Soll-Stand der Oberfläche beobachten' \
    '' \
    '[Path]' \
    "PathChanged=$FRONT_STATE/soll.json" \
    'Unit=automato-front-sync.service' \
    '' \
    '[Install]' \
    'WantedBy=multi-user.target' >"$FRONT_SYSTEMD_DIR/automato-front-sync.path"
  printf '%s\n' \
    '[Unit]' \
    'Description=Automato Front-Proxy: Soll-Stand regelmässig prüfen (falls ein Ereignis fehlte)' \
    '' \
    '[Timer]' \
    'OnBootSec=2min' \
    'OnUnitActiveSec=10min' \
    'Unit=automato-front-sync.service' \
    '' \
    '[Install]' \
    'WantedBy=timers.target' >"$FRONT_SYSTEMD_DIR/automato-front-sync.timer"
  $SYSTEMCTL daemon-reload
  $SYSTEMCTL enable --now automato-front-sync.path automato-front-sync.timer
  ok "Host-Agent eingerichtet: $FRONT_STATE/soll.json → front.sh sync (systemd)"
  info "Stand: systemctl status automato-front-sync.path; Log: journalctl -u automato-front-sync"
}

# --- Anmelde-Tor: Client-Secret je Realm und Stand ----------------------------------------------
tor_adressen() {  # Valid redirect URIs des Clients automato-front in einem Realm
  local realm=$1 k
  for k in $(kunden); do
    lies_kunde "$k"
    [ "$REALM" = "$realm" ] || continue
    printf '            https://%s.%s/oauth2/callback\n' "$k" "$DOMAIN"
    [ -z "$NETBOX" ] || [ "$NB" != ja ] || printf '            https://%s-nb.%s/oauth2/callback\n' "$k" "$DOMAIN"
    [ "$DOC" != ja ] || printf '            https://%s-doc.%s/oauth2/callback\n' "$k" "$DOMAIN"
    [ "$UM" != ja ] || [ -z "$UM_ZIEL" ] || printf '            https://%s-um.%s/oauth2/callback\n' "$k" "$DOMAIN"
    [ "$CMDB_AN" != ja ] || [ -z "$CMDB" ] || printf '            https://%s-cmdb.%s/oauth2/callback\n' "$k" "$DOMAIN"
  done
  if [ "$realm" = "$HAUPT_REALM" ] && [ -n "$INTERN" ]; then
    printf '            https://start.%s/oauth2/callback\n' "$INTERN"
  fi
}

tor_logout_adressen() {  # Valid post logout redirect URIs des Clients automato-front (1.12.1)
  local realm=$1 k
  for k in $(kunden); do
    lies_kunde "$k"
    [ "$REALM" != "$realm" ] || printf '            https://%s.%s/*\n' "$k" "$DOMAIN"
  done
  if [ "$realm" = "$HAUPT_REALM" ] && [ -n "$INTERN" ]; then
    printf '            https://start.%s/*\n' "$INTERN"
  fi
}

tor_stand() {
  local r k
  titel "Anmelde-Tor ($TOR)"
  if ! tor_an; then
    info "aus – einschalten: AUTOMATO_FRONT_TOR=an in .env, dann front.sh erzeugen"
  fi
  [ -n "$KC" ] && ok "Keycloak: $KC (Browser), $KC_INTERN (Proxy)" || warn "Keycloak unbekannt: AUTOMATO_OIDC_ISSUER bzw. AUTOMATO_FRONT_KEYCLOAK"
  tor_uebersicht
  for r in $TOR_BEREIT; do
    ok "Realm $r: Client-Secret hinterlegt ($(tor_herkunft "$r"))"
  done
  for r in $TOR_FEHLT; do
    warn "Realm $r: Client-Secret fehlt – sudo deploy/front/front.sh tor $r"
  done
  for r in $TOR_BEREIT $TOR_FEHLT; do
    info "Keycloak → Realm $r → Client $TOR_CLIENT (Client authentication an, Standard flow, PKCE S256),"
    info "  Valid redirect URIs:"
    tor_adressen "$r"
    info "  Valid post logout redirect URIs:"
    tor_logout_adressen "$r"
  done
  for k in $TOR_GESPERRT; do
    lies_kunde "$k"
    if [ -z "$REALM" ]; then warn "Kunde $k ohne Realm – gesperrt (Kunden-Portal: Realm eintragen)"
    else warn "Kunde $k gesperrt – Client-Secret für Realm $REALM fehlt"; fi
  done
  tor_an || return 0
  local kennung
  kennung=$(empfang_kennung)
  if [ -n "$kennung" ]; then
    info "Empfangsschlüssel für Client-Secrets aus dem Cockpit: ${kennung:0:16}… (front.sh tor-empfang)"
  fi
}

cmd_tor() {
  { set +x; } 2>/dev/null  # das Client-Secret nie in einer Ablaufverfolgung (bash -x)
  root_noetig
  lade_conf
  local realm=${1:-} geheim ziel
  if [ -z "$realm" ]; then
    tor_stand
    return 0
  fi
  realm_pruefen "$realm"
  titel "Anmelde-Tor: Client-Secret für Realm $realm"
  info "Keycloak → Realm $realm → Clients → $TOR_CLIENT → Credentials → Client secret"
  if [ -t 0 ]; then
    read -rsp "          Client secret (Eingabe unsichtbar): " geheim
    echo
  else
    IFS= read -r geheim || true
  fi
  [[ "$geheim" =~ ^[A-Za-z0-9._~+/=-]{8,512}$ ]] || abbruch "Client secret leer oder ungültig – nichts geändert"
  install -d -m 700 "$FRONT_TOR_DIR"
  ziel=$(tor_env "$realm")
  (
    umask 077
    {
      printf '# Anmelde-Tor, Realm %s (front.sh tor) – nur root\n' "$realm"
      printf 'OAUTH2_PROXY_CLIENT_SECRET=%s\n' "$geheim"
      printf 'OAUTH2_PROXY_COOKIE_SECRET=%s\n' "$(openssl rand -hex 16)"
    } >"$ziel.neu"
  )
  mv -f "$ziel.neu" "$ziel"
  ok "Client secret für $realm hinterlegt ($ziel, nur root; Sitzungen beginnen neu)"
  if aus_cockpit_realm "$realm"; then
    info "Realm $realm gehört zu einem Kunden aus dem Cockpit: gilt, bis das Cockpit ein neues Client-Secret schickt"
  fi
  tor_an || info "Anmelde-Tor ist aus – einschalten: AUTOMATO_FRONT_TOR=an in .env"
  TOR_NEU=$realm
  cmd_erzeugen
}

# --- Client-Secrets aus dem Cockpit (1.13.0, ADR 0055) -------------------------------------------
# Das Cockpit liest das Client-Secret von automato-front in Keycloak und versiegelt es mit dem
# öffentlichen Empfangsschlüssel dieser VM (Inventar: front.tor_empfang). Automato hält nur das
# Chiffrat (soll.json, tor_geheimnisse); entschlüsselt wird nur hier, als root, und der Klartext
# geht nur durch Pipes (openssl → Python) – nie in eine Variable, ein Argument, die Ausgabe oder
# status.json.
empfang_key() { printf '%s/empfang.key' "$FRONT_TOR_DIR"; }
empfang_pub() { printf '%s/empfang.pub' "$FRONT_TOR_DIR"; }

empfang_kennung() {  # SHA-256 (hex, klein) über den öffentlichen Schlüssel im DER-Format; leer = keiner
  local pub
  pub=$(empfang_pub)
  if [ ! -s "$pub" ] || ! openssl pkey -pubin -in "$pub" -noout 2>/dev/null; then return 0; fi
  openssl pkey -pubin -in "$pub" -outform DER 2>/dev/null | sha256sum | cut -c1-64
}

empfang_anlegen() {  # Empfangsschlüssel anlegen, wenn keiner da ist (RSA 3072, privat nur root, 600)
  local key pub
  key=$(empfang_key)
  pub=$(empfang_pub)
  install -d -m 700 "$FRONT_TOR_DIR"
  if [ ! -s "$key" ]; then
    rm -f "$pub" "$key.neu"
    if ! (umask 077 && openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out "$key.neu" 2>/dev/null); then
      rm -f "$key.neu"
      warn "Empfangsschlüssel nicht angelegt (openssl) – Client-Secrets aus dem Cockpit erst danach"
      return 0
    fi
    chmod 600 "$key.neu"
    mv -f "$key.neu" "$key"
    ok "Empfangsschlüssel für Client-Secrets aus dem Cockpit angelegt (ADR 0055)"
  fi
  if [ ! -s "$pub" ]; then
    if openssl pkey -in "$key" -pubout -out "$pub.neu" 2>/dev/null; then
      chmod 644 "$pub.neu"
      mv -f "$pub.neu" "$pub"
    else
      rm -f "$pub.neu"
      warn "Empfangsschlüssel $key nicht lesbar – neu: front.sh tor-empfang neu"
    fi
  fi
}

tor_vom_cockpit() {  # Client-Secret des Realms zuletzt aus dem Cockpit übernommen (Kopfzeile der Datei)?
  local erste=""
  tor_bereit "$1" || return 1
  IFS= read -r erste <"$(tor_env "$1")" || true
  [[ "$erste" == *"(aus dem Cockpit)"* ]]
}

tor_herkunft() {  # für tor, tor-empfang und pruefen
  if tor_vom_cockpit "$1"; then printf 'aus dem Cockpit'; else printf 'von Hand'; fi
}

tor_verwerfen() {  # $1 Realm, $2 Grund – Warnung und status.json, nie mit Inhalt
  warn "Realm ${1:-?}: Client-Secret aus dem Cockpit verworfen ($2)"
  [ -z "${TOR_VERWORFEN:-}" ] || printf '%s\t%s\n' "$1" "$2" >>"$TOR_VERWORFEN"
}

tor_geheimnisse_uebernehmen() {  # $1 soll.json; setzt TOR_NEU (Realms mit neuem Client-Secret)
  { set +x; } 2>/dev/null  # keine Ablaufverfolgung in der Nähe des Geheimnisses
  local soll=$1 zeilen realm kennung versiegelt eigene k summe siegel ergebnis
  local -A cockpit=()
  TOR_NEU=""
  # nur lesen und in Zeilen wandeln (Trenner 0x1f); geprüft wird danach
  zeilen=$(python3 -I - "$soll" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
eintraege = data.get("tor_geheimnisse") if isinstance(data, dict) else None
for e in eintraege if isinstance(eintraege, list) else []:
    werte = [e.get(f) if isinstance(e, dict) else None for f in ("realm", "kennung", "versiegelt")]
    print("\x1f".join(w if isinstance(w, str) and w.isprintable() and "\x1f" not in w else "" for w in werte))
PY
  ) || { tor_verwerfen "" format; return 0; }
  [ -n "$zeilen" ] || return 0
  for k in $(kunden); do  # nur Realms von Kunden aus dem Cockpit
    lies_kunde "$k"
    if [ "$QUELLE" = cockpit ] && [ -n "$REALM" ]; then cockpit[$REALM]=1; fi
  done
  eigene=$(empfang_kennung)
  while IFS=$'\x1f' read -r realm kennung versiegelt; do
    if ! realm_ok "$realm"; then tor_verwerfen "" format; continue; fi
    if ! [[ "$kennung" =~ ^[0-9a-f]{64}$ && "$versiegelt" =~ ^[A-Za-z0-9+/]+={0,2}$ ]] \
      || [ "${#versiegelt}" -gt 1024 ] || [ $(( ${#versiegelt} % 4 )) -ne 0 ]; then
      tor_verwerfen "$realm" format
      continue
    fi
    if [ -z "${cockpit[$realm]:-}" ]; then tor_verwerfen "$realm" realm_unbekannt; continue; fi
    # schon verarbeitet (übernommen bzw. danach von Hand ersetzt): gilt, bis ein anderes Chiffrat kommt
    summe=$(printf '%s' "$versiegelt" | sha256sum | cut -c1-64)
    siegel="$FRONT_TOR_DIR/$realm.siegel"
    if [ -f "$siegel" ] && [ "$(cat "$siegel")" = "$summe" ]; then continue; fi
    if [ -z "$eigene" ]; then tor_verwerfen "$realm" kein_empfang; continue; fi
    if [ "$kennung" != "$eigene" ]; then tor_verwerfen "$realm" schluessel_veraltet; continue; fi
    ergebnis=$(printf '%s' "$versiegelt" | base64 -d 2>/dev/null \
      | openssl pkeyutl -decrypt -inkey "$(empfang_key)" -pkeyopt rsa_padding_mode:oaep \
          -pkeyopt rsa_oaep_md:sha256 -pkeyopt rsa_mgf1_md:sha256 2>/dev/null \
      | python3 -I -c "$TOR_SIEGEL_PY" "$realm" "$TOR_CLIENT" "$(tor_env "$realm")" 2>/dev/null) || true
    case "$ergebnis" in
      neu | gleich)
        (umask 077 && printf '%s\n' "$summe" >"$siegel")
        if [ "$ergebnis" = neu ]; then
          TOR_NEU+="$realm "
          ok "Realm $realm: Client-Secret aus dem Cockpit übernommen (Container des Tors neu)"
        else
          ok "Realm $realm: Client-Secret aus dem Cockpit wie bisher"
        fi
        ;;
      nicht_entschluesselt | inhalt_ungueltig) tor_verwerfen "$realm" "$ergebnis" ;;
      *) tor_verwerfen "$realm" nicht_geschrieben ;;
    esac
  done <<<"$zeilen"
  return 0
}

# Python für tor_geheimnisse_uebernehmen: Klartext des Siegels auf stdin, Realm, Client und Datei
# als Argumente. Gibt nur ein Wort aus (neu, gleich, nicht_entschluesselt, inhalt_ungueltig, fehler).
TOR_SIEGEL_PY=$(cat <<'PY'
import hmac, json, os, re, secrets, sys
def ende(wort):
    print(wort)
    sys.exit(0)
try:
    realm, client, ziel = sys.argv[1:4]
    roh = sys.stdin.buffer.read(4096)
    if not roh:
        ende("nicht_entschluesselt")
    try:
        inhalt = json.loads(roh.decode("utf-8"))
    except ValueError:
        inhalt = None
    geheim = inhalt.get("secret") if isinstance(inhalt, dict) else None
    if not (isinstance(inhalt, dict) and inhalt.get("realm") == realm and inhalt.get("client") == client
            and isinstance(geheim, str) and re.fullmatch(r"[A-Za-z0-9._~+/=-]{8,512}", geheim)):
        ende("inhalt_ungueltig")
    alt, zeilen = {}, []
    try:
        with open(ziel, encoding="utf-8") as fh:
            zeilen = fh.read().splitlines()
    except OSError:
        pass
    for zeile in zeilen:
        schluessel, _, wert = zeile.partition("=")
        alt.setdefault(schluessel, wert)
    gleich = hmac.compare_digest(alt.get("OAUTH2_PROXY_CLIENT_SECRET", "").encode(), geheim.encode())
    kopf = f"# Anmelde-Tor, Realm {realm} (aus dem Cockpit) – nur root"
    if gleich and zeilen and zeilen[0] == kopf:
        ende("gleich")
    # Cookie-Secret bleibt (Sitzungen bleiben gültig), sonst neu wie front.sh tor
    cookie = alt.get("OAUTH2_PROXY_COOKIE_SECRET", "")
    if not re.fullmatch(r"[A-Za-z0-9._~+/=-]{16,64}", cookie):
        cookie = secrets.token_hex(16)
    neu = ziel + ".neu"
    fd = os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(f"{kopf}\nOAUTH2_PROXY_CLIENT_SECRET={geheim}\nOAUTH2_PROXY_COOKIE_SECRET={cookie}\n")
    os.chmod(neu, 0o600)
    os.replace(neu, ziel)
    ende("gleich" if gleich else "neu")
except SystemExit:
    raise
except BaseException:  # nie eine Ablaufverfolgung (sie könnte Werte zeigen)
    ende("fehler")
PY
)

cmd_tor_empfang() {
  root_noetig
  lade_conf
  local kennung r
  case "${1:-}" in
    "") ;;
    neu)
      tor_an || abbruch "Anmelde-Tor ist aus (AUTOMATO_FRONT_TOR=an) – kein Empfangsschlüssel nötig"
      rm -f "$(empfang_key)" "$(empfang_pub)"
      empfang_anlegen
      schreibe_topologie
      info "Das Cockpit versiegelt die Client-Secrets beim nächsten Abgleich neu; die hinterlegten gelten weiter"
      ;;
    *) abbruch "Verwendung: front.sh tor-empfang [neu]" ;;
  esac
  titel "Empfangsschlüssel für Client-Secrets aus dem Cockpit (ADR 0055)"
  kennung=$(empfang_kennung)
  if [ -n "$kennung" ]; then
    ok "Kennung $kennung"
    info "RSA 3072, $TOR_EMPFANG_ALG, öffentlich: $(empfang_pub) – im Inventar fürs Cockpit"
  elif tor_an; then
    warn "noch keiner – legt front.sh erzeugen bzw. sync an (oder: front.sh tor-empfang neu)"
  else
    info "keiner – Anmelde-Tor aus"
  fi
  tor_an || return 0
  tor_uebersicht
  for r in $TOR_BEREIT; do info "Realm $r: Client-Secret $(tor_herkunft "$r")"; done
  for r in $TOR_FEHLT; do info "Realm $r: Client-Secret fehlt"; done
  return 0
}

case "${1:-}" in
  kunde | link | entfernen)  # 1.14.0 (ADR 0058): --trotzdem an beliebiger Stelle
    befehl=$1 rest=()
    shift
    for a in "$@"; do if [ "$a" = --trotzdem ]; then TROTZDEM=ja; else rest+=("$a"); fi; done
    set -- "$befehl" ${rest[@]+"${rest[@]}"}
    ;;
esac
case "${1:-}" in
  kunde) shift; cmd_kunde "$@" ;;
  link) shift; cmd_link "$@" ;;
  entfernen) shift; cmd_entfernen "$@" ;;
  liste) cmd_liste ;;
  erzeugen) root_noetig; cmd_erzeugen ;;
  pruefen) cmd_pruefen ;;
  adressen) cmd_adressen ;;
  sync) shift; cmd_sync "$@" ;;
  agent) cmd_agent ;;
  tor) shift; cmd_tor "$@" ;;
  tor-empfang) shift; cmd_tor_empfang "$@" ;;
  *) sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
