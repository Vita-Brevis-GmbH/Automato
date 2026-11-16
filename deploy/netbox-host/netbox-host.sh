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
#   netbox-host.sh token <kunde>                Lese- und Sync-Benutzer mit Tokens für Automato (Notweg)
#   netbox-host.sh sichern <kunde|alle|konfig>  Datenbank und Medien (alle: auch die Konfiguration) mit age
#   netbox-host.sh wiederherstellen <kunde> <datei.sql.gz.age>   Sicherung einspielen
#   netbox-host.sh update <kunde|alle>          sichern, Version aus host.conf holen, starten, prüfen
#   netbox-host.sh loeschen <kunde> --ja        letzte Sicherung, dann Container, Daten und Proxy-Eintrag weg
#   netbox-host.sh pruefen                      Proxy, Zertifikat, Instanzen, Sicherungen
#   netbox-host.sh sso <kunde|alle>             Anmeldung über das Anmelde-Tor einrichten (1.4.0)
#   netbox-host.sh agent <automato-adresse>     Agent einrichten: NetBox je Kunde aus Automato (1.11.0)
#   netbox-host.sh sync                         einmal abgleichen (das tut der Agent alle 5 Minuten)
#
# Seit 1.11.0 (ADR 0054) legt der Agent die NetBox je Kunde selbst an: er holt aus Automato die
# Kunden mit dem Werkzeug NetBox (GET /api/netbox-host, eigenes Token) und ruft für jeden ohne
# Instanz `neu` auf. Entfernt wird nie selbst – eine NetBox ohne Freigabe meldet er nur.
#
# Seit 1.13.0 (ADR 0056) ist eine NetBox danach ohne Handgriffe fertig: der Agent richtet die
# Anmeldung über das Tor ein, wo sie fehlt (SSO=an; Neustart nur bei neuen Einstellungen), und
# übergibt Automato den Zugang (Lese- und Sync-Token) mit PUT /api/netbox-host/zugang. Tokens
# erzeugt er nur, wenn Automato sie annimmt (`zugang` je Kunde = fehlt), hält sie nur im Speicher
# (an docker über die Umgebung, an curl über stdin) und gibt sie nie aus. Vorhandene Zugänge bleiben.
#
# Seit 1.4.0 (ADR 0051) meldet sich NetBox über das Anmelde-Tor des Front-Proxys an (SSO=an in
# host.conf): der Front-Proxy setzt X-Automato-User (<benutzer>@<realm>) und X-Automato-Groups
# (Realm-Rollen als role:<rolle>), dieser Proxy nimmt sie nur von FRONT an. Rechte: role:admin =
# Superuser, role:operator = bearbeiten, alle anderen lesen; lokale Konten (admin, Tokens) bleiben.
#
# Kurzname des Kunden: Kleinbuchstaben, Ziffern und Bindestrich (DNS-Label), z. B. "kunde-a"; nicht
# auf -nb, -doc, -um oder -cmdb endend (sonst fiele er mit einem Werkzeug eines anderen Kunden zusammen).
# Einstellungen: $NBH_DIR/host.conf (Vorlage beim ersten init). Für Tests ersetzbar: NBH_DIR,
# DOCKER, GIT, AGE, AGE_KEYGEN, CURL, SYSTEMCTL, FLOCK, NBH_SYSTEMD_DIR, NBH_SKIP_ROOT=1,
# NBH_WAIT=<Sekunden>.
set -euo pipefail

NBH_DIR=${NBH_DIR:-/opt/netbox-host}
DOCKER=${DOCKER:-docker}
GIT=${GIT:-git}
AGE=${AGE:-age}
AGE_KEYGEN=${AGE_KEYGEN:-age-keygen}
CURL=${CURL:-curl}
SYSTEMCTL=${SYSTEMCTL:-systemctl}
FLOCK=${FLOCK:-flock}
NBH_SYSTEMD_DIR=${NBH_SYSTEMD_DIR:-/etc/systemd/system}
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
  # Agent (1.11.0): Adresse der Oberfläche von Automato und ggf. die CA ihres Zertifikats
  AUTOMATO_URL=${AUTOMATO_URL:-}
  AUTOMATO_CA=${AUTOMATO_CA:-}
}

root_noetig() {
  [ "${NBH_SKIP_ROOT:-0}" = "1" ] || [ "$(id -u)" -eq 0 ] || abbruch "als root bzw. mit sudo ausführen"
}

kurzname() {  # prüft den Kurznamen des Kunden (DNS-Label, Platz für "-cmdb")
  local k=${1:-}
  [ -n "$k" ] || abbruch "Kurzname des Kunden fehlt"
  [[ "$k" =~ ^[a-z0-9]([a-z0-9-]{0,40}[a-z0-9])?$ ]] \
    || abbruch "Kurzname '$k' ungültig: nur a-z, 0-9 und '-', nicht am Anfang/Ende, höchstens 42 Zeichen"
  case "$k" in start | www | proxy | alle | konfig | mgmt | konsole | connect) abbruch "Kurzname '$k' ist reserviert" ;; esac
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
  sso_rechte "$k" || true
  proxy_reload
  if [ -n "${NBH_ADMIN_DATEI:-}" ]; then  # Agent (1.13.0): den Zugang übergibt er gleich danach
    info "Zugang für Automato: übergibt der Agent, sobald der Kunde in Automato einen Mandanten hat"
  else
    info "Lese- und Sync-Token für Automato: der Agent (netbox-host.sh agent) bzw. von Hand netbox-host.sh token $k"
  fi
}

# --- Anmeldung über das Anmelde-Tor (1.4.0, ADR 0051) -------------------------------------------
# Stand je Instanz für den Agenten (1.13.0): `neustart` = neue Einstellungen, NetBox noch nicht
# neu gestartet; `ok` = Gruppen und Rechte angelegt. Ohne Datei (vor 1.13.0 eingerichtet): der
# Agent legt nur die Rechte noch einmal an (idempotent), ohne Neustart.
sso_stand() { printf '%s/sso-stand' "$(kdir "$1")"; }

sso_datei() {  # NetBox-Einstellungen nach SSO in host.conf schreiben bzw. entfernen; 0 = geändert
  local k=$1 ziel
  ziel="$(nbdir "$k")/configuration/zz_automato_sso.py"
  [ -d "$(dirname "$ziel")" ] || return 1
  if [ "$SSO" != an ]; then
    [ -e "$ziel" ] || return 1
    rm -f "$ziel" "$(sso_stand "$k")"
    return 0
  fi
  [ -f "$ziel" ] && [ "$(cat "$ziel")" = "$(sso_inhalt)" ] && return 1
  sso_inhalt >"$ziel"
  chmod 644 "$ziel"
  printf 'neustart\n' >"$(sso_stand "$k")"  # wirkt erst mit dem (Neu-)Start der NetBox
  return 0
}

sso_inhalt() {  # NetBox-Einstellungen für die Anmeldung über Kopfzeilen (im Konfig-Ordner der Instanz)
  cat <<'EOF'
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
    printf 'ok\n' >"$(sso_stand "$k")"
    ok "$k: Anmeldung über den Realm – role:admin Superuser, role:operator bearbeiten, sonst lesen"
  else
    warn "$k: Gruppen für die Anmeldung nicht angelegt – später: netbox-host.sh sso $k (bzw. der Agent)"
    return 1
  fi
}

sso_nachfuehren() {  # Agent (1.13.0, ADR 0056): Anmeldung über das Tor, wo sie fehlt
  local k=$1 stand
  [ "$SSO" = an ] || return 0  # SSO=aus entfernt nur der Befehl sso <kunde|alle>
  [ -d "$(nbdir "$k")/configuration" ] || return 0
  # ohne das Entfernen gefälschter Kopfzeilen im Proxy wäre die Anmeldung über Kopfzeilen offen
  if ! grep -q "X-Automato-User" "$NBH_DIR/proxy/Caddyfile" 2>/dev/null; then
    warn "$k: Anmeldung über das Tor nicht eingerichtet – der Proxy entfernt gefälschte Anmelde-Kopfzeilen nicht (netbox-host.sh init)"
    return 1
  fi
  if sso_datei "$k"; then
    info "$k: Anmeldung über das Tor wird eingerichtet (NetBox startet neu)"
  fi
  stand=$(cat "$(sso_stand "$k")" 2>/dev/null || true)
  [ "$stand" != ok ] || return 0
  if [ "$stand" = neustart ]; then
    # shellcheck disable=SC2046 # Dienste der Instanz, die NetBox nutzen (netbox, netbox-worker, …)
    if ! nbc "$k" restart $(nbc "$k" config --services 2>/dev/null | grep '^netbox') </dev/null >/dev/null 2>&1; then
      warn "$k: NetBox nicht neu gestartet – der nächste Abgleich versucht es wieder"
      return 1
    fi
    if ! warte_netbox "$k" </dev/null; then
      fehler "$k: NetBox antwortet nach dem Neustart nicht – cd $(nbdir "$k") && docker compose logs netbox"
      return 1
    fi
    printf 'rechte\n' >"$(sso_stand "$k")"
    SSO_NEU="$SSO_NEU $k"
  fi
  sso_rechte "$k" </dev/null
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
    sso_datei "$k" || true
    # shellcheck disable=SC2046 # Dienste der Instanz, die NetBox nutzen (netbox, netbox-worker, …)
    nbc "$k" restart $(nbc "$k" config --services 2>/dev/null | grep '^netbox') >/dev/null 2>&1 \
      || warn "$k: NetBox nicht neu gestartet – cd $(nbdir "$k") && docker compose restart"
    if warte_netbox "$k"; then
      sso_rechte "$k" || true
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
  sso_datei "$k" || true
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
    if [ -n "${NBH_ADMIN_DATEI:-}" ]; then  # Agent (1.11.0): niemand liest mit – Datei nur für root
      (umask 077 && printf '%s\n' "$pass" >"$NBH_ADMIN_DATEI")
      ok "Admin 'admin' angelegt – Passwort in $NBH_ADMIN_DATEI (nur root): in den Tresor, dann löschen"
    else
      ok "Admin 'admin' angelegt – Passwort (nur jetzt sichtbar, in den Tresor, beim ersten Login ändern):"
      printf '\n          %s\n\n' "$pass"
    fi
  else
    warn "Admin nicht angelegt – später: cd $(nbdir "$k") && docker compose exec netbox /opt/netbox/netbox/manage.py createsuperuser"
  fi
}

# --- token --------------------------------------------------------------------------------------
zugang_benutzer() {  # <kunde> <lese-token> <sync-token>: Benutzer für Automato, alte Tokens ersetzt
  # Die Tokens gehen nur über die Umgebung an docker compose (`-e NAME` ohne Wert, wie
  # sync-user.sh) – nie in eine Befehlszeile (ps) und nie in eine Ausgabe. Rückgabe 1 = Lese-,
  # 2 = Sync-Benutzer nicht angelegt.
  local k=$1
  NBH_LESE_TOKEN=$2 nbc "$k" exec -T -e NBH_LESE_TOKEN netbox /opt/netbox/netbox/manage.py shell >/dev/null <<'PY' || return 1
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
Token(user=u, version=1, token=os.environ["NBH_LESE_TOKEN"], write_enabled=False).save()
PY
  NETBOX_DIR="$(nbdir "$k")" SYNC_TOKEN=$3 DOCKER="$DOCKER" \
    bash "$REPO_DIR/source-of-truth/netbox/sync-user.sh" </dev/null >/dev/null || return 2
}

cmd_token() {  # Notweg (seit 1.13.0 übergibt der Agent den Zugang selbst, ADR 0056)
  root_noetig
  lade_conf
  local k=${1:-}
  kurzname "$k"
  [ -d "$(nbdir "$k")" ] || abbruch "keine NetBox für '$k'"
  titel "Benutzer für Automato in der NetBox von $k"
  local lese sync rc=0
  lese=$(openssl rand -hex 20)
  sync=$(openssl rand -hex 20)
  zugang_benutzer "$k" "$lese" "$sync" || rc=$?
  [ "$rc" -ne 1 ] || abbruch "Lese-Benutzer nicht angelegt"
  ok "automato-collector (nur lesen)"
  [ "$rc" -eq 0 ] || abbruch "Sync-Benutzer nicht angelegt"
  ok "automato-sync (schreibt nur die Objekttypen des Syncs, ADR 0010)"
  cat <<EOF

In Automato: Mandant des Kunden → Quellsysteme → + Quellsystem „NetBox“
  Adresse:        https://$(nb_host "$k")
  Lese-Token:     $lese
  Schreib-Token:  $sync      (nur für den NetBox-Sync)
Die Tokens nur dort eintragen bzw. in den Tresor – sie werden nicht gespeichert und nicht
wieder angezeigt; erneut aufrufen ersetzt sie (auch die, die der Agent Automato übergeben hat:
dort von Hand nachführen).
EOF
}

# --- Agent: NetBox je Kunde aus Automato (1.11.0, ADR 0054) -------------------------------------
token_datei() { printf '%s/automato.token' "$NBH_DIR"; }
status_datei() { printf '%s/sync-status.json' "$NBH_DIR"; }

setze_conf() {  # KEY=wert in host.conf setzen bzw. anhängen (Wert ohne Anführungszeichen/Leerzeichen)
  local key=$1 wert=$2 f=$NBH_DIR/host.conf
  if grep -q "^$key=" "$f"; then
    sed -i "s|^$key=.*|$key=$wert|" "$f"
  else
    printf '\n%s=%s\n' "$key" "$wert" >>"$f"
  fi
}

cmd_agent() {
  root_noetig
  lade_conf
  local url=${1:-} token=${NBH_TOKEN:-}
  url=${url%/}
  [[ "$url" =~ ^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$ ]] \
    || abbruch "Verwendung: agent https://<adresse der Oberfläche von Automato> (ohne Pfad)"
  if [ -z "$token" ]; then
    [ -t 0 ] || abbruch "Token fehlt (NBH_TOKEN=… bzw. im Terminal aufrufen)"
    printf 'Token aus "deploy/aufbau.sh netbox-host-token" auf der Automato-VM (Eingabe unsichtbar): '
    IFS= read -rs token
    echo
  fi
  [[ "$token" =~ ^[A-Za-z0-9._~+/=-]{16,200}$ ]] || abbruch "Token ungültig (16–200 Zeichen, ohne Leerzeichen)"
  (umask 077 && printf '%s\n' "$token" >"$(token_datei)")
  setze_conf AUTOMATO_URL "$url"
  ok "Automato: $url, Token in $(token_datei) (nur root)"
  [ -z "$AUTOMATO_CA" ] || [ -s "$AUTOMATO_CA" ] || warn "AUTOMATO_CA=$AUTOMATO_CA fehlt oder ist leer"
  local skript="$SCRIPT_DIR/netbox-host.sh"
  install -d -m 755 "$NBH_SYSTEMD_DIR"
  printf '%s\n' \
    '[Unit]' \
    'Description=NetBox-Host: NetBox je Kunde aus Automato anlegen (ADR 0054)' \
    'After=docker.service network-online.target' \
    '' \
    '[Service]' \
    'Type=oneshot' \
    "ExecStart=$skript sync" >"$NBH_SYSTEMD_DIR/netbox-host-sync.service"
  printf '%s\n' \
    '[Unit]' \
    'Description=NetBox-Host: Kunden aus Automato alle 5 Minuten abgleichen' \
    '' \
    '[Timer]' \
    'OnBootSec=2min' \
    'OnUnitActiveSec=5min' \
    'Unit=netbox-host-sync.service' \
    '' \
    '[Install]' \
    'WantedBy=timers.target' >"$NBH_SYSTEMD_DIR/netbox-host-sync.timer"
  $SYSTEMCTL daemon-reload
  $SYSTEMCTL enable --now netbox-host-sync.timer
  $SYSTEMCTL start --no-block netbox-host-sync.service || true
  ok "Agent eingerichtet: alle 5 Minuten netbox-host.sh sync (systemd)"
  info "Log: journalctl -u netbox-host-sync -f; Stand: netbox-host.sh pruefen"
}

# Antwort von Automato prüfen: je Kunde "kurzname<TAB>name<TAB>zugang"; erste Zeile "domain<TAB><domain>".
# `zugang` (1.13.0, ADR 0056) ist optional – fehlt er (Automato älter), übergibt der Agent nichts.
NBH_KUNDEN_PY=$(cat <<'PY'
import json, re, sys
daten = json.load(sys.stdin)
if not isinstance(daten, dict) or daten.get("vertrag") != 1 or not isinstance(daten.get("kunden"), list):
    sys.exit("Antwort von Automato unbekannt (vertrag 1 erwartet)")
domain = daten.get("domain") if isinstance(daten.get("domain"), str) else ""
print("domain\t" + (domain if re.fullmatch(r"[a-z0-9.-]{1,253}", domain) else ""))
STAENDE = ("fehlt", "vorhanden", "kein_mandant", "andere_adresse", "keine_adresse")
for k in daten["kunden"][:1000]:
    if not isinstance(k, dict):
        continue
    kurz, name, zugang = k.get("kurzname"), k.get("name"), k.get("zugang")
    if not isinstance(kurz, str) or not re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,40}[a-z0-9])?", kurz):
        continue
    if not isinstance(name, str) or not name.strip() or not name.isprintable() or len(name) > 200:
        name = kurz
    zugang = zugang if zugang in STAENDE else "-"
    print(kurz + "\t" + name.replace("\t", " ").strip() + "\t" + zugang)
PY
)

# Antwort auf PUT /api/netbox-host/zugang: nur der Stand (nie der Inhalt ausgeben)
NBH_ZUGANG_PY=$(cat <<'PY'
import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    d = {}
s = d.get("status") if isinstance(d, dict) else None
ok = ("uebernommen", "unveraendert", "vorhanden", "kein_mandant", "andere_adresse", "keine_adresse")
print(s if s in ok else "")
PY
)

# Stand für pruefen: Zeit, ok, angelegt, fehlgeschlagen, ohne Freigabe, Meldung; seit 1.13.0 auch
# Zugang übergeben, ohne Mandant (folgt), von Hand klären und Anmeldung eingerichtet – nur
# Kurznamen, nie Tokens
NBH_STATUS_PY=$(cat <<'PY'
import json, sys
from datetime import datetime, timezone
ok, angelegt, fehl, verwaist, kunden, meldung, zugang, offen, klaeren, sso = (sys.argv[1:] + [""] * 10)[:10]
print(json.dumps({
    "zeit": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "ok": ok == "ja",
    "kunden": int(kunden or 0),
    "angelegt": angelegt.split(),
    "fehlgeschlagen": fehl.split(),
    "ohne_freigabe": verwaist.split(),
    "meldung": meldung or None,
    "zugang_uebergeben": zugang.split(),
    "zugang_offen": offen.split(),
    "zugang_klaeren": klaeren.split(),
    "sso_eingerichtet": sso.split(),
}, ensure_ascii=False))
PY
)

sync_status() {  # $1 ok ja|nein, $2 angelegt, $3 fehlgeschlagen, $4 ohne Freigabe, $5 Anzahl, $6 Meldung
  local tmp
  tmp=$(mktemp "$NBH_DIR/.sync-status-XXXXXX")
  python3 -I -c "$NBH_STATUS_PY" "$@" "$ZUGANG_NEU" "$ZUGANG_OFFEN" "$ZUGANG_KLAEREN" "$SSO_NEU" >"$tmp" \
    && chmod 600 "$tmp" && mv -f "$tmp" "$(status_datei)" || rm -f "$tmp"
}

# Stand eines Abgleichs (Kurznamen mit Leerzeichen getrennt)
ZUGANG_NEU=""
ZUGANG_OFFEN=""
ZUGANG_KLAEREN=""
ZUGANG_FEHL=""
SSO_NEU=""
NBH_KOPF=""
NBH_CA=()

zugang_meldung() {  # <kunde> <stand>: Zugang nicht übergeben – warum
  case "$2" in
    kein_mandant)
      info "$1: in Automato noch ohne Mandant (Kunden-Portal) – den Zugang übergibt der Agent, sobald ein Mandant gewählt ist"
      ZUGANG_OFFEN="$ZUGANG_OFFEN $1" ;;
    andere_adresse)
      warn "$1: der Mandant in Automato hat ein Quellsystem NetBox mit einer anderen Adresse – nichts übergeben, von Hand klären"
      ZUGANG_KLAEREN="$ZUGANG_KLAEREN $1" ;;
    keine_adresse)
      warn "$1: Automato kennt die Domain des Front-Proxys nicht (AUTOMATO_FRONT_DOMAIN) – nichts übergeben"
      ZUGANG_KLAEREN="$ZUGANG_KLAEREN $1" ;;
  esac
}

zugang_liefern() {  # Agent (1.13.0, ADR 0056): Benutzer mit neuen Tokens anlegen und sofort übergeben
  local k=$1 lese sync antwort code status
  lese=$(openssl rand -hex 20)
  sync=$(openssl rand -hex 20)
  # Fehlermeldungen von NetBox nicht ins Protokoll – sie könnten Werte nennen
  if ! zugang_benutzer "$k" "$lese" "$sync" 2>/dev/null; then
    lese="" sync=""
    ZUGANG_FEHL="$ZUGANG_FEHL $k"
    fehler "$k: Benutzer für Automato nicht angelegt (läuft die NetBox? cd $(nbdir "$k") && docker compose logs netbox) – der nächste Abgleich versucht es wieder"
    return 1
  fi
  antwort=$(mktemp "$NBH_DIR/.antwort-XXXXXX")
  # die Tokens nur im Inhalt der Anfrage (printf ist eingebaut, curl liest stdin) – nie in einer
  # Befehlszeile, Datei oder Ausgabe
  code=$(printf '{"kurzname":"%s","lesen":"%s","sync":"%s"}' "$k" "$lese" "$sync" \
    | $CURL -sS --noproxy '*' "${NBH_CA[@]}" --max-time 30 -X PUT -H "@$NBH_KOPF" \
      -H 'Content-Type: application/json' --data-binary @- -o "$antwort" -w '%{http_code}' \
      "$AUTOMATO_URL/api/netbox-host/zugang" 2>/dev/null) || code=${code:-000}
  lese="" sync=""
  status=$(python3 -I -c "$NBH_ZUGANG_PY" <"$antwort" 2>/dev/null || true)
  rm -f "$antwort"
  case "${code:-000}:$status" in
    200:uebernommen)
      ok "$k: Zugang für Automato übergeben (Quellsystem NetBox beim Mandanten des Kunden)"
      ZUGANG_NEU="$ZUGANG_NEU $k" ;;
    200:unveraendert) ok "$k: Zugang für Automato schon übergeben" ;;
    200:vorhanden)
      # nur, wenn in Automato seit dem Abruf eben von Hand ein Zugang dazukam
      warn "$k: Automato hat inzwischen einen anderen Zugang (bleibt) – die NetBox hat eben neue Tokens bekommen; in Automato „Verbindung prüfen“, notfalls dort die Tokens entfernen (der Agent übergibt dann neue)"
      ZUGANG_KLAEREN="$ZUGANG_KLAEREN $k" ;;
    409:kein_mandant | 409:andere_adresse | 409:keine_adresse) zugang_meldung "$k" "$status" ;;
    *)
      ZUGANG_FEHL="$ZUGANG_FEHL $k"
      fehler "$k: Zugang für Automato nicht übergeben (HTTP ${code:-000}) – der nächste Abgleich versucht es wieder"
      return 1 ;;
  esac
}

zugang_abgleichen() {  # <kunde> <stand aus GET /api/netbox-host>
  local k=$1 stand=$2
  case "$stand" in
    fehlt) zugang_liefern "$k" || true ;;
    vorhanden) ok "$k: Zugang für Automato vorhanden" ;;
    kein_mandant | andere_adresse | keine_adresse) zugang_meldung "$k" "$stand" ;;
    *) info "$k: Automato meldet den Zugang nicht (älter als 1.13.0) – Tokens von Hand: netbox-host.sh token $k" ;;
  esac
}

cmd_sync() {
  root_noetig
  lade_conf
  [ -n "$AUTOMATO_URL" ] || abbruch "Agent nicht eingerichtet – netbox-host.sh agent https://<automato>"
  [ -s "$(token_datei)" ] || abbruch "$(token_datei) fehlt – netbox-host.sh agent $AUTOMATO_URL"
  exec 9>"$NBH_DIR/.sync.lock"
  $FLOCK -n 9 || { info "Abgleich läuft schon"; return 0; }
  titel "Kunden aus Automato ($AUTOMATO_URL)"
  local antwort liste k name zugang domain="" domain_ok=1 angelegt="" fehl="" verwaist="" n=0 meldung=""
  NBH_KOPF=$(mktemp "$NBH_DIR/.kopf-XXXXXX")
  trap 'rm -f "$NBH_KOPF"' EXIT
  antwort=$(mktemp "$NBH_DIR/.antwort-XXXXXX")
  chmod 600 "$NBH_KOPF" "$antwort"
  # das Token nie in der Befehlszeile (ps): als Kopfzeile aus einer Datei nur für root
  printf 'Authorization: Bearer %s\n' "$(tr -d '\r\n' <"$(token_datei)")" >"$NBH_KOPF"
  [ -z "$AUTOMATO_CA" ] || NBH_CA=(--cacert "$AUTOMATO_CA")
  if ! $CURL -fsS --noproxy '*' "${NBH_CA[@]}" --max-time 30 -H "@$NBH_KOPF" -o "$antwort" "$AUTOMATO_URL/api/netbox-host" 2>/dev/null; then
    rm -f "$antwort"
    sync_status nein "" "" "" 0 "Automato nicht erreichbar bzw. Token abgelehnt"
    abbruch "Automato antwortet nicht bzw. lehnt ab – Adresse, Zertifikat (AUTOMATO_CA), Token (aufbau.sh netbox-host-token)"
  fi
  if ! liste=$(python3 -I -c "$NBH_KUNDEN_PY" <"$antwort" 2>&1); then
    rm -f "$antwort"
    sync_status nein "" "" "" 0 "Antwort von Automato unbekannt"
    abbruch "Antwort von Automato unbekannt: $liste"
  fi
  rm -f "$antwort"
  local gewollt=" "
  # die Liste über fd 3: docker compose exec im Abgleich liest sonst stdin (die übrigen Kunden)
  while IFS=$'\t' read -r -u 3 k name zugang; do
    [ -n "$k" ] || continue
    if [ "$k" = domain ] && [ -z "$domain" ]; then
      domain=${name:-–}
      if [ "$domain" != "–" ] && [ "$domain" != "$DOMAIN" ]; then
        domain_ok=0
        warn "Automato nennt die Domain $domain, dieser Host $DOMAIN (host.conf) – Adressen passen nicht, kein Zugang für Automato"
      fi
      continue
    fi
    n=$((n + 1))
    gewollt="$gewollt$k "
    if [ -d "$(nbdir "$k")" ]; then
      ok "$k: NetBox vorhanden"
      sso_nachfuehren "$k" || true
    else
      info "$k: NetBox fehlt – wird angelegt"
      # eigener Prozess: bricht das Anlegen ab, bleibt der Abgleich der übrigen Kunden
      if NBH_ADMIN_DATEI="$(kdir "$k")/admin-passwort" bash "$SCRIPT_DIR/netbox-host.sh" neu "$k" "$name"; then
        angelegt="$angelegt $k"
        ok "$k: angelegt"
      else
        fehl="$fehl $k"
        fehler "$k: Anlegen fehlgeschlagen (siehe oben) – der nächste Abgleich versucht es wieder"
        continue
      fi
    fi
    if [ "$domain_ok" = 1 ]; then
      zugang_abgleichen "$k" "${zugang:--}"
    fi
  done 3<<<"$liste"
  for k in $(kunden); do
    case "$gewollt" in *" $k "*) ;; *)
      verwaist="$verwaist $k"
      warn "$k: in Automato ohne NetBox freigegeben – bleibt; entfernen nur von Hand: netbox-host.sh loeschen $k --ja"
      ;;
    esac
  done
  [ "$n" -gt 0 ] || info "Automato nennt keine Kunden mit NetBox"
  if [ -n "$fehl" ] || [ "$FEHLER" -gt 0 ]; then
    [ -z "$fehl" ] || meldung="Anlegen von${fehl}"
    [ -z "$ZUGANG_FEHL" ] || meldung="${meldung:+$meldung; }Zugang für Automato nicht übergeben:${ZUGANG_FEHL}"
    sync_status nein "$angelegt" "$fehl" "$verwaist" "$n" "${meldung:-siehe Log}"
    return 1
  fi
  sync_status ja "$angelegt" "" "$verwaist" "$n" ""
  ok "Abgleich fertig ($n Kunden mit NetBox)"
}

agent_stand() {  # für pruefen
  titel "Agent (Kunden aus Automato)"
  if [ -z "$AUTOMATO_URL" ]; then
    info "nicht eingerichtet – NetBox je Kunde von Hand (neu) oder: netbox-host.sh agent https://<automato>"
    return 0
  fi
  if $SYSTEMCTL is-active --quiet netbox-host-sync.timer 2>/dev/null; then
    ok "Agent aktiv ($AUTOMATO_URL, alle 5 Minuten)"
  else
    fehler "Agent eingerichtet, Timer läuft nicht – systemctl enable --now netbox-host-sync.timer"
  fi
  local stand
  if ! stand=$(python3 -I - "$(status_datei)" 2>/dev/null <<'PY'
import json, sys
s = json.load(open(sys.argv[1], encoding="utf-8"))
teile = [("ok" if s.get("ok") else "fehler"), s.get("zeit") or "?", str(s.get("kunden", 0))]
for feld in ("fehlgeschlagen", "ohne_freigabe"):
    teile.append(" ".join(s.get(feld) or []))
teile.append(s.get("meldung") or "")
for feld in ("zugang_uebergeben", "zugang_offen", "zugang_klaeren", "sso_eingerichtet"):
    teile.append(" ".join(s.get(feld) or []))
print("\x1f".join(teile))
PY
  ); then
    warn "noch kein Abgleich – Log: journalctl -u netbox-host-sync"
    return 0
  fi
  local erfolg zeit anzahl fehl verwaist meldung zugang offen klaeren sso
  IFS=$'\x1f' read -r erfolg zeit anzahl fehl verwaist meldung zugang offen klaeren sso <<<"$stand"
  if [ "$erfolg" = ok ]; then
    ok "letzter Abgleich $zeit: $anzahl Kunden mit NetBox"
  else
    fehler "letzter Abgleich $zeit fehlgeschlagen: ${meldung:-Anlegen von $fehl} – journalctl -u netbox-host-sync"
  fi
  [ -z "$verwaist" ] || warn "ohne Freigabe in Automato: $verwaist (bleiben, bis Sie sie von Hand entfernen)"
  [ -z "$sso" ] || ok "Anmeldung über das Tor eingerichtet: $sso"
  [ -z "$zugang" ] || ok "Zugang an Automato übergeben: $zugang"
  [ -z "$offen" ] || info "in Automato noch ohne Mandant (Zugang folgt): $offen"
  [ -z "$klaeren" ] || warn "Zugang für Automato von Hand klären: $klaeren (Log: journalctl -u netbox-host-sync)"
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
    sso_rechte "$k" || true
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
      warn "$k: Anmeldung über das Tor nicht eingerichtet – richtet der Agent ein (sync), von Hand: netbox-host.sh sso $k"
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
  agent_stand
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
  agent) shift; cmd_agent "$@" ;;
  sync) cmd_sync ;;
  *) sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
