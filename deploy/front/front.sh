#!/usr/bin/env bash
# shellcheck disable=SC1111 # deutsche Anführungszeichen „…“ in Meldungen sind gewollt
# Front-Proxy der Automato-VM (1.2.0, ADR 0044 Nachtrag): Startseiten je Kunde, internes Portal und
# Weiterleitung je Werkzeug – im Proxy von Automato (Profil "proxy", deploy/proxy/Caddyfile).
#
# Adressen (<domain> = AUTOMATO_FRONT_DOMAIN, <intern> = AUTOMATO_FRONT_DOMAIN_INT):
#   <kunde>.<domain>        Startseite des Kunden (Kacheln zu seinen Werkzeugen)
#   <kunde>-nb.<domain>     NetBox des Kunden (NetBox-Host, AUTOMATO_FRONT_NETBOX)
#   <kunde>-doc.<domain>    Automato (diese VM) – nur mit --doc
#   <kunde>-um.<domain>     Benutzerverwaltung (Magister) – nur mit --um und AUTOMATO_FRONT_MAGISTER
#   <kunde>-cmdb.<domain>   CMDB – nur mit --cmdb und AUTOMATO_FRONT_CMDB
#   start.<intern>          Portal mit allen Kunden (nur Management-Netz)
#   <intern>                Verwaltung (Cockpit, Version 2) – bis dahin Weiterleitung aufs Portal
# Kunden-Adressen nur von der WAF und aus dem Management-Netz (AUTOMATO_FRONT_KUNDEN_ALLOW), der
# Angabe X-Forwarded-For wird nur von der WAF geglaubt; interne Adressen nur aus dem Management-Netz
# und nie über die WAF.
#
# Verwendung (im Repo-Ordner, mit sudo):
#   front.sh kunde <kunde> "<Anzeigename>" [--doc] [--um] [--cmdb] [--realm <r>]
#                  [--zugriff ueberall|intern|"<netz> …"]           anlegen/ändern
#   front.sh link <kunde> <titel> <url> [beschreibung]               weitere Kachel auf der Startseite
#   front.sh entfernen <kunde> --ja                                 Kunde aus dem Front-Proxy nehmen
#   front.sh liste                                                  Kunden mit ihren Werkzeugen
#   front.sh erzeugen                                               alles neu erzeugen, Proxy neu laden
#   front.sh pruefen                                                Einstellungen, Zertifikate, Wege
#   front.sh sync [--immer]                                         Kunden aus der Oberfläche übernehmen
#   front.sh agent                                                  Host-Agent einrichten (systemd)
#   front.sh tor [<realm>]                                          Anmelde-Tor: Stand bzw. Client-Secret
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
# Einstellungen: AUTOMATO_FRONT_* in .env (siehe .env.example). Kunden: deploy/front/kunden/,
# erzeugt: deploy/front/generated/, Client-Secrets: deploy/front/tor/ (alles nicht im Repo).
# Zertifikate: deploy/proxy/pki/mgmt.crt|key (Kunden-Adressen) und mgmt-int.crt|key (intern).
# Kurzname: Kleinbuchstaben, Ziffern und Bindestrich (DNS-Label), nicht auf -nb/-doc/-um/-cmdb endend.
# Für Tests ersetzbar: DOCKER, CURL, IPTABLES, SYSTEMCTL, FRONT_DIR, FRONT_PKI, FRONT_STATE,
# FRONT_TOR_DIR, FRONT_SYSTEMD_DIR, AUTOMATO_ENV_FILE, FRONT_SKIP_ROOT=1.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
FRONT_DIR=${FRONT_DIR:-$SCRIPT_DIR}
FRONT_PKI=${FRONT_PKI:-$REPO_DIR/deploy/proxy/pki}
DOCKER=${DOCKER:-docker}
IPTABLES=${IPTABLES:-iptables}
CURL=${CURL:-curl}
SYSTEMCTL=${SYSTEMCTL:-systemctl}
FRONT_STATE=${FRONT_STATE:-$FRONT_DIR/state}  # Soll-Stand der Oberfläche (1.3.0)
FRONT_TOR_DIR=${FRONT_TOR_DIR:-$FRONT_DIR/tor}  # Client-Secrets des Anmelde-Tors (1.4.0, nur root)
FRONT_SYSTEMD_DIR=${FRONT_SYSTEMD_DIR:-/etc/systemd/system}
ORCH_UID=1001  # Benutzer im Orchestrator-Container (orchestrator/Dockerfile)
TOR_CLIENT=automato-front  # Client des Anmelde-Tors in jedem Realm (deploy/keycloak/make_realm.py)
TOR_IMAGE_STANDARD=quay.io/oauth2-proxy/oauth2-proxy:v7.15.6
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
  local u
  for u in "$MAGISTER" "$CMDB"; do
    [ -z "$u" ] || [[ "$u" =~ ^https?://[A-Za-z0-9.:/_-]+$ ]] || abbruch "Adresse ungültig: '$u' (http(s)://name[:port][/pfad])"
  done
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
  case "$k" in start | www | proxy | alle | konfig | mgmt) abbruch "Kurzname '$k' ist reserviert" ;; esac
  case "$k" in *-nb | *-doc | *-um | *-cmdb) abbruch "Kurzname '$k' ist reserviert (endet wie ein Werkzeug: -nb, -doc, -um, -cmdb)" ;; esac
}

kdir() { printf '%s/kunden' "$FRONT_DIR"; }
aus_oberflaeche() {  # Kunde wird in der Oberfläche gepflegt (QUELLE=oberflaeche)?
  [ -f "$(kdir)/$1.conf" ] && grep -qx 'QUELLE=oberflaeche' "$(kdir)/$1.conf"
}
nur_befehl() {
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

lies_kunde() {  # setzt NAME, DOC, UM, CMDB_AN, QUELLE, REALM, ZUGRIFF, NETZE aus kunden/<k>.conf
  local k=$1
  NAME=$k DOC=nein UM=nein CMDB_AN=nein QUELLE=befehl REALM="" ZUGRIFF=ueberall NETZE=""
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
seite() {  # HTML-Gerüst: $1 Titel, $2 Untertitel, $3 "abmelden" (Anmelde-Tor), Kacheln auf stdin
  local t=$1 sub=$2 fuss="Anmeldung je Werkzeug · sign-in per tool" kt ku kb
  # mit Anmelde-Tor: die Anmeldung gilt für alle Werkzeuge, Abmelden beendet auch die Keycloak-Sitzung
  [ "${3:-}" != abmelden ] || fuss='<a href="/oauth2/sign_out">Abmelden · sign out</a>'
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
  printf 'NetBox\thttps://%s-nb.%s\tIPAM, Geräte, Verkabelung\n' "$k" "$DOMAIN"
  [ "$DOC" != ja ] || printf 'Dokumentation\thttps://%s-doc.%s\tFirewall-Regeln, Applikationen, Wege\n' "$k" "$DOMAIN"
  [ "$UM" != ja ] || [ -z "$MAGISTER" ] || printf 'Benutzerverwaltung\thttps://%s-um.%s\tBenutzer und Rechte\n' "$k" "$DOMAIN"
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

route_kunde() {  # route einer Kunden-Adresse: $1 Ziel, $2 vor dem Tor (immer), $3 vor dem Tor (nur mit Tor)
  printf '\troute {\n\t\timport front-kunden-sperre\n'
  zugriff_sperre
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
  route_kunde $'\t\t'"root * /srv/front/$k"$'\n\t\timport front-seite'
  printf '}\n'
  if [ -n "$NETBOX" ]; then
    nb_ziel() {  # NetBox auf dem NetBox-Host; $1 = Zeile(n) für den Benutzer
      printf '\t\treverse_proxy https://%s {\n\t\t\theader_up Host {host}%s\n\t\t\ttransport http {\n\t\t\t\ttls_server_name {host}%s\n\t\t\t}\n\t\t}' \
        "$NETBOX" "${1:-}" "$trust"
    }
    printf '\n%s-nb.%s {\n\timport front-kunden\n' "$k" "$DOMAIN"
    zugriff_matcher
    # API mit Token (NetBox-Sync von Automato, Skripte): NetBox prüft den Token selbst – ohne Tor
    printf '\t@front_nb_token {\n\t\tpath /api/* /graphql/*\n\t\theader_regexp Authorization ^(Token|Bearer)\\s\n\t}\n'
    route_kunde "$(nb_ziel "$ident")" \
      $'\t\thandle @front_nb_token {\n'"$(nb_ziel "" | sed 's/^/\t/')"$'\n\t\t}' \
      $'\t\tredir /logout/ /oauth2/sign_out 302'
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
  if [ "$UM" = ja ] && [ -n "$MAGISTER" ]; then
    printf '\n%s-um.%s {\n\timport front-kunden\n' "$k" "$DOMAIN"
    zugriff_matcher
    route_kunde $'\t\t'"reverse_proxy $MAGISTER {$ident"$'\n\t\t}'
    printf '}\n'
  fi
  if [ "$CMDB_AN" = ja ] && [ -n "$CMDB" ]; then
    printf '\n%s-cmdb.%s {\n\timport front-kunden\n' "$k" "$DOMAIN"
    zugriff_matcher
    route_kunde $'\t\t'"reverse_proxy $CMDB {$ident"$'\n\t\t}'
    printf '}\n'
  fi
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

cmd_erzeugen() {
  lade_conf
  local g k www name abmelden
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
    [ -z "$TOR_DIENST" ] || abmelden=abmelden
    kacheln "$k" | seite "$NAME" "Werkzeuge · tools" "$abmelden" >"$www/$k/index.html"
  done
  # Unbekannte Namen unter den Domains (z. B. <kunde>-doc ohne --doc): 404 statt einer leeren
  # Antwort; die bekannten Namen sind genauer und gehen vor
  {
    printf '# Unbekannte Adressen (front.sh) – nicht von Hand ändern\n'
    printf '*.%s {\n\timport front-kunden\n\troute {\n\t\timport front-kunden-sperre\n\t\trespond "Unbekannte Adresse" 404\n\t}\n}\n' "$DOMAIN"
    [ -z "$INTERN" ] || printf '\n*.%s {\n\timport front-intern\n\troute {\n\t\timport front-intern-sperre\n\t\trespond "Unbekannte Adresse" 404\n\t}\n}\n' "$INTERN"
  } >"$g/sites/_unbekannt.caddy"
  if [ -n "$INTERN" ]; then
    # Portal: mit Anmelde-Tor nur für die Rollen AUTOMATO_FRONT_INTERN_ROLLEN im Realm von Automato
    local portal=$'\t\troot * /srv/front/_portal\n\t\timport front-seite'
    abmelden=""
    if tor_an; then
      if [ -n "$HAUPT_REALM" ] && tor_bereit "$HAUPT_REALM"; then
        portal=$'\t\timport front-tor tor-intern\n'"$portal"
        abmelden=abmelden
      else
        portal=$'\t\timport front-gesperrt'
      fi
    fi
    install -d -m 755 "$www/_portal"
    {
      for k in $(kunden); do
        lies_kunde "$k"
        name=$NAME
        printf '%s\thttps://%s.%s\t%s\n' "$name" "$k" "$DOMAIN" "$(werkzeuge "$k")"
      done
    } | seite "Kunden" "Portal · alle Kunden" "$abmelden" >"$www/_portal/index.html"
    cat >"$g/sites/_intern.caddy" <<EOF
# Interne Adressen (front.sh) – nicht von Hand ändern
start.$INTERN {
	import front-intern
	route {
		import front-intern-sperre
$portal
	}
}

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
  local meldung
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
  [ -z "$NETBOX" ] || w="NetBox"
  [ "$DOC" != ja ] || w="${w:+$w, }Doku"
  if [ "$UM" = ja ]; then
    if [ -n "$MAGISTER" ]; then w="${w:+$w, }Benutzer"; else w="${w:+$w, }Benutzer (aus)"; fi
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
  local k=${1:-} name=${2:-} doc=nein um=nein cmdb=nein realm="" zugriff=ueberall netz=""
  kurzname "$k"
  nur_befehl "$k"
  [ -n "$name" ] && [[ "$name" != --* ]] || abbruch "Verwendung: kunde <kunde> \"<Anzeigename>\" [--doc] [--um] [--cmdb] [--realm <realm>] [--zugriff ueberall|intern|\"<netz> …\"]"
  shift 2
  while [ $# -gt 0 ]; do
    case "$1" in
      --doc) doc=ja ;;
      --um) um=ja ;;
      --cmdb) cmdb=ja ;;
      --realm) realm=${2:-}; realm_pruefen "$realm"; shift ;;
      --zugriff)
        case "${2:-}" in
          ueberall | intern) zugriff=$2 ;;
          *) zugriff=netze netz=$(printf '%s' "${2:-}" | tr ',' ' ' | xargs) ;;
        esac
        zugriff_pruefen "$zugriff" "$netz"
        shift
        ;;
      *) abbruch "unbekannte Angabe '$1' (--doc, --um, --cmdb, --realm <realm>, --zugriff …)" ;;
    esac
    shift
  done
  install -d -m 750 "$(kdir)"
  printf 'NAME=%q\nDOC=%q\nUM=%q\nCMDB_AN=%q\nREALM=%q\nZUGRIFF=%q\nNETZE=%q\n' \
    "$name" "$doc" "$um" "$cmdb" "$realm" "$zugriff" "$netz" >"$(kdir)/$k.conf"
  titel "Kunde $k im Front-Proxy"
  ZUGRIFF=$zugriff NETZE=$netz
  ok "Zugriff: $(zugriff_text)"
  ok "Startseite https://$k.$DOMAIN"
  [ -n "$NETBOX" ] && ok "NetBox https://$k-nb.$DOMAIN → $NETBOX" || warn "AUTOMATO_FRONT_NETBOX fehlt – keine Weiterleitung zur NetBox"
  if [ "$doc" = ja ]; then
    ok "Dokumentation https://$k-doc.$DOMAIN"
    info "Keycloak (Client automato-web): Valid redirect URI https://$k-doc.$DOMAIN/* und Web origin https://$k-doc.$DOMAIN ergänzen"
  fi
  [ "$um" != ja ] || { [ -n "$MAGISTER" ] && ok "Benutzerverwaltung https://$k-um.$DOMAIN → $MAGISTER" || warn "--um gesetzt, aber AUTOMATO_FRONT_MAGISTER leer – bleibt aus"; }
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
  local quelle
  printf '%-20s %-28s %-16s %-14s %-8s %s\n' KUNDE WERKZEUGE REALM ZUGRIFF QUELLE STARTSEITE
  for k in $(kunden); do
    lies_kunde "$k"
    quelle=Hand
    [ "$QUELLE" != oberflaeche ] || quelle=Portal
    printf '%-20s %-28s %-16s %-14s %-8s https://%s.%s\n' "$k" "$(werkzeuge "$k")" "${REALM:-–}" \
      "$(zugriff_text | cut -c1-14)" "$quelle" "$k" "$DOMAIN"
  done
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

cmd_pruefen() {
  root_noetig
  lade_conf
  titel "Einstellungen"
  ok "Kunden-Adressen *.$DOMAIN nur aus: $KUNDEN_ALLOW"
  [ -n "$WAF" ] && ok "WAF: $WAF (X-Forwarded-For wird übernommen)" || warn "AUTOMATO_FRONT_WAF leer – Adresse der Benutzer unbekannt"
  [ -n "$INTERN" ] && ok "Intern start.$INTERN und $INTERN nur aus: $MGMT${WAF:+ (nie über die WAF)}" || warn "AUTOMATO_FRONT_DOMAIN_INT leer – kein Portal"
  [ -n "$NETBOX" ] && ok "NetBox-Host: $NETBOX" || warn "AUTOMATO_FRONT_NETBOX leer – keine Weiterleitung zur NetBox"
  [ -n "$MAGISTER" ] && ok "Magister: $MAGISTER" || info "Magister (-um) aus"
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
  [ -z "$INTERN" ] || pruefe_zertifikat mgmt-int "$INTERN" "*.$INTERN"

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

  titel "Anmelde-Tor"
  if tor_an; then
    ok "an – Keycloak $KC (Browser), $KC_INTERN (Proxy)"
    tor_uebersicht
    local r laufend soll
    for r in $TOR_BEREIT; do ok "Realm $r: Client-Secret hinterlegt"; done
    for r in $TOR_FEHLT; do fehler "Realm $r: Client-Secret fehlt – front.sh tor $r"; done
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
    [ -n "$NETBOX" ] || continue
    # Orchestrator (Prüfen, NetBox-Sync) und zentraler Worker (Einlesen, Abgleich) müssen die
    # NetBox gleich erreichen – extra_hosts in docker-compose.override.yml für beide (1.5.0)
    orch=$(dc exec -T orchestrator getent hosts "$k-nb.$DOMAIN" 2>/dev/null | awk 'NR==1 {print $1}' || true)
    coll=$($DOCKER compose --profile core --profile collectors exec -T collector getent hosts "$k-nb.$DOMAIN" 2>/dev/null | awk 'NR==1 {print $1}' || true)
    if [ -n "$orch" ] && [ -n "$coll" ] && [ "$orch" != "$coll" ]; then
      warn "$k-nb.$DOMAIN: Orchestrator → $orch, zentraler Worker (collector) → $coll – in docker-compose.override.yml extra_hosts für beide gleich setzen (INSTALLATION-FRONT 8)"
    fi
    local nbhost=${NETBOX%:*} nbport=443
    [ "$nbhost" = "$NETBOX" ] || nbport=${NETBOX##*:}
    code=$($CURL -s --noproxy '*' "${ca[@]}" -o /dev/null -w '%{http_code}' --resolve "$k-nb.$DOMAIN:$nbport:$nbhost" "https://$k-nb.$DOMAIN:$nbport/login/" 2>/dev/null || true)
    case "$code" in
      200) ok "$k: NetBox vom Front-Proxy aus erreichbar" ;;
      403) fehler "$k: NetBox-Host lehnt diese VM ab (403) – ALLOW/FRONT in /opt/netbox-host/host.conf" ;;
      *) fehler "$k: NetBox nicht erreichbar (HTTP ${code:-–}; Zertifikat, Firewall, gibt es $k auf dem NetBox-Host?)" ;;
    esac
  done
  [ -n "$(kunden)" ] || info "noch keine Kunden – front.sh kunde <kunde> \"<Name>\""
  echo
  if [ "$FEHLER" -gt 0 ]; then echo "Es gibt Fehler (siehe oben)."; return 1; fi
  if [ "$WARNUNGEN" -gt 0 ]; then echo "Alles in Ordnung ($WARNUNGEN Warnungen)."; else echo "Alles in Ordnung."; fi
}


# --- Host-Agent: Kunden aus der Oberfläche (1.3.0, ADR 0050) ---------------------------------
sync_anwenden() {  # läuft in einer Subshell: abbruch beendet nur diese
  lade_conf
  local soll=$1 zeilen art k a b c d e f g
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
    print(SEP.join(["K", k, clean(kunde.get("name"))] + flags
                   + [clean(kunde.get("realm")), zugriff, netze]))
    for link in kunde.get("links") or []:
        print(SEP.join(["L", k, clean(link.get("title")), clean(link.get("url")), clean(link.get("text"))]))
PY
  ) || abbruch "soll.json nicht lesbar ($soll)"
  while IFS=$'\x1f' read -r art k a b c d e f g; do
    case "$art" in
      K)
        kurzname "$k"
        [ -n "$a" ] || abbruch "Kunde '$k' ohne Anzeigename"
        [ -z "$e" ] || realm_pruefen "$e"
        [ "$f" = netze ] || g=""
        zugriff_pruefen "${f:-ueberall}" "$g"
        printf 'NAME=%q\nDOC=%q\nUM=%q\nCMDB_AN=%q\nREALM=%q\nZUGRIFF=%q\nNETZE=%q\nQUELLE=oberflaeche\n' \
          "$a" "$b" "$c" "$d" "$e" "${f:-ueberall}" "$g" >"$(kdir)/$k.conf.neu"
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
  cmd_erzeugen
  [ "$FEHLER" -eq 0 ] || exit 1
}

tor_uebersicht() {  # setzt TOR_BEREIT (Realms mit Client-Secret), TOR_FEHLT (ohne), TOR_GESPERRT (Kunden)
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
}

status_schreiben() {  # $1 = ok|fehler, $2 = Prüfsumme, $3 = Meldung
  local ziel="$FRONT_STATE/status.json" tmp
  tmp=$(mktemp "$FRONT_STATE/.status-XXXXXX")
  # sync wendet in einer Subshell an: Einstellungen hier noch einmal lesen (ohne abzubrechen)
  if [ -z "${TOR:-}" ] && (lade_conf) >/dev/null 2>&1; then lade_conf; fi
  TOR=${TOR:-aus} TOR_BEREIT="" TOR_FEHLT="" TOR_GESPERRT=""
  if [ "$TOR" = an ]; then tor_uebersicht; fi
  python3 -I - "$1" "$2" "$3" "$(kunden | tr '\n' ' ')" "$TOR" "$TOR_BEREIT" "$TOR_FEHLT" "$TOR_GESPERRT" \
    "${HAUPT_REALM:-}" >"$tmp" <<'PY'
import json, sys
from datetime import datetime, timezone
ok, summe, meldung, kunden, tor, bereit, fehlt, gesperrt, haupt = sys.argv[1:10]
print(json.dumps({
    "ok": ok == "ok",
    "zeit": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "soll_sha256": summe,
    "meldung": meldung,
    "kunden": kunden.split(),
    # Anmelde-Tor (1.4.0): welche Realms bereit sind, welchen das Client-Secret fehlt, wer gesperrt ist
    "tor": {
        "an": tor == "an",
        "realms_bereit": sorted(bereit.split()),
        "realms_ohne_secret": sorted(fehlt.split()),
        "gesperrt": gesperrt.split(),
        "haupt_realm": haupt,
    },
}, ensure_ascii=False))
PY
  chmod 644 "$tmp"
  mv -f "$tmp" "$ziel"
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
  if (sync_anwenden "$soll") 2>"$fehlertext"; then rc=0; else rc=$?; fi
  cat "$fehlertext" >&2
  if [ "$rc" -eq 0 ]; then
    status_schreiben ok "$summe" "übernommen"
    rm -f "$fehlertext"
    ok "Kunden aus der Oberfläche übernommen"
  else
    status_schreiben fehler "$summe" "$(grep -m1 'FEHLER' "$fehlertext" | sed -E 's/^ *FEHLER:? *//' || true)"
    rm -f "$fehlertext"
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
    [ -z "$NETBOX" ] || printf '            https://%s-nb.%s/oauth2/callback\n' "$k" "$DOMAIN"
    [ "$DOC" != ja ] || printf '            https://%s-doc.%s/oauth2/callback\n' "$k" "$DOMAIN"
    [ "$UM" != ja ] || [ -z "$MAGISTER" ] || printf '            https://%s-um.%s/oauth2/callback\n' "$k" "$DOMAIN"
    [ "$CMDB_AN" != ja ] || [ -z "$CMDB" ] || printf '            https://%s-cmdb.%s/oauth2/callback\n' "$k" "$DOMAIN"
  done
  if [ "$realm" = "$HAUPT_REALM" ] && [ -n "$INTERN" ]; then
    printf '            https://start.%s/oauth2/callback\n' "$INTERN"
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
    ok "Realm $r: Client-Secret hinterlegt"
  done
  for r in $TOR_FEHLT; do
    warn "Realm $r: Client-Secret fehlt – sudo deploy/front/front.sh tor $r"
  done
  for r in $TOR_BEREIT $TOR_FEHLT; do
    info "Keycloak → Realm $r → Client $TOR_CLIENT (Client authentication an, Standard flow, PKCE S256),"
    info "  Valid redirect URIs:"
    tor_adressen "$r"
  done
  for k in $TOR_GESPERRT; do
    lies_kunde "$k"
    if [ -z "$REALM" ]; then warn "Kunde $k ohne Realm – gesperrt (Kunden-Portal: Realm eintragen)"
    else warn "Kunde $k gesperrt – Client-Secret für Realm $REALM fehlt"; fi
  done
}

cmd_tor() {
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
  tor_an || info "Anmelde-Tor ist aus – einschalten: AUTOMATO_FRONT_TOR=an in .env"
  cmd_erzeugen
}

case "${1:-}" in
  kunde) shift; cmd_kunde "$@" ;;
  link) shift; cmd_link "$@" ;;
  entfernen) shift; cmd_entfernen "$@" ;;
  liste) cmd_liste ;;
  erzeugen) root_noetig; cmd_erzeugen ;;
  pruefen) cmd_pruefen ;;
  sync) shift; cmd_sync "$@" ;;
  agent) cmd_agent ;;
  tor) shift; cmd_tor "$@" ;;
  *) sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
