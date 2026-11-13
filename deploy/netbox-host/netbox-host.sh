#!/usr/bin/env bash
# shellcheck disable=SC1111 # deutsche Anführungszeichen „…“ in Meldungen sind gewollt
# NetBox-Host (0.121.0, ADR 0044): je Kunde eine eigene NetBox (netbox-docker, eigene Datenbank,
# eigenes Redis, eigene Secrets, eigene Sicherung) hinter einem gemeinsamen Proxy (Caddy).
#
# Seit 1.1.0 (ADR 0044 Nachtrag) ist dieser Host ein Backend: Kunden kommen über die WAF auf den
# Front-Proxy der Automato-VM (Startseiten, Portal, Weiterleitung je Werkzeug); hierher dürfen
# nur der Front-Proxy und das Management-Netz (ALLOW). Adressen (flach, ein Wildcard-Zertifikat):
#   <kunde>-nb.<domain>       NetBox des Kunden (dieser Host)
#   <kunde>.<domain>, <kunde>-doc|-um|-cmdb.<domain>, start.<interne domain>  Front-Proxy
#
# Verwendung (als root bzw. mit sudo):
#   netbox-host.sh init                         Grundgerüst, host.conf, Proxy, Sicherungsschlüssel, cron
#   netbox-host.sh neu <kunde> [Anzeigename]    NetBox für einen Kunden anlegen und starten
#   netbox-host.sh liste                        Kunden mit Zustand, Version und Adresse
#   netbox-host.sh token <kunde>                Lese- und Sync-Benutzer mit Tokens für Automato
#   netbox-host.sh sichern <kunde|alle|konfig>  Datenbank und Medien (alle: auch die Konfiguration) mit age
#   netbox-host.sh wiederherstellen <kunde> <datei.sql.gz.age>   Sicherung einspielen
#   netbox-host.sh update <kunde|alle>          sichern, Version aus host.conf holen, starten, prüfen
#   netbox-host.sh loeschen <kunde> --ja        letzte Sicherung, dann Container, Daten und Proxy-Eintrag weg
#   netbox-host.sh pruefen                      Proxy, Zertifikat, Instanzen, Sicherungen
#   netbox-host.sh sso <kunde|alle>             Anmeldung über das Anmelde-Tor einrichten (1.4.0)
#
# Seit 1.4.0 (ADR 0051) meldet sich NetBox über das Anmelde-Tor des Front-Proxys an (SSO=an in
# host.conf): der Front-Proxy setzt X-Automato-User (<benutzer>@<realm>) und X-Automato-Groups
# (Realm-Rollen als role:<rolle>), dieser Proxy nimmt sie nur von FRONT an. Rechte: role:admin =
# Superuser, role:operator = bearbeiten, alle anderen lesen; lokale Konten (admin, Tokens) bleiben.
#
# Kurzname des Kunden: Kleinbuchstaben, Ziffern und Bindestrich (DNS-Label), z. B. "kunde-a"; nicht
# auf -nb, -doc, -um oder -cmdb endend (sonst fiele er mit einem Werkzeug eines anderen Kunden zusammen).
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
  FRONT=${FRONT:-}
  TIME_ZONE=${TIME_ZONE:-Europe/Zurich}
  BACKUP_DIR=${BACKUP_DIR:-/var/backups/netbox-host}
  BACKUP_KEEP_DAYS=${BACKUP_KEEP_DAYS:-14}
  AGE_RECIPIENT=${AGE_RECIPIENT:-}
  SSO=${SSO:-an}
  case "$SSO" in an | aus) ;; *) abbruch "SSO in host.conf: an oder aus ('$SSO')" ;; esac
  # Port des Proxys auf dem Host (1.9.1): 443, ausser die VM trägt schon einen Dienst auf 443
  # (z. B. Dev auf der Magister-VM); der Front-Proxy nennt ihn in AUTOMATO_FRONT_NETBOX=<ip>:<port>
  PORT=${PORT:-443}
  [[ "$PORT" =~ ^[0-9]{1,5}$ ]] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] \
    || abbruch "PORT in host.conf: Zahl von 1 bis 65535 ('$PORT')"
}

root_noetig() {
  [ "${NBH_SKIP_ROOT:-0}" = "1" ] || [ "$(id -u)" -eq 0 ] || abbruch "als root bzw. mit sudo ausführen"
}

kurzname() {  # prüft den Kurznamen des Kunden (DNS-Label, Platz für "-cmdb")
  local k=${1:-}
  [ -n "$k" ] || abbruch "Kurzname des Kunden fehlt"
  [[ "$k" =~ ^[a-z0-9]([a-z0-9-]{0,40}[a-z0-9])?$ ]] \
    || abbruch "Kurzname '$k' ungültig: nur a-z, 0-9 und '-', nicht am Anfang/Ende, höchstens 42 Zeichen"
  case "$k" in start | www | proxy | alle | konfig | mgmt) abbruch "Kurzname '$k' ist reserviert" ;; esac
  # <kunde>-nb, -doc, -um, -cmdb sind die Werkzeuge eines Kunden (ADR 0044 Nachtrag)
  case "$k" in *-nb | *-doc | *-um | *-cmdb) abbruch "Kurzname '$k' ist reserviert (endet wie ein Werkzeug: -nb, -doc, -um, -cmdb)" ;; esac
}

kdir() { printf '%s/kunden/%s' "$NBH_DIR" "$1"; }
nbdir() { printf '%s/kunden/%s/netbox' "$NBH_DIR" "$1"; }
nb_host() { printf '%s-nb.%s' "$1" "$DOMAIN"; }

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

# --- init ---------------------------------------------------------------------------------------
cmd_init() {
  root_noetig
  titel "Grundgerüst $NBH_DIR"
  install -d -m 750 "$NBH_DIR" "$NBH_DIR/kunden" "$NBH_DIR/proxy" "$NBH_DIR/proxy/sites" \
    "$NBH_DIR/proxy/pki"
  if [ ! -f "$NBH_DIR/host.conf" ]; then
    install -m 600 "$SCRIPT_DIR/host.conf.example" "$NBH_DIR/host.conf"
    ok "host.conf angelegt – DOMAIN, ALLOW und FRONT eintragen, Zertifikat ablegen, dann init erneut"
  else
    ok "host.conf vorhanden"
  fi
  lade_conf
  install -m 644 "$SCRIPT_DIR/proxy/docker-compose.yml" "$NBH_DIR/proxy/docker-compose.yml"
  install -m 644 "$SCRIPT_DIR/proxy/Caddyfile" "$NBH_DIR/proxy/Caddyfile"
  proxy_env
  [ -z "${DOMAIN:-}" ] || site_auffang
  ok "Proxy-Vorlage nach $NBH_DIR/proxy"

  titel "Zertifikat"
  if [ -s "$NBH_DIR/proxy/pki/wildcard.crt" ] && [ -s "$NBH_DIR/proxy/pki/wildcard.key" ]; then
    ok "pki/wildcard.crt und pki/wildcard.key vorhanden"
    # Passt das Zertifikat nicht, startet Caddy nicht und startet endlos neu – vorher abbrechen
    zert_pruefen || abbruch "Proxy nicht gestartet – erst das Zertifikat richten, dann init erneut"
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
  # Startseiten liegen seit 1.1.0 auf dem Front-Proxy – Reste früherer Fassungen entfernen
  rm -rf "$NBH_DIR/proxy/www"
  rm -f "$NBH_DIR/proxy/sites/_start.caddy"
  if [ -s "$NBH_DIR/proxy/pki/wildcard.crt" ]; then
    # Hängt der Proxy in der Neustart-Schleife (z. B. altes Zertifikat), erzeugt `up -d` ihn nicht
    # neu, und Docker wartet zwischen den Versuchen immer länger – jetzt neu starten.
    if proxyc ps --status restarting --services 2>/dev/null | grep -qx proxy; then
      proxyc restart proxy >/dev/null && info "Proxy aus der Neustart-Schleife geholt"
    fi
    proxyc up -d >/dev/null && ok "Proxy läuft (Port $PORT)"
    # ein laufender Proxy liest Caddyfile und sites/ erst beim Neuladen (z. B. neuer Auffang)
    proxy_reload
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

zert_pruefen() {  # Zertifikat des Proxys: passt zum Schlüssel, nennt *.DOMAIN (1.9.1)
  # Anlass (Dev, 2026-11-12): in wildcard.crt stand nur das Zertifikat der CA – das ausgestellte
  # fehlte beim Zusammensetzen der Kette. Caddy meldete „private key does not match public key“
  # und startete endlos neu; pruefen zeigte nur das Ablaufdatum (das der CA).
  local crt="$NBH_DIR/proxy/pki/wildcard.crt" key="$NBH_DIR/proxy/pki/wildcard.key" a b
  a=$(openssl x509 -in "$crt" -noout -pubkey 2>/dev/null) || a=""
  b=$(openssl pkey -in "$key" -pubout 2>/dev/null) || b=""
  if [ -z "$a" ] || [ "$a" != "$b" ]; then
    fehler "pki/wildcard.crt passt nicht zu pki/wildcard.key – zuerst steht das ausgestellte Zertifikat (hier: $(openssl x509 -in "$crt" -noout -subject 2>/dev/null || echo 'unlesbar')), danach die Kette"
    return 1
  fi
  if openssl x509 -in "$crt" -noout -ext subjectAltName 2>/dev/null | grep -qF "*.$DOMAIN"; then
    ok "Zertifikat für *.$DOMAIN, passt zum Schlüssel"
  else
    warn "Zertifikat nennt *.$DOMAIN nicht (subjectAltName) – der Front-Proxy lehnt es ab"
  fi
}

proxy_env() {  # .env des Proxys aus host.conf: wer zugreifen darf, wessen X-Forwarded-For gilt
  local allow=${ALLOW:-}
  [ -n "$allow" ] || { allow="0.0.0.0/0 ::/0"; warn "ALLOW leer – alle Netze dürfen zugreifen (host.conf: ALLOW=\"<Front-Proxy> <Management-Netz>\")"; }
  {
    printf 'ALLOW="%s"\n' "$allow"
    # Front-Proxy: seine Angabe X-Forwarded-For (Adresse des Benutzers) wird übernommen
    [ -z "${FRONT:-}" ] || printf 'FRONT="%s"\n' "$FRONT"
    printf 'NBH_PORT=%s\n' "${PORT:-443}"
  } >"$NBH_DIR/proxy/.env"
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
  instanz_env "$k"
  instanz_override "$k"
  ok "Secrets in $nb/.env (nur root), Netz netbox-front als nb-$k"
  site_netbox "$k"
  ok "Proxy-Eintrag https://$(nb_host "$k") (für Kunden über den Front-Proxy)"
  nbc "$k" pull -q >/dev/null 2>&1 || warn "Images nicht geladen (Netz?) – docker compose pull in $nb"
  nbc "$k" up -d netbox >/dev/null || abbruch "Start fehlgeschlagen: cd $nb && docker compose logs netbox"
  warte_netbox "$k" || abbruch "NetBox für $k antwortet nicht – cd $nb && docker compose logs netbox"
  nbc "$k" up -d >/dev/null 2>&1 || warn "Hintergrunddienste (worker) nicht gestartet – cd $nb && docker compose up -d"
  admin_anlegen "$k"
  sso_rechte "$k"
  proxy_reload
  info "Lese- und Sync-Token für Automato: netbox-host.sh token $k"
}

# --- Anmeldung über das Anmelde-Tor (1.4.0, ADR 0051) -------------------------------------------
sso_datei() {  # NetBox-Einstellungen für die Anmeldung über Kopfzeilen (im Konfig-Ordner der Instanz)
  local k=$1 ziel
  ziel="$(nbdir "$k")/configuration/zz_automato_sso.py"
  [ -d "$(dirname "$ziel")" ] || return 0
  if [ "$SSO" != an ]; then
    rm -f "$ziel"
    return 0
  fi
  cat >"$ziel" <<'EOF'
# Anmeldung über das Anmelde-Tor des Front-Proxys (Automato 1.4.0, ADR 0051) – geschrieben von
# netbox-host.sh, nicht von Hand ändern. Die Kopfzeilen setzt nur der Front-Proxy; der Proxy des
# NetBox-Hosts entfernt sie bei allen anderen Absendern. Lokale Konten (admin, Token-Benutzer)
# melden sich weiter mit Passwort bzw. Token an.
REMOTE_AUTH_ENABLED = True
REMOTE_AUTH_BACKEND = "netbox.authentication.RemoteUserBackend"
REMOTE_AUTH_HEADER = "HTTP_X_AUTOMATO_USER"  # <benutzer>@<realm>
REMOTE_AUTH_AUTO_CREATE_USER = True
REMOTE_AUTH_GROUP_SYNC_ENABLED = True
REMOTE_AUTH_GROUP_HEADER = "HTTP_X_AUTOMATO_GROUPS"  # automato-lesen,role:<rolle>,…
REMOTE_AUTH_GROUP_SEPARATOR = ","
REMOTE_AUTH_AUTO_CREATE_GROUPS = False  # nur die Gruppen mit Rechten (netbox-host.sh sso)
REMOTE_AUTH_SUPERUSER_GROUPS = ["role:admin"]
REMOTE_AUTH_STAFF_GROUPS = ["role:admin"]
REMOTE_AUTH_DEFAULT_GROUPS = []
EOF
  chmod 644 "$ziel"
}

sso_rechte() {  # Gruppen und Rechte: automato-lesen (alle), role:operator (bearbeiten), role:admin
  local k=$1
  [ "$SSO" = an ] || return 0
  if nbc "$k" exec -T netbox /opt/netbox/netbox/manage.py shell >/dev/null 2>&1 <<'EOF'; then
from django.contrib.contenttypes.models import ContentType
from users.models import Group, ObjectPermission

typen = list(ContentType.objects.values_list("pk", flat=True))
rechte = {
    "automato-lesen": ("automato-sso-lesen", ["view"]),
    "role:operator": ("automato-sso-bearbeiten", ["view", "add", "change", "delete"]),
}
for gruppe, (name, aktionen) in rechte.items():
    g, _ = Group.objects.get_or_create(name=gruppe)
    perm, _ = ObjectPermission.objects.get_or_create(name=name, defaults={"actions": aktionen})
    perm.enabled = True
    perm.actions = aktionen
    perm.save()
    perm.object_types.set(typen)
    perm.groups.add(g)
Group.objects.get_or_create(name="role:admin")  # Superuser (REMOTE_AUTH_SUPERUSER_GROUPS)
EOF
    ok "$k: Anmeldung über den Realm – role:admin Superuser, role:operator bearbeiten, sonst lesen"
  else
    warn "$k: Gruppen für die Anmeldung nicht angelegt – später: netbox-host.sh sso $k"
  fi
}

cmd_sso() {
  root_noetig
  lade_conf
  local wer=${1:-} k liste
  [ -n "$wer" ] || abbruch "Verwendung: sso <kunde|alle>"
  [ "$SSO" = an ] || info "SSO=aus in host.conf – Anmeldung über das Tor wird entfernt"
  [ -n "$FRONT" ] || warn "FRONT leer in host.conf – ohne Front-Proxy kommt keine Anmeldung an"
  titel "Proxy (Kopfzeilen der Anmeldung nur vom Front-Proxy)"
  install -m 644 "$SCRIPT_DIR/proxy/Caddyfile" "$NBH_DIR/proxy/Caddyfile"
  proxy_env
  site_auffang
  proxy_reload
  liste=$wer
  [ "$wer" != alle ] || liste=$(kunden)
  for k in $liste; do
    kurzname "$k"
    [ -d "$(nbdir "$k")" ] || abbruch "keine NetBox für '$k'"
    titel "NetBox $k"
    sso_datei "$k"
    # shellcheck disable=SC2046 # Dienste der Instanz, die NetBox nutzen (netbox, netbox-worker, …)
    nbc "$k" restart $(nbc "$k" config --services 2>/dev/null | grep '^netbox') >/dev/null 2>&1 \
      || warn "$k: NetBox nicht neu gestartet – cd $(nbdir "$k") && docker compose restart"
    if warte_netbox "$k"; then
      sso_rechte "$k"
    else
      fehler "$k: NetBox antwortet nicht – cd $(nbdir "$k") && docker compose logs netbox"
    fi
  done
  [ "$FEHLER" -eq 0 ]
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
  sso_datei "$k"
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

site_auffang() {  # Namen ohne NetBox-Instanz: 404 mit Hinweis statt einer leeren Seite (1.10.0)
  install -d -m 755 "$NBH_DIR/proxy/sites"
  cat >"$NBH_DIR/proxy/sites/_unbekannt.caddy" <<EOF
# Namen ohne NetBox-Instanz (netbox-host.sh) – nicht von Hand ändern
*.$DOMAIN {
	import common
	respond "Keine NetBox für diesen Namen · no NetBox for this name (netbox-host.sh neu <kunde>)" 404
}
EOF
}

site_netbox() {  # Proxy-Eintrag der NetBox eines Kunden
  local k=$1
  cat >"$NBH_DIR/proxy/sites/$k-nb.caddy" <<EOF
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
perm.object_types.set(list(ContentType.objects.values_list("pk", flat=True)))  # NetBox 4: ObjectType
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

sichere_konfig() {  # host.conf, Proxy (Zertifikat, Einträge) und je Kunde .env/Override – ohne Repo-Kopie
  local ziel=$BACKUP_DIR/_konfig ts datei aus=()
  install -d -m 700 "$ziel"
  ts=$(date +%Y%m%d-%H%M%S)
  datei=$ziel/konfig-$ts.tar.gz.age
  # liegt das Repo (dieses Werkzeug) unter NBH_DIR, gehört es nicht in die Sicherung
  case "$REPO_DIR/" in "$NBH_DIR"/*) aus=(--exclude="./${REPO_DIR#"$NBH_DIR"/}") ;; esac
  if tar -C "$NBH_DIR" "${aus[@]}" -czf - . | $AGE -r "$AGE_RECIPIENT" -o "$datei.tmp"; then
    mv "$datei.tmp" "$datei"
  else
    rm -f "$datei.tmp"
    fehler "Konfiguration nicht gesichert"
    return 1
  fi
  find "$ziel" -name 'konfig-*.age' -type f -mtime +"$BACKUP_KEEP_DAYS" -delete 2>/dev/null || true
  ok "Konfiguration: $(basename "$datei") (host.conf, Zertifikat, Secrets je Kunde)"
}

cmd_sichern() {
  root_noetig
  lade_conf
  local wer=${1:-} k
  [ -n "$wer" ] || abbruch "Verwendung: sichern <kunde|alle|konfig>"
  [ -n "$AGE_RECIPIENT" ] || abbruch "AGE_RECIPIENT fehlt in host.conf – netbox-host.sh init"
  titel "Sicherung nach $BACKUP_DIR"
  if [ "$wer" = alle ]; then
    for k in $(kunden); do sichere_einen "$k" || true; done
    sichere_konfig || true
  elif [ "$wer" = konfig ]; then
    sichere_konfig || true
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
  if warte_netbox "$k"; then
    ok "$k läuft mit $NETBOX_VERSION"
    sso_rechte "$k"
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
  [ "$ja" = "--ja" ] || abbruch "löscht NetBox, Daten und Proxy-Eintrag von '$k' – bestätigen mit: loeschen $k --ja"
  titel "Löschen $k"
  if [ -d "$(nbdir "$k")" ]; then
    if [ -n "$AGE_RECIPIENT" ]; then
      sichere_einen "$k" || abbruch "letzte Sicherung fehlgeschlagen – nichts gelöscht"
    else
      abbruch "AGE_RECIPIENT fehlt – ohne letzte Sicherung wird nichts gelöscht"
    fi
    nbc "$k" down -v --remove-orphans >/dev/null 2>&1 || warn "$k: Container nicht entfernt"
  fi
  rm -rf "$(kdir "$k")"
  rm -f "$NBH_DIR/proxy/sites/$k-nb.caddy"
  ok "$k entfernt – Sicherungen bleiben in $BACKUP_DIR/$k"
  proxy_reload
}

# --- pruefen ------------------------------------------------------------------------------------
cmd_pruefen() {
  root_noetig
  lade_conf
  titel "Proxy"
  if proxyc ps --status restarting --services 2>/dev/null | grep -qx proxy; then
    fehler "Proxy startet ständig neu – (cd $NBH_DIR/proxy && docker compose logs --tail 20 proxy)"
  elif proxyc ps --status running --services 2>/dev/null | grep -qx proxy; then ok "Proxy läuft (Port $PORT)"
  else fehler "Proxy läuft nicht – netbox-host.sh init"; fi
  if [ -s "$NBH_DIR/proxy/pki/wildcard.crt" ]; then
    zert_pruefen || true
    if openssl x509 -in "$NBH_DIR/proxy/pki/wildcard.crt" -noout -checkend $((30 * 86400)) >/dev/null 2>&1; then
      ok "Zertifikat gültig bis $(openssl x509 -in "$NBH_DIR/proxy/pki/wildcard.crt" -noout -enddate | cut -d= -f2)"
    else
      warn "Zertifikat läuft in weniger als 30 Tagen ab (bzw. ist abgelaufen)"
    fi
  else
    fehler "Wildcard-Zertifikat fehlt ($NBH_DIR/proxy/pki/wildcard.crt)"
  fi
  [ -n "$ALLOW" ] && ok "Zugriff nur aus: $ALLOW" || warn "ALLOW leer – alle Netze dürfen zugreifen"
  [ -n "$FRONT" ] && ok "Front-Proxy: $FRONT (Adresse des Benutzers aus X-Forwarded-For)" \
    || info "FRONT leer – NetBox sieht als Adresse den Front-Proxy statt des Benutzers"
  if [ "$SSO" = an ]; then
    [ -n "$FRONT" ] && ok "Anmeldung über das Anmelde-Tor (Kopfzeilen nur von $FRONT)" \
      || warn "SSO an, aber FRONT leer – die Anmeldung über das Tor kommt nicht an"
    grep -q "X-Automato-User" "$NBH_DIR/proxy/Caddyfile" 2>/dev/null \
      || fehler "Proxy entfernt gefälschte Anmelde-Kopfzeilen nicht – netbox-host.sh sso alle"
  fi
  local konf
  konf=$(find "$BACKUP_DIR/_konfig" -name 'konfig-*.tar.gz.age' -type f -mmin -$((26 * 60)) 2>/dev/null | head -1 || true)
  [ -n "$konf" ] && ok "Konfiguration gesichert (jünger als 26 h)" || warn "keine Sicherung der Konfiguration der letzten 26 h (sichern konfig)"
  titel "Kunden"
  local k code neu
  for k in $(kunden); do
    if [ "$SSO" = an ] && [ ! -f "$(nbdir "$k")/configuration/zz_automato_sso.py" ]; then
      warn "$k: Anmeldung über das Tor nicht eingerichtet – netbox-host.sh sso $k"
    fi
    if nbc "$k" exec -T netbox curl -fsS -o /dev/null http://localhost:8080/login/ >/dev/null 2>&1; then
      ok "$k: NetBox läuft"
    else
      fehler "$k: NetBox antwortet im Container nicht – cd $(nbdir "$k") && docker compose logs netbox"
    fi
    code=$($CURL -sk -o /dev/null -w '%{http_code}' --resolve "$(nb_host "$k"):$PORT:127.0.0.1" "https://$(nb_host "$k"):$PORT/login/" 2>/dev/null || true)
    case "$code" in
      200) ok "$k: über den Proxy erreichbar (https://$(nb_host "$k"):$PORT)" ;;
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
  sichern) shift; cmd_sichern "$@" ;;
  wiederherstellen) shift; cmd_wiederherstellen "$@" ;;
  update) shift; cmd_update "$@" ;;
  loeschen) shift; cmd_loeschen "$@" ;;
  pruefen) cmd_pruefen ;;
  sso) shift; cmd_sso "$@" ;;
  *) sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
