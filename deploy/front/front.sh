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
#   front.sh kunde <kunde> "<Anzeigename>" [--doc] [--um] [--cmdb]   anlegen bzw. ändern
#   front.sh link <kunde> <titel> <url> [beschreibung]               weitere Kachel auf der Startseite
#   front.sh entfernen <kunde> --ja                                 Kunde aus dem Front-Proxy nehmen
#   front.sh liste                                                  Kunden mit ihren Werkzeugen
#   front.sh erzeugen                                               alles neu erzeugen, Proxy neu laden
#   front.sh pruefen                                                Einstellungen, Zertifikate, Wege
#
# Einstellungen: AUTOMATO_FRONT_* in .env (siehe .env.example). Kunden: deploy/front/kunden/,
# erzeugt: deploy/front/generated/ (beides nicht im Repo). Zertifikate: deploy/proxy/pki/mgmt.crt|key
# (Kunden-Adressen) und mgmt-int.crt|key (interne Adressen).
# Kurzname: Kleinbuchstaben, Ziffern und Bindestrich (DNS-Label), nicht auf -nb/-doc/-um/-cmdb endend.
# Für Tests ersetzbar: DOCKER, CURL, FRONT_DIR, FRONT_PKI, AUTOMATO_ENV_FILE, FRONT_SKIP_ROOT=1.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
FRONT_DIR=${FRONT_DIR:-$SCRIPT_DIR}
FRONT_PKI=${FRONT_PKI:-$REPO_DIR/deploy/proxy/pki}
DOCKER=${DOCKER:-docker}
CURL=${CURL:-curl}
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
gen() { printf '%s/generated' "$FRONT_DIR"; }

kunden() {  # alle Kurznamen
  local f
  for f in "$(kdir)"/*.conf; do
    [ -f "$f" ] && basename "$f" .conf
  done
  return 0
}

lies_kunde() {  # setzt NAME, DOC, UM, CMDB_AN aus kunden/<k>.conf
  local k=$1
  NAME=$k DOC=nein UM=nein CMDB_AN=nein
  # shellcheck disable=SC1090 # mit printf %q geschrieben (kunde)
  . "$(kdir)/$k.conf"
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
seite() {  # HTML-Gerüst: $1 Titel, $2 Untertitel, Kacheln auf stdin (Titel\tURL\tBeschreibung)
  local t=$1 sub=$2 kt ku kb
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
  cat <<'EOF'
</div>
<footer>Anmeldung je Werkzeug · sign-in per tool</footer>
</main>
</body>
</html>
EOF
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
schreibe_snippets() {
  # Interne Adressen nie über die WAF – auch wenn das Management-Netz die WAF einschliesst (sonst
  # käme ein Kunde mit gesetztem Host-Kopf über die WAF aufs Portal)
  local ohne_waf=""
  [ -z "$WAF" ] || ohne_waf=$'\n\t'"@front_waf remote_ip $WAF"$'\n\t''respond @front_waf "Zugriff nur aus dem Management-Netz" 403'
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
	respond @front_fremd "Zugriff nur über die WAF" 403
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
	@front_fremd not remote_ip $MGMT
	respond @front_fremd "Zugriff nur aus dem Management-Netz" 403$ohne_waf
}

# Statische Seite: nichts nachladen, nichts einbetten
(front-seite) {
	header Content-Security-Policy "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
	file_server
}
EOF
}

schreibe_kunde() {  # Caddy-Einträge eines Kunden
  local k=$1 trust=""
  lies_kunde "$k"
  [ -z "$NETBOX_CA" ] || trust=$'\n\t\t\ttls_trust_pool file /etc/caddy/pki/'"$NETBOX_CA"
  cat <<EOF
# Kunde $k (front.sh) – nicht von Hand ändern
$k.$DOMAIN {
	import front-kunden
	root * /srv/front/$k
	import front-seite
}
EOF
  if [ -n "$NETBOX" ]; then
    cat <<EOF

$k-nb.$DOMAIN {
	import front-kunden
	reverse_proxy https://$NETBOX {
		header_up Host {host}
		transport http {
			tls_server_name {host}$trust
		}
	}
}
EOF
  fi
  if [ "$DOC" = ja ]; then
    cat <<EOF

$k-doc.$DOMAIN {
	import front-kunden
	@internal path /api/internal /api/internal/*
	respond @internal 404
	reverse_proxy web-ui:5173 {
		import /etc/caddy/automato-tls/upstream-{\$AUTOMATO_INTERNAL_TLS:off}.caddy
	}
}
EOF
  fi
  if [ "$UM" = ja ] && [ -n "$MAGISTER" ]; then
    printf '\n%s-um.%s {\n\timport front-kunden\n\treverse_proxy %s\n}\n' "$k" "$DOMAIN" "$MAGISTER"
  fi
  if [ "$CMDB_AN" = ja ] && [ -n "$CMDB" ]; then
    printf '\n%s-cmdb.%s {\n\timport front-kunden\n\treverse_proxy %s\n}\n' "$k" "$DOMAIN" "$CMDB"
  fi
}

proxy_reload() {
  if $DOCKER compose --profile proxy ps --status running --services 2>/dev/null | grep -qx proxy; then
    if $DOCKER compose --profile proxy exec -T proxy caddy reload --config /etc/caddy/Caddyfile >/dev/null 2>&1; then
      ok "Proxy neu geladen"
    else
      fehler "Proxy-Konfiguration ungültig – alte bleibt aktiv: docker compose --profile proxy logs proxy"
    fi
  else
    info "Proxy läuft nicht – Start: docker compose --profile core --profile proxy up -d"
  fi
}

cmd_erzeugen() {
  lade_conf
  local g k www name
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
    kacheln "$k" | seite "$NAME" "Werkzeuge · tools" >"$www/$k/index.html"
  done
  # Unbekannte Namen unter den Domains (z. B. <kunde>-doc ohne --doc): 404 statt einer leeren
  # Antwort; die bekannten Namen sind genauer und gehen vor
  {
    printf '# Unbekannte Adressen (front.sh) – nicht von Hand ändern\n'
    printf '*.%s {\n\timport front-kunden\n\trespond "Unbekannte Adresse" 404\n}\n' "$DOMAIN"
    [ -z "$INTERN" ] || printf '\n*.%s {\n\timport front-intern\n\trespond "Unbekannte Adresse" 404\n}\n' "$INTERN"
  } >"$g/sites/_unbekannt.caddy"
  if [ -n "$INTERN" ]; then
    install -d -m 755 "$www/_portal"
    {
      for k in $(kunden); do
        lies_kunde "$k"
        name=$NAME
        printf '%s\thttps://%s.%s\t%s\n' "$name" "$k" "$DOMAIN" "$(werkzeuge "$k")"
      done
    } | seite "Kunden" "Portal · alle Kunden" >"$www/_portal/index.html"
    local erlaubt="remote_ip $MGMT"
    [ -z "$WAF" ] || erlaubt+=$'\n\t\t'"not remote_ip $WAF"
    cat >"$g/sites/_intern.caddy" <<EOF
# Interne Adressen (front.sh) – nicht von Hand ändern
start.$INTERN {
	import front-intern
	root * /srv/front/_portal
	import front-seite
}

# Verwaltung (Cockpit, Version 2) – bis dahin das Portal; redir läuft vor respond, darum nur für
# erlaubte Adressen (sonst gilt die Sperre aus front-intern)
$INTERN {
	import front-intern
	@front_portal {
		$erlaubt
	}
	redir @front_portal https://start.$INTERN{uri} 302
}
EOF
  else
    warn "AUTOMATO_FRONT_DOMAIN_INT fehlt – kein internes Portal"
  fi
  chmod -R a+rX "$g"
  ok "Front-Proxy erzeugt ($(kunden | wc -l | tr -d ' ') Kunden)"
  proxy_reload
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
  local k=${1:-} name=${2:-} doc=nein um=nein cmdb=nein a
  kurzname "$k"
  [ -n "$name" ] && [[ "$name" != --* ]] || abbruch "Verwendung: kunde <kunde> \"<Anzeigename>\" [--doc] [--um] [--cmdb]"
  shift 2
  for a in "$@"; do
    case "$a" in
      --doc) doc=ja ;;
      --um) um=ja ;;
      --cmdb) cmdb=ja ;;
      *) abbruch "unbekannte Angabe '$a' (--doc, --um, --cmdb)" ;;
    esac
  done
  install -d -m 750 "$(kdir)"
  printf 'NAME=%q\nDOC=%q\nUM=%q\nCMDB_AN=%q\n' "$name" "$doc" "$um" "$cmdb" >"$(kdir)/$k.conf"
  titel "Kunde $k im Front-Proxy"
  ok "Startseite https://$k.$DOMAIN"
  [ -n "$NETBOX" ] && ok "NetBox https://$k-nb.$DOMAIN → $NETBOX" || warn "AUTOMATO_FRONT_NETBOX fehlt – keine Weiterleitung zur NetBox"
  if [ "$doc" = ja ]; then
    ok "Dokumentation https://$k-doc.$DOMAIN"
    info "Keycloak (Client automato-web): Valid redirect URI https://$k-doc.$DOMAIN/* und Web origin https://$k-doc.$DOMAIN ergänzen"
  fi
  [ "$um" != ja ] || { [ -n "$MAGISTER" ] && ok "Benutzerverwaltung https://$k-um.$DOMAIN → $MAGISTER" || warn "--um gesetzt, aber AUTOMATO_FRONT_MAGISTER leer – bleibt aus"; }
  [ "$cmdb" != ja ] || { [ -n "$CMDB" ] && ok "CMDB https://$k-cmdb.$DOMAIN → $CMDB" || warn "--cmdb gesetzt, aber AUTOMATO_FRONT_CMDB leer – bleibt aus"; }
  cmd_erzeugen
}

cmd_link() {
  root_noetig
  lade_conf
  local k=${1:-} t=${2:-} u=${3:-} b=${4:-}
  kurzname "$k"
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
  [ -f "$(kdir)/$k.conf" ] || abbruch "Kunde '$k' unbekannt"
  [ "$ja" = "--ja" ] || abbruch "nimmt Startseite und Weiterleitungen von '$k' aus dem Front-Proxy (NetBox und Daten bleiben) – bestätigen mit: entfernen $k --ja"
  rm -f "$(kdir)/$k.conf" "$(kdir)/$k.links.tsv"
  ok "$k aus dem Front-Proxy genommen"
  cmd_erzeugen
}

cmd_liste() {
  lade_conf
  local k
  printf '%-20s %-28s %s\n' KUNDE WERKZEUGE STARTSEITE
  for k in $(kunden); do
    printf '%-20s %-28s https://%s.%s\n' "$k" "$(werkzeuge "$k")" "$k" "$DOMAIN"
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

  titel "Zertifikate"
  pruefe_zertifikat mgmt "*.$DOMAIN"
  [ -z "$INTERN" ] || pruefe_zertifikat mgmt-int "$INTERN" "*.$INTERN"

  titel "Proxy"
  if $DOCKER compose --profile proxy ps --status running --services 2>/dev/null | grep -qx proxy; then
    ok "Proxy läuft"
  else
    fehler "Proxy läuft nicht – docker compose --profile core --profile proxy up -d"
  fi
  [ -f "$(gen)/snippets.caddy" ] && ok "Front-Proxy erzeugt" || fehler "noch nicht erzeugt – front.sh erzeugen"

  titel "Kunden"
  local k code ca=()
  [ -z "$NETBOX_CA" ] || ca=(--cacert "$FRONT_PKI/$NETBOX_CA")
  for k in $(kunden); do
    code=$($CURL -sk --noproxy '*' -o /dev/null -w '%{http_code}' --resolve "$k.$DOMAIN:443:127.0.0.1" "https://$k.$DOMAIN/" 2>/dev/null || true)
    case "$code" in
      200 | 403) ok "$k: Startseite antwortet (HTTP $code; 403 = diese VM ist nicht freigegeben)" ;;
      *) fehler "$k: Startseite antwortet nicht (HTTP ${code:-–})" ;;
    esac
    [ -n "$NETBOX" ] || continue
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

case "${1:-}" in
  kunde) shift; cmd_kunde "$@" ;;
  link) shift; cmd_link "$@" ;;
  entfernen) shift; cmd_entfernen "$@" ;;
  liste) cmd_liste ;;
  erzeugen) root_noetig; cmd_erzeugen ;;
  pruefen) cmd_pruefen ;;
  *) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
