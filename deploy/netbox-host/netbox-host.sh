#!/usr/bin/env bash
# shellcheck disable=SC1111 # deutsche Anführungszeichen „…“ in Meldungen sind gewollt
# NetBox-Host (0.121.0, ADR 0044): je Kunde eine eigene NetBox (netbox-docker, eigene Datenbank,
# eigenes Redis, eigene Secrets, eigene Sicherung) hinter einem gemeinsamen Proxy (Caddy) mit
# Wildcard-Zertifikat; dazu je Kunde eine Startseite mit Kacheln zu seinen Werkzeugen.
#
# Adressen (ADR 0044, Variante B; flach, weil das Wildcard-Zertifikat nur eine Ebene abdeckt):
#   <kunde>.<domain>          Startseite des Kunden
#   <kunde>-netbox.<domain>   NetBox des Kunden
#   start.<domain>            interne Übersicht aller Kunden
#
# Verwendung (als root bzw. mit sudo):
#   netbox-host.sh init                         Grundgerüst, host.conf, Proxy, Sicherungsschlüssel, cron
#   netbox-host.sh neu <kunde> [Anzeigename]    NetBox für einen Kunden anlegen und starten
#   netbox-host.sh liste                        Kunden mit Zustand, Version und Adresse
#   netbox-host.sh token <kunde>                Lese- und Sync-Benutzer mit Tokens für Automato
#   netbox-host.sh link <kunde> <titel> <url> [beschreibung]   Kachel auf der Startseite ergänzen
#   netbox-host.sh startseiten                  alle Startseiten und die Übersicht neu erzeugen
#   netbox-host.sh sichern <kunde|alle>         Datenbank und Medien verschlüsselt sichern (age)
#   netbox-host.sh wiederherstellen <kunde> <datei.sql.gz.age>   Sicherung einspielen
#   netbox-host.sh update <kunde|alle>          sichern, Version aus host.conf holen, starten, prüfen
#   netbox-host.sh loeschen <kunde> --ja        letzte Sicherung, dann Container, Daten und Seiten weg
#   netbox-host.sh pruefen                      Proxy, Zertifikat, Instanzen, Sicherungen
#
# Kurzname des Kunden: Kleinbuchstaben, Ziffern und Bindestrich (DNS-Label), z. B. "kunde-a".
# Einstellungen: $NBH_DIR/host.conf (Vorlage beim ersten init). Für Tests ersetzbar: NBH_DIR,
# DOCKER, GIT, AGE, AGE_KEYGEN, CURL, NBH_SKIP_ROOT=1, NBH_WAIT=<Sekunden>.
set -euo pipefail

NBH_DIR=${NBH_DIR:-/opt/netbox-host}
DOCKER=${DOCKER:-docker}
GIT=${GIT:-git}
AGE=${AGE:-age}
AGE_KEYGEN=${AGE_KEYGEN:-age-keygen}
CURL=${CURL:-curl}
NBH_WAIT=${NBH_WAIT:-600}
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
NETBOX_DOCKER_URL=${NETBOX_DOCKER_URL:-https://github.com/netbox-community/netbox-docker.git}

ok() { printf '  OK      %s\n' "$*"; }
info() { printf '          %s\n' "$*"; }
warn() { printf '  WARNUNG %s\n' "$*"; WARNUNGEN=$((WARNUNGEN + 1)); }
fehler() { printf '  FEHLER  %s\n' "$*" >&2; FEHLER=$((FEHLER + 1)); }
titel() { printf '\n==> %s\n' "$*"; }
abbruch() { printf 'FEHLER: %s\n' "$*" >&2; exit 1; }
WARNUNGEN=0
FEHLER=0

# --- Einstellungen -----------------------------------------------------------------------------
lade_conf() {
  [ -f "$NBH_DIR/host.conf" ] || abbruch "$NBH_DIR/host.conf fehlt – erst: netbox-host.sh init"
  # shellcheck disable=SC1091
  . "$NBH_DIR/host.conf"
  : "${DOMAIN:?DOMAIN in host.conf setzen}"
  NETBOX_DOCKER_TAG=${NETBOX_DOCKER_TAG:-5.0.2}
  NETBOX_VERSION=${NETBOX_VERSION:-v4.6.10-5.0.2}
  ALLOW=${ALLOW:-}
  TIME_ZONE=${TIME_ZONE:-Europe/Zurich}
  BACKUP_DIR=${BACKUP_DIR:-/var/backups/netbox-host}
  BACKUP_KEEP_DAYS=${BACKUP_KEEP_DAYS:-14}
  AGE_RECIPIENT=${AGE_RECIPIENT:-}
  AUTOMATO_URL=${AUTOMATO_URL:-}
}

root_noetig() {
  [ "${NBH_SKIP_ROOT:-0}" = "1" ] || [ "$(id -u)" -eq 0 ] || abbruch "als root bzw. mit sudo ausführen"
}

kurzname() {  # prüft den Kurznamen des Kunden (DNS-Label, Platz für "-netbox")
  local k=${1:-}
  [ -n "$k" ] || abbruch "Kurzname des Kunden fehlt"
  [[ "$k" =~ ^[a-z0-9]([a-z0-9-]{0,40}[a-z0-9])?$ ]] \
    || abbruch "Kurzname '$k' ungültig: nur a-z, 0-9 und '-', nicht am Anfang/Ende, höchstens 42 Zeichen"
  case "$k" in start | www | proxy | alle) abbruch "Kurzname '$k' ist reserviert" ;; esac
}

kdir() { printf '%s/kunden/%s' "$NBH_DIR" "$1"; }
nbdir() { printf '%s/kunden/%s/netbox' "$NBH_DIR" "$1"; }
nb_host() { printf '%s-netbox.%s' "$1" "$DOMAIN"; }
start_host() { printf '%s.%s' "$1" "$DOMAIN"; }

kunden() {  # alle Kurznamen mit NetBox-Instanz
  local d
  for d in "$NBH_DIR"/kunden/*/netbox; do
    [ -d "$d" ] && basename "$(dirname "$d")"
  done
  return 0
}

nbc() {  # docker compose in der Instanz eines Kunden
  local k=$1
  shift
  (cd "$(nbdir "$k")" && $DOCKER compose "$@")
}

proxyc() { (cd "$NBH_DIR/proxy" && $DOCKER compose "$@"); }

geheim() { openssl rand -hex 32; }

html() {  # Text für HTML maskieren
  local s=$1
  # Ersatz in Anführungszeichen: sonst setzt bash ≥ 5.2 (patsub_replacement) für & den Treffer ein
  s=${s//&/"&amp;"}
  s=${s//</"&lt;"}
  s=${s//>/"&gt;"}
  s=${s//\"/"&quot;"}
  printf '%s' "$s"
}

# --- init ---------------------------------------------------------------------------------------
cmd_init() {
  root_noetig
  titel "Grundgerüst $NBH_DIR"
  install -d -m 750 "$NBH_DIR" "$NBH_DIR/kunden" "$NBH_DIR/proxy" "$NBH_DIR/proxy/sites" \
    "$NBH_DIR/proxy/www" "$NBH_DIR/proxy/pki"
  if [ ! -f "$NBH_DIR/host.conf" ]; then
    install -m 600 "$SCRIPT_DIR/host.conf.example" "$NBH_DIR/host.conf"
    ok "host.conf angelegt – DOMAIN, ALLOW, Zertifikat und AUTOMATO_URL eintragen, dann init erneut"
  else
    ok "host.conf vorhanden"
  fi
  lade_conf
  install -m 644 "$SCRIPT_DIR/proxy/docker-compose.yml" "$NBH_DIR/proxy/docker-compose.yml"
  install -m 644 "$SCRIPT_DIR/proxy/Caddyfile" "$NBH_DIR/proxy/Caddyfile"
  proxy_env
  ok "Proxy-Vorlage nach $NBH_DIR/proxy"

  titel "Zertifikat"
  if [ -s "$NBH_DIR/proxy/pki/wildcard.crt" ] && [ -s "$NBH_DIR/proxy/pki/wildcard.key" ]; then
    ok "pki/wildcard.crt und pki/wildcard.key vorhanden"
  else
    warn "Wildcard-Zertifikat fehlt: Fullchain nach $NBH_DIR/proxy/pki/wildcard.crt, Schlüssel nach pki/wildcard.key (für *.$DOMAIN)"
  fi

  titel "Sicherungsschlüssel (age)"
  if [ -n "$AGE_RECIPIENT" ]; then
    ok "Empfänger in host.conf: ${AGE_RECIPIENT:0:16}…"
  elif command -v "$AGE_KEYGEN" >/dev/null 2>&1; then
    local id=${AGE_IDENTITY:-/root/netbox-host-backup.agekey} empf
    [ -f "$id" ] || (umask 077 && $AGE_KEYGEN -o "$id" >/dev/null 2>&1) || abbruch "age-keygen -o $id fehlgeschlagen"
    empf=$($AGE_KEYGEN -y "$id")
    printf '\nAGE_RECIPIENT=%s\nAGE_IDENTITY=%s\n' "$empf" "$id" >>"$NBH_DIR/host.conf"
    AGE_RECIPIENT=$empf
    ok "neue Identität $id – zusätzlich getrennt von der VM aufbewahren (Tresor)"
  else
    warn "age fehlt (apt-get install -y age) – ohne Schlüssel keine Sicherung"
  fi

  titel "Netz und Proxy"
  if $DOCKER network inspect netbox-front >/dev/null 2>&1; then ok "Netz netbox-front vorhanden"
  else $DOCKER network create netbox-front >/dev/null && ok "Netz netbox-front angelegt"; fi
  cmd_startseiten >/dev/null
  if [ -s "$NBH_DIR/proxy/pki/wildcard.crt" ]; then
    proxyc up -d >/dev/null && ok "Proxy läuft (443)"
  else
    info "Proxy startet nach dem Zertifikat: netbox-host.sh init erneut"
  fi

  titel "Tägliche Sicherung"
  local self
  self="$(cd "$SCRIPT_DIR" && pwd)/$(basename "$0")"
  if [ -d /etc/cron.d ] && [ "${NBH_SKIP_ROOT:-0}" != "1" ]; then
    printf '# NetBox-Host: tägliche Sicherung aller Kunden (netbox-host.sh)\n17 2 * * * root %s sichern alle >>/var/log/netbox-host-backup.log 2>&1\n' \
      "$self" >/etc/cron.d/netbox-host
    ok "/etc/cron.d/netbox-host (täglich 02:17)"
  fi
}

proxy_env() {  # .env des Proxys aus host.conf (Netze, aus denen zugegriffen werden darf)
  local allow=${ALLOW:-}
  [ -n "$allow" ] || { allow="0.0.0.0/0 ::/0"; warn "ALLOW leer – alle Netze dürfen zugreifen (host.conf: ALLOW=\"<netze>\")"; }
  printf 'ALLOW="%s"\n' "$allow" >"$NBH_DIR/proxy/.env"
  chmod 640 "$NBH_DIR/proxy/.env"
}

proxy_reload() {
  if proxyc ps --status running --services 2>/dev/null | grep -qx proxy; then
    proxyc exec -T proxy caddy reload --config /etc/caddy/Caddyfile >/dev/null 2>&1 \
      && ok "Proxy neu geladen" || fehler "Proxy-Konfiguration ungültig: (cd $NBH_DIR/proxy && docker compose logs proxy)"
  else
    info "Proxy läuft noch nicht (Zertifikat?) – netbox-host.sh init"
  fi
}

# --- neu ----------------------------------------------------------------------------------------
cmd_neu() {
  root_noetig
  lade_conf
  local k=${1:-} name=${2:-}
  kurzname "$k"
  local d nb
  d=$(kdir "$k")
  nb=$(nbdir "$k")
  [ ! -d "$nb" ] || abbruch "NetBox für '$k' gibt es schon ($nb)"
  titel "NetBox für $k ($(nb_host "$k"))"
  install -d -m 750 "$d"
  $GIT clone -q -c advice.detachedHead=false --depth 1 -b "$NETBOX_DOCKER_TAG" "$NETBOX_DOCKER_URL" "$nb" \
    || abbruch "netbox-docker $NETBOX_DOCKER_TAG nicht geladen"
  ok "netbox-docker $NETBOX_DOCKER_TAG"
  printf 'NAME=%q\n' "${name:-$k}" >"$d/kunde.conf"
  if [ ! -f "$d/links.tsv" ]; then
    printf 'NetBox\thttps://%s\tIPAM, Geräte, Verkabelung\n' "$(nb_host "$k")" >"$d/links.tsv"
    [ -z "$AUTOMATO_URL" ] || printf 'Dokumentation\t%s\tFirewall-Regeln, Applikationen, Wege\n' "$AUTOMATO_URL" >>"$d/links.tsv"
  fi
  instanz_env "$k"
  instanz_override "$k"
  ok "Secrets in $nb/.env (nur root), Netz netbox-front als nb-$k"
  site_netbox "$k"
  cmd_startseiten >/dev/null
  ok "Proxy-Eintrag und Startseite https://$(start_host "$k")"
  nbc "$k" pull -q >/dev/null 2>&1 || warn "Images nicht geladen (Netz?) – docker compose pull in $nb"
  nbc "$k" up -d netbox >/dev/null || abbruch "Start fehlgeschlagen: cd $nb && docker compose logs netbox"
  warte_netbox "$k" || abbruch "NetBox für $k antwortet nicht – cd $nb && docker compose logs netbox"
  nbc "$k" up -d >/dev/null 2>&1 || warn "Hintergrunddienste (worker) nicht gestartet – cd $nb && docker compose up -d"
  admin_anlegen "$k"
  proxy_reload
  info "Lese- und Sync-Token für Automato: netbox-host.sh token $k"
}

instanz_env() {  # .env der Instanz: Version, Projektname, Secrets (überschreibt die Vorgaben von netbox-docker)
  local k=$1 nb
  nb=$(nbdir "$k")
  (
    umask 077
    {
      printf '# NetBox von %s – nur root; Werte gelten über docker-compose.override.yml\n' "$k"
      printf 'COMPOSE_PROJECT_NAME=nb-%s\n' "$k"
      printf 'VERSION=%s\n' "$NETBOX_VERSION"
      printf 'NBH_HOST=%s\n' "$(nb_host "$k")"
      printf 'NBH_TIME_ZONE=%s\n' "$TIME_ZONE"
      printf 'NBH_SECRET_KEY=%s\n' "$(geheim)"
      printf 'NBH_API_TOKEN_PEPPER_1=%s\n' "$(geheim)"
      printf 'NBH_DB_PASSWORD=%s\n' "$(geheim)"
      printf 'NBH_REDIS_PASSWORD=%s\n' "$(geheim)"
      printf 'NBH_REDIS_CACHE_PASSWORD=%s\n' "$(geheim)"
    } >"$nb/.env"
  )
}

instanz_override() {  # Secrets, Name, Neustart-Regel und Netz zum Proxy für alle Dienste der Instanz
  local k=$1 nb svc
  nb=$(nbdir "$k")
  local services
  services=$(nbc "$k" config --services 2>/dev/null) || abbruch "docker compose config in $nb fehlgeschlagen"
  printf '%s\n' "$services" | grep -qx netbox || abbruch "Dienst 'netbox' fehlt in netbox-docker $NETBOX_DOCKER_TAG"
  {
    echo "# NetBox-Host (netbox-host.sh) – nicht von Hand ändern, wird bei 'update' neu geschrieben"
    echo "services:"
    for svc in $services; do
      echo "  ${svc}:"
      echo "    restart: unless-stopped"
      case "$svc" in
        netbox | netbox-*)
          cat <<'YAML'
    environment:
      SECRET_KEY: ${NBH_SECRET_KEY}
      API_TOKEN_PEPPER_1: ${NBH_API_TOKEN_PEPPER_1}
      DB_PASSWORD: ${NBH_DB_PASSWORD}
      REDIS_PASSWORD: ${NBH_REDIS_PASSWORD}
      REDIS_CACHE_PASSWORD: ${NBH_REDIS_CACHE_PASSWORD}
      ALLOWED_HOSTS: ${NBH_HOST} localhost
      CSRF_TRUSTED_ORIGINS: https://${NBH_HOST}
      CORS_ORIGIN_ALLOW_ALL: "False"
      LOGIN_REQUIRED: "True"
      SKIP_SUPERUSER: "true"
      TIME_ZONE: ${NBH_TIME_ZONE}
YAML
          ;;
        postgres)
          printf '    environment:\n      POSTGRES_PASSWORD: ${NBH_DB_PASSWORD}\n' ;;
        redis)
          printf '    environment:\n      REDIS_PASSWORD: ${NBH_REDIS_PASSWORD}\n' ;;
        redis-cache)
          printf '    environment:\n      REDIS_PASSWORD: ${NBH_REDIS_CACHE_PASSWORD}\n' ;;
      esac
      if [ "$svc" = netbox ]; then
        cat <<YAML
    healthcheck:
      start_period: 300s
      interval: 15s
      retries: 15
    networks:
      default: {}
      front:
        aliases: ["nb-${k}"]
YAML
      fi
    done
    cat <<'YAML'
networks:
  front:
    name: netbox-front
    external: true
YAML
  } >"$nb/docker-compose.override.yml"
}

site_netbox() {  # Proxy-Eintrag der NetBox eines Kunden
  local k=$1
  cat >"$NBH_DIR/proxy/sites/$k-netbox.caddy" <<EOF
# NetBox von $k (netbox-host.sh)
$(nb_host "$k") {
	import common
	reverse_proxy nb-$k:8080
}
EOF
}

warte_netbox() {  # bis die Login-Seite der Instanz antwortet
  local k=$1 i=0
  while [ "$i" -lt "$NBH_WAIT" ]; do
    if nbc "$k" exec -T netbox curl -fsS -o /dev/null http://localhost:8080/login/ >/dev/null 2>&1; then
      ok "NetBox antwortet"
      return 0
    fi
    sleep 5
    i=$((i + 5))
  done
  [ "$NBH_WAIT" = "0" ] && return 0
  return 1
}

admin_anlegen() {  # erster Admin mit Zufallspasswort (einmal angezeigt)
  local k=$1 pass
  pass=$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)
  if nbc "$k" exec -T -e NBH_ADMIN_PASS="$pass" netbox /opt/netbox/netbox/manage.py shell >/dev/null 2>&1 <<'PY'; then
import os
from django.contrib.auth import get_user_model
User = get_user_model()
u, created = User.objects.get_or_create(username="admin")
if created:
    u.is_superuser = u.is_staff = True
    u.set_password(os.environ["NBH_ADMIN_PASS"])
    u.save()
PY
    ok "Admin 'admin' angelegt – Passwort (nur jetzt sichtbar, in den Tresor, beim ersten Login ändern):"
    printf '\n          %s\n\n' "$pass"
  else
    warn "Admin nicht angelegt – später: cd $(nbdir "$k") && docker compose exec netbox /opt/netbox/netbox/manage.py createsuperuser"
  fi
}

# --- token --------------------------------------------------------------------------------------
cmd_token() {
  root_noetig
  lade_conf
  local k=${1:-}
  kurzname "$k"
  [ -d "$(nbdir "$k")" ] || abbruch "keine NetBox für '$k'"
  titel "Benutzer für Automato in der NetBox von $k"
  local lese sync
  lese=$(openssl rand -hex 20)
  sync=$(openssl rand -hex 20)
  nbc "$k" exec -T -e NBH_TOKEN="$lese" netbox /opt/netbox/netbox/manage.py shell >/dev/null <<'PY' || abbruch "Lese-Benutzer nicht angelegt"
import os
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from users.models import ObjectPermission, Token
User = get_user_model()
u, _ = User.objects.get_or_create(username="automato-collector")
u.is_superuser = u.is_staff = False
u.save()
perm, _ = ObjectPermission.objects.get_or_create(name="automato-readonly-view", defaults={"actions": ["view"]})
perm.enabled = True
perm.actions = ["view"]
perm.save()
perm.object_types.set(ContentType.objects.all())
try:
    u.object_permissions.add(perm)
except AttributeError:
    perm.users.add(u)
Token.objects.filter(user=u).delete()
Token(user=u, version=1, token=os.environ["NBH_TOKEN"], write_enabled=False).save()
PY
  ok "automato-collector (nur lesen)"
  NETBOX_DIR="$(nbdir "$k")" SYNC_TOKEN="$sync" DOCKER="$DOCKER" \
    bash "$REPO_DIR/source-of-truth/netbox/sync-user.sh" >/dev/null || abbruch "Sync-Benutzer nicht angelegt"
  ok "automato-sync (schreibt nur die Objekttypen des Syncs, ADR 0010)"
  cat <<EOF

In Automato: Mandant des Kunden → Quellsysteme → + Quellsystem „NetBox“
  Adresse:        https://$(nb_host "$k")
  Lese-Token:     $lese
  Schreib-Token:  $sync      (nur für den NetBox-Sync)
Die Tokens nur dort eintragen bzw. in den Tresor – sie werden nicht gespeichert und nicht
wieder angezeigt; erneut aufrufen ersetzt sie.
EOF
}

# --- Startseiten --------------------------------------------------------------------------------
cmd_link() {
  root_noetig
  lade_conf
  local k=${1:-} t=${2:-} u=${3:-} b=${4:-}
  kurzname "$k"
  [ -d "$(kdir "$k")" ] || abbruch "Kunde '$k' unbekannt"
  [ -n "$t" ] && [ -n "$u" ] || abbruch "Verwendung: link <kunde> <titel> <url> [beschreibung]"
  [[ "$u" =~ ^https?:// ]] || abbruch "URL muss mit https:// beginnen"
  [[ "$t$u$b" != *$'\t'* ]] || abbruch "keine Tabulatoren in Titel, URL oder Beschreibung"
  printf '%s\t%s\t%s\n' "$t" "$u" "$b" >>"$(kdir "$k")/links.tsv"
  cmd_startseiten >/dev/null
  ok "Kachel „$t“ auf https://$(start_host "$k")"
}

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

cmd_startseiten() {
  lade_conf
  local k d name www=$NBH_DIR/proxy/www
  install -d -m 755 "$www"
  for d in "$NBH_DIR"/kunden/*/; do
    [ -d "$d" ] || continue
    k=$(basename "$d")
    name=$k
    # shellcheck disable=SC1091
    [ -f "$d/kunde.conf" ] && name=$(. "$d/kunde.conf" && printf '%s' "${NAME:-$k}")
    install -d -m 755 "$www/$k"
    seite "$name" "Werkzeuge · tools" <"$d/links.tsv" >"$www/$k/index.html"
    cat >"$NBH_DIR/proxy/sites/$k.caddy" <<EOF
# Startseite von $k (netbox-host.sh)
$(start_host "$k") {
	import common
	root * /srv/www/$k
	file_server
}
EOF
  done
  install -d -m 755 "$www/_start"
  {
    for d in "$NBH_DIR"/kunden/*/; do
      [ -d "$d" ] || continue
      k=$(basename "$d")
      name=$k
      # shellcheck disable=SC1091
      [ -f "$d/kunde.conf" ] && name=$(. "$d/kunde.conf" && printf '%s' "${NAME:-$k}")
      printf '%s\thttps://%s\tStartseite · NetBox: %s\n' "$name" "$(start_host "$k")" "$(nb_host "$k")"
    done
  } | seite "Kunden" "Übersicht aller Kunden · all customers" >"$www/_start/index.html"
  cat >"$NBH_DIR/proxy/sites/_start.caddy" <<EOF
# Interne Übersicht (netbox-host.sh)
start.$DOMAIN {
	import common
	root * /srv/www/_start
	file_server
}
EOF
  ok "Startseiten erzeugt"
  proxy_reload
}

# --- liste --------------------------------------------------------------------------------------
cmd_liste() {
  root_noetig
  lade_conf
  local k zustand version
  printf '%-20s %-12s %-18s %s\n' KUNDE ZUSTAND VERSION ADRESSE
  for k in $(kunden); do
    zustand=$(nbc "$k" ps --format '{{.Service}} {{.State}}' 2>/dev/null | awk '$1=="netbox"{print $2}' || true)
    version=$(sed -n 's/^VERSION=//p' "$(nbdir "$k")/.env" 2>/dev/null)
    printf '%-20s %-12s %-18s https://%s\n' "$k" "${zustand:-gestoppt}" "${version:-?}" "$(nb_host "$k")"
  done
}

# --- sichern / wiederherstellen -----------------------------------------------------------------
sichere_einen() {
  local k=$1 ziel ts db media
  ziel=$BACKUP_DIR/$k
  install -d -m 700 "$ziel"
  ts=$(date +%Y%m%d-%H%M%S)
  db=$ziel/$k-$ts.sql.gz.age
  media=$ziel/$k-$ts.media.tar.gz.age
  if ! nbc "$k" exec -T postgres sh -c 'pg_dump -c --if-exists -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip | $AGE -r "$AGE_RECIPIENT" -o "$db.tmp"; then
    rm -f "$db.tmp"
    fehler "$k: Datenbank nicht gesichert"
    return 1
  fi
  mv "$db.tmp" "$db"
  if nbc "$k" exec -T netbox tar -C /opt/netbox/netbox/media -cf - . 2>/dev/null | gzip | $AGE -r "$AGE_RECIPIENT" -o "$media.tmp"; then
    mv "$media.tmp" "$media"
  else
    rm -f "$media.tmp"
    warn "$k: Medien nicht gesichert"
  fi
  find "$ziel" -name "$k-*.age" -type f -mtime +"$BACKUP_KEEP_DAYS" -delete 2>/dev/null || true
  ok "$k: $(basename "$db")"
}

cmd_sichern() {
  root_noetig
  lade_conf
  local wer=${1:-} k
  [ -n "$wer" ] || abbruch "Verwendung: sichern <kunde|alle>"
  [ -n "$AGE_RECIPIENT" ] || abbruch "AGE_RECIPIENT fehlt in host.conf – netbox-host.sh init"
  titel "Sicherung nach $BACKUP_DIR"
  if [ "$wer" = alle ]; then
    for k in $(kunden); do sichere_einen "$k" || true; done
  else
    kurzname "$wer"
    [ -d "$(nbdir "$wer")" ] || abbruch "keine NetBox für '$wer'"
    sichere_einen "$wer"
  fi
  [ "$FEHLER" -eq 0 ]
}

cmd_wiederherstellen() {
  root_noetig
  lade_conf
  local k=${1:-} datei=${2:-} id=${AGE_IDENTITY:-/root/netbox-host-backup.agekey} media
  kurzname "$k"
  [ -d "$(nbdir "$k")" ] || abbruch "keine NetBox für '$k' (erst: neu $k)"
  [ -f "$datei" ] || abbruch "Sicherung '$datei' fehlt"
  [ -r "$id" ] || abbruch "age-Identität $id fehlt (aus dem Tresor zurückholen)"
  titel "Wiederherstellen $k aus $(basename "$datei")"
  # shellcheck disable=SC2046 # Dienste der Instanz, die NetBox nutzen (netbox, netbox-worker, …)
  nbc "$k" stop $(nbc "$k" config --services 2>/dev/null | grep '^netbox') >/dev/null 2>&1 || true
  $AGE -d -i "$id" "$datei" | gunzip | nbc "$k" exec -T postgres sh -c 'psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" "$POSTGRES_DB"' >/dev/null \
    || abbruch "Datenbank nicht eingespielt – NetBox bleibt gestoppt (cd $(nbdir "$k") && docker compose up -d)"
  ok "Datenbank eingespielt"
  media=${datei%.sql.gz.age}.media.tar.gz.age
  if [ -f "$media" ]; then
    $AGE -d -i "$id" "$media" | gunzip | nbc "$k" run --rm --no-deps -T --entrypoint tar netbox -C /opt/netbox/netbox/media -xf - \
      && ok "Medien eingespielt" || warn "Medien nicht eingespielt"
  fi
  nbc "$k" up -d >/dev/null && warte_netbox "$k" || fehler "NetBox startet nicht – cd $(nbdir "$k") && docker compose logs netbox"
  [ "$FEHLER" -eq 0 ]
}

# --- update -------------------------------------------------------------------------------------
update_einen() {
  local k=$1 nb
  nb=$(nbdir "$k")
  titel "Update $k auf netbox-docker $NETBOX_DOCKER_TAG / NetBox $NETBOX_VERSION"
  sichere_einen "$k" || { fehler "$k: ohne Sicherung kein Update"; return 1; }
  (cd "$nb" && $GIT fetch -q --depth 1 origin tag "$NETBOX_DOCKER_TAG" && $GIT -c advice.detachedHead=false checkout -q "$NETBOX_DOCKER_TAG") \
    || { fehler "$k: netbox-docker $NETBOX_DOCKER_TAG nicht geladen"; return 1; }
  sed -i "s|^VERSION=.*|VERSION=$NETBOX_VERSION|" "$nb/.env"
  instanz_override "$k"
  nbc "$k" pull -q >/dev/null 2>&1 || warn "$k: Images nicht geladen"
  nbc "$k" up -d >/dev/null || { fehler "$k: Start fehlgeschlagen"; return 1; }
  if warte_netbox "$k"; then ok "$k läuft mit $NETBOX_VERSION"
  else
    fehler "$k: NetBox antwortet nicht – zurück: alte Werte in host.conf, update $k, ggf. wiederherstellen $k <letzte Sicherung>"
    return 1
  fi
}

cmd_update() {
  root_noetig
  lade_conf
  local wer=${1:-} k
  [ -n "$wer" ] || abbruch "Verwendung: update <kunde|alle>"
  [ -n "$AGE_RECIPIENT" ] || abbruch "AGE_RECIPIENT fehlt in host.conf – ohne Sicherung kein Update"
  if [ "$wer" = alle ]; then
    for k in $(kunden); do update_einen "$k" || true; done
  else
    kurzname "$wer"
    [ -d "$(nbdir "$wer")" ] || abbruch "keine NetBox für '$wer'"
    update_einen "$wer" || true
  fi
  [ "$FEHLER" -eq 0 ]
}

# --- loeschen -----------------------------------------------------------------------------------
cmd_loeschen() {
  root_noetig
  lade_conf
  local k=${1:-} ja=${2:-}
  kurzname "$k"
  [ -d "$(kdir "$k")" ] || abbruch "Kunde '$k' unbekannt"
  [ "$ja" = "--ja" ] || abbruch "löscht NetBox, Daten und Startseite von '$k' – bestätigen mit: loeschen $k --ja"
  titel "Löschen $k"
  if [ -d "$(nbdir "$k")" ]; then
    if [ -n "$AGE_RECIPIENT" ]; then
      sichere_einen "$k" || abbruch "letzte Sicherung fehlgeschlagen – nichts gelöscht"
    else
      abbruch "AGE_RECIPIENT fehlt – ohne letzte Sicherung wird nichts gelöscht"
    fi
    nbc "$k" down -v --remove-orphans >/dev/null 2>&1 || warn "$k: Container nicht entfernt"
  fi
  rm -rf "$(kdir "$k")" "$NBH_DIR/proxy/www/$k"
  rm -f "$NBH_DIR/proxy/sites/$k.caddy" "$NBH_DIR/proxy/sites/$k-netbox.caddy"
  ok "$k entfernt – Sicherungen bleiben in $BACKUP_DIR/$k"
  cmd_startseiten >/dev/null
}

# --- pruefen ------------------------------------------------------------------------------------
cmd_pruefen() {
  root_noetig
  lade_conf
  titel "Proxy"
  if proxyc ps --status running --services 2>/dev/null | grep -qx proxy; then ok "Proxy läuft"
  else fehler "Proxy läuft nicht – netbox-host.sh init"; fi
  if [ -s "$NBH_DIR/proxy/pki/wildcard.crt" ]; then
    if openssl x509 -in "$NBH_DIR/proxy/pki/wildcard.crt" -noout -checkend $((30 * 86400)) >/dev/null 2>&1; then
      ok "Zertifikat gültig bis $(openssl x509 -in "$NBH_DIR/proxy/pki/wildcard.crt" -noout -enddate | cut -d= -f2)"
    else
      warn "Zertifikat läuft in weniger als 30 Tagen ab (bzw. ist abgelaufen)"
    fi
  else
    fehler "Wildcard-Zertifikat fehlt ($NBH_DIR/proxy/pki/wildcard.crt)"
  fi
  [ -n "$ALLOW" ] && ok "Zugriff nur aus: $ALLOW" || warn "ALLOW leer – alle Netze dürfen zugreifen"
  titel "Kunden"
  local k code neu
  for k in $(kunden); do
    if nbc "$k" exec -T netbox curl -fsS -o /dev/null http://localhost:8080/login/ >/dev/null 2>&1; then
      ok "$k: NetBox läuft"
    else
      fehler "$k: NetBox antwortet im Container nicht – cd $(nbdir "$k") && docker compose logs netbox"
    fi
    code=$($CURL -sk -o /dev/null -w '%{http_code}' --resolve "$(nb_host "$k"):443:127.0.0.1" "https://$(nb_host "$k")/login/" 2>/dev/null || true)
    case "$code" in
      200) ok "$k: über den Proxy erreichbar (https://$(nb_host "$k"))" ;;
      403) ok "$k: Proxy antwortet (403 – die VM selbst ist nicht in ALLOW)" ;;
      *) fehler "$k: über den Proxy nicht erreichbar (HTTP ${code:-–})" ;;
    esac
    neu=$(find "$BACKUP_DIR/$k" -name "$k-*.sql.gz.age" -type f -mmin -$((26 * 60)) 2>/dev/null | head -1 || true)
    [ -n "$neu" ] && ok "$k: Sicherung jünger als 26 h" || warn "$k: keine Sicherung der letzten 26 h"
  done
  [ -n "$(kunden)" ] || info "noch keine Kunden – netbox-host.sh neu <kunde>"
  echo
  if [ "$FEHLER" -gt 0 ]; then echo "Es gibt Fehler (siehe oben)."; return 1; fi
  if [ "$WARNUNGEN" -gt 0 ]; then echo "Alles in Ordnung ($WARNUNGEN Warnungen)."; else echo "Alles in Ordnung."; fi
}

case "${1:-}" in
  init) cmd_init ;;
  neu) shift; cmd_neu "$@" ;;
  liste) cmd_liste ;;
  token) shift; cmd_token "$@" ;;
  link) shift; cmd_link "$@" ;;
  startseiten) root_noetig; cmd_startseiten ;;
  sichern) shift; cmd_sichern "$@" ;;
  wiederherstellen) shift; cmd_wiederherstellen "$@" ;;
  update) shift; cmd_update "$@" ;;
  loeschen) shift; cmd_loeschen "$@" ;;
  pruefen) cmd_pruefen ;;
  *) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
