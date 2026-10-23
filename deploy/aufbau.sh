#!/usr/bin/env bash
# Aufbau der Automato-VM pruefen - und jeweils pruefen, ob es geht (0.40.0, nach dem Muster
# von Magisters plattform-aufbau.sh: jede Pruefung sagt, was fehlt und wie man es behebt).
#
#   deploy/aufbau.sh pruefen              Werkzeuge, Docker, Container, Health, Verbindungen
#                                         aus dem Orchestrator-Container, ufw-Sperren
#   deploy/aufbau.sh firewall             noetige ufw-Regeln zeigen (Container -> Docker-Host)
#   deploy/aufbau.sh firewall --anwenden  ... und setzen (root), danach erneut pruefen
#   deploy/aufbau.sh status               Container und Health kurz
#   deploy/aufbau.sh netbox-start         lokale NetBox (netbox-docker) starten, Neustart-Regel
#                                         setzen und warten, bis sie antwortet (0.41.0)
#   deploy/aufbau.sh backup-schluessel    age-Schluessel fuer die Sicherung anlegen bzw. den
#                                         vorhandenen nehmen und in .env eintragen (0.42.0)
#
# Die Verbindungen prueft der Orchestrator selbst (`automato check-connections`, wie System ->
# Verbindungen im UI): Datenbank, Redis, Keycloak, jedes zentral gelesene Quellsystem - auf
# genau dem Weg, den Einlesen und NetBox-Sync nehmen. Typischer Befund: ufw sperrt den Weg vom
# Docker-Netz zu einem Dienst auf dem Host (z. B. NetBox auf :8080 ueber host.docker.internal).
# Die Regel dafuer ist schmal: nur das Netz des Containers, nur die Gateway-Adresse, nur der Port.
#
# Seit 0.41.0 auch: startet Docker beim Booten, haben die Container eine Neustart-Regel (sonst
# bleiben sie nach einem Neustart der VM aus), und - wenn es eine lokale NetBox gibt
# (source-of-truth/netbox/netbox-docker bzw. AUTOMATO_NETBOX_DIR) - laufen ihre Container und
# antwortet die Login-Seite. Befund 0.40.0: NetBox war nach einem Neustart aus, Automato nicht.
#
# Exit 0 = alles erreichbar, 1 = mindestens ein Fehler. Aendert nur `firewall --anwenden`,
# `netbox-start` und `backup-schluessel` etwas. Einstellungen auch aus .env (deploy/env.sh).
# Profile ueber AUTOMATO_COMPOSE_PROFILES (Standard "--profile core"), wie deploy/update.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
. deploy/env.sh

COMPOSE=${COMPOSE:-docker compose}
read -r -a PROFILES <<<"${AUTOMATO_COMPOSE_PROFILES:---profile core}"
HEALTH_URL=${AUTOMATO_HEALTH_URL:-http://127.0.0.1:8000/health}
NETBOX_DIR=${AUTOMATO_NETBOX_DIR:-source-of-truth/netbox/netbox-docker}
NETBOX_COMPOSE=${NETBOX_COMPOSE:-docker compose}
NETBOX_WAIT=${AUTOMATO_NETBOX_WAIT:-300} # Sekunden bis NetBox antwortet (erster Start dauert)

FEHLER=0
ok() { printf '  \033[32mOK\033[0m      %s\n' "$*"; }
warn() { printf '  \033[33mWARNUNG\033[0m %s\n' "$*"; }
fehler() { printf '  \033[31mFEHLER\033[0m  %s\n' "$*"; FEHLER=1; }
info() { printf '          %s\n' "$*"; }
titel() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# --- Voraussetzungen -------------------------------------------------------------------
werkzeuge() {
  titel "Werkzeuge"
  local w
  for w in docker curl git python3; do
    if command -v "$w" >/dev/null 2>&1; then ok "$w"; else fehler "$w fehlt"; fi
  done
  if docker compose version >/dev/null 2>&1; then ok "docker compose $(docker compose version --short 2>/dev/null)"
  else fehler "docker compose v2 fehlt (Paket docker-compose-plugin)"; fi
  if docker info >/dev/null 2>&1; then ok "Docker-Dienst antwortet (Engine $(docker version --format '{{.Server.Version}}' 2>/dev/null))"
  else fehler "Docker-Dienst antwortet nicht (systemctl start docker; als root bzw. Gruppe docker)"; return; fi
  [ -f .env ] && ok ".env vorhanden" || warn ".env fehlt – es gelten die Vorgaben aus docker-compose.yml"
  if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    if systemctl is-enabled --quiet docker 2>/dev/null; then ok "Docker startet beim Booten"
    else warn "Docker startet nicht beim Booten: sudo systemctl enable docker containerd"; fi
  fi
}

# Neustart-Regel eines Containers (no | unless-stopped | always | on-failure; ? = unbekannt)
regel_von() {
  [ -n "${1:-}" ] || { echo "?"; return; }
  docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$1" 2>/dev/null || echo "?"
}

container() {
  titel "Container"
  local zeilen
  if ! zeilen=$($COMPOSE "${PROFILES[@]}" ps -a --format '{{.Service}}|{{.State}}|{{.Health}}|{{.Name}}' 2>/dev/null); then
    fehler "docker compose ps fehlgeschlagen – im Repo-Verzeichnis aufrufen"; return
  fi
  [ -n "$zeilen" ] || { fehler "keine Container – starten: $COMPOSE ${PROFILES[*]} up -d"; return; }
  local dienst zustand gesund name ohne=""
  while IFS='|' read -r dienst zustand gesund name; do
    [ -n "$dienst" ] || continue
    if [ "$zustand" != "running" ]; then
      fehler "$dienst: $zustand – Logs: $COMPOSE logs --tail 50 $dienst"
    elif [ -n "$gesund" ] && [ "$gesund" != "healthy" ]; then
      warn "$dienst: läuft, Health $gesund"
    else
      ok "$dienst${gesund:+ ($gesund)}"
    fi
    [ "$(regel_von "$name")" = "no" ] && ohne="$ohne $dienst"
  done <<<"$zeilen"
  if [ -n "$ohne" ]; then
    warn "ohne Neustart-Regel:$ohne – nach einem Neustart der VM bleiben sie aus."
    info "docker-compose.yml setzt restart: unless-stopped (ab 0.41.0): $COMPOSE ${PROFILES[*]} up -d"
  fi
}

# --- Lokale NetBox (netbox-docker auf derselben VM) ----------------------------------------
dc_netbox() { (cd "$NETBOX_DIR" && $NETBOX_COMPOSE "$@"); }

netbox_port() {  # Host-Port aus dem Override von setup.sh ("8080:8080"), sonst 8080
  local p
  p=$(grep -oE '[0-9]+:8080' "$NETBOX_DIR/docker-compose.override.yml" 2>/dev/null | head -1 | cut -d: -f1 || true)
  echo "${p:-8080}"
}

netbox_antwortet() { curl -fsS -m 5 -o /dev/null "http://localhost:$(netbox_port)/login/" 2>/dev/null; }

netbox_stack() {
  [ -d "$NETBOX_DIR" ] || return 0  # keine lokale NetBox (z. B. eigene NetBox im Netz)
  titel "NetBox auf dieser VM ($NETBOX_DIR)"
  local zeilen
  if ! zeilen=$(dc_netbox ps -a --format '{{.Service}}|{{.State}}|{{.Health}}|{{.Name}}' 2>/dev/null); then
    fehler "docker compose ps im NetBox-Ordner fehlgeschlagen"; return
  fi
  [ -n "$zeilen" ] || { fehler "keine NetBox-Container – einrichten: source-of-truth/netbox/setup.sh"; return; }
  local dienst zustand gesund name aus=0 ohne=""
  while IFS='|' read -r dienst zustand gesund name; do
    [ -n "$dienst" ] || continue
    if [ "$zustand" != "running" ]; then
      aus=1
      case "$dienst" in
        netbox-worker | netbox-housekeeping) warn "$dienst: $zustand (Hintergrunddienst, für Automato nicht nötig)" ;;
        *) fehler "$dienst: $zustand" ;;
      esac
    elif [ -n "$gesund" ] && [ "$gesund" != "healthy" ]; then
      warn "$dienst: läuft, Health $gesund (erster Start dauert bis 5 min)"
    else
      ok "$dienst${gesund:+ ($gesund)}"
    fi
    [ "$(regel_von "$name")" = "no" ] && ohne="$ohne $dienst"
  done <<<"$zeilen"
  if netbox_antwortet; then ok "Login-Seite antwortet (localhost:$(netbox_port))"
  else fehler "NetBox antwortet auf localhost:$(netbox_port) nicht"; fi
  [ -z "$ohne" ] || warn "ohne Neustart-Regel:$ohne – nach einem Neustart der VM bleibt NetBox aus"
  if [ "$aus" -eq 1 ] || [ -n "$ohne" ]; then
    info "Starten und Neustart-Regel setzen: deploy/aufbau.sh netbox-start"
  fi
}

cmd_netbox_start() {
  [ -d "$NETBOX_DIR" ] || { fehler "kein NetBox-Ordner $NETBOX_DIR (AUTOMATO_NETBOX_DIR setzen)"; return 1; }
  titel "NetBox starten ($NETBOX_DIR)"
  if ! dc_netbox up -d; then
    fehler "docker compose up -d im NetBox-Ordner fehlgeschlagen (Meldung oben)"; return 1
  fi
  ok "Container gestartet"
  local ids
  ids=$(dc_netbox ps -aq 2>/dev/null || true)
  # wirkt sofort und bleibt bis zum Neuerstellen der Container; dauerhaft im Override (setup.sh)
  # shellcheck disable=SC2086
  if [ -n "$ids" ] && docker update --restart unless-stopped $ids >/dev/null 2>&1; then
    ok "Neustart-Regel unless-stopped gesetzt"
  else
    warn "Neustart-Regel nicht gesetzt (docker update) – von Hand: restart: unless-stopped je Dienst"
  fi
  if ! grep -q "restart:" "$NETBOX_DIR/docker-compose.override.yml" 2>/dev/null; then
    info "Dauerhaft (auch nach einem Neuerstellen): restart: unless-stopped je Dienst in"
    info "$NETBOX_DIR/docker-compose.override.yml eintragen – nur Dienste aus: (cd $NETBOX_DIR && docker compose config --services)"
  fi
  info "warte, bis NetBox antwortet (erster Start bis $((NETBOX_WAIT / 60)) min) …"
  local bis=$((SECONDS + NETBOX_WAIT))
  while :; do
    if netbox_antwortet; then ok "NetBox antwortet (localhost:$(netbox_port))"; return 0; fi
    [ "$SECONDS" -lt "$bis" ] || break
    sleep 5
  done
  fehler "NetBox antwortet nicht – Logs: (cd $NETBOX_DIR && docker compose logs --tail 50 netbox)"
  return 1
}

health() {
  titel "Orchestrator"
  local antwort
  if antwort=$(curl -fsS -m 5 "$HEALTH_URL" 2>/dev/null) && grep -q '"status":"ok"' <<<"$antwort"; then
    ok "$HEALTH_URL antwortet (Version $(cat VERSION 2>/dev/null || echo ?))"
  else
    fehler "$HEALTH_URL antwortet nicht mit ok – Logs: $COMPOSE logs --tail 50 orchestrator"
  fi
}

# --- Verbindungen aus dem Container ------------------------------------------------------
BERICHT=""
verbindungen() {
  titel "Verbindungen (aus dem Orchestrator-Container)"
  if ! BERICHT=$($COMPOSE "${PROFILES[@]}" exec -T orchestrator automato check-connections --json 2>/dev/null); then
    # Exit 1 heisst: es gibt einen Fehler - der Bericht steht trotzdem da
    [ -n "$BERICHT" ] || { fehler "Prüfung im Container nicht möglich (läuft der Orchestrator?)"; return; }
  fi
  python3 - "$BERICHT" <<'PY' || FEHLER=1
import json, sys
farben = {"ok": "\033[32mOK\033[0m     ", "info": "\033[36mHINWEIS\033[0m", "warn": "\033[33mWARNUNG\033[0m",
          "error": "\033[31mFEHLER\033[0m "}
try:
    daten = json.loads(sys.argv[1])
except ValueError:
    print("  \033[31mFEHLER\033[0m  Antwort der Prüfung nicht lesbar"); sys.exit(1)
for i in daten["items"]:
    ziel = f"  {i['target']}" if i.get("target") else ""
    print(f"  {farben.get(i['status'], i['status'])} {i['name']}{ziel}")
    if i["status"] in ("warn", "error"):
        for s in i["steps"]:
            print(f"          {'✓' if s['ok'] else '✗'} {s['step']}: {s['detail']}")
        if i.get("hint"):
            print(f"          → {i['hint']}")
    elif i["status"] == "info" and i.get("hint"):
        print(f"          {i['hint']}")
sys.exit(1 if daten["status"] == "error" else 0)
PY
}

# Regeln aus dem Bericht: je Ziel auf dem Docker-Host (from|to|port|kommentar|name)
regeln() {
  [ -n "$BERICHT" ] || return 0
  python3 - "$BERICHT" <<'PY'
import json, sys
try:
    daten = json.loads(sys.argv[1])
except ValueError:
    sys.exit(0)
for i in daten["items"]:
    u = i.get("ufw")
    if u:
        print(f"{u['from']}|{u['to']}|{u['port']}|{u.get('comment') or 'Automato'}|{i['name']}")
PY
}

ufw_aktiv() {
  command -v ufw >/dev/null 2>&1 || return 1
  [ "$(id -u)" -eq 0 ] || return 2
  ufw status 2>/dev/null | grep -q "^Status: active"
}

firewall_pruefen() {
  titel "Host-Firewall (ufw)"
  local rc=0
  ufw_aktiv || rc=$?
  case $rc in
    1) ok "ufw nicht aktiv bzw. nicht installiert"; return ;;
    2) warn "nicht als root – ufw nicht geprüft (sudo deploy/aufbau.sh pruefen)"; return ;;
  esac
  ok "ufw aktiv"
  # Sperren aus den Docker-Netzen der letzten Stunde (Kernel-Log)
  local log
  log=$( (journalctl -k --since "-1 hour" 2>/dev/null || dmesg 2>/dev/null) | grep "UFW BLOCK" | grep -E "IN=(br-|docker)" || true)
  if [ -n "$log" ]; then
    warn "ufw hat Verbindungen aus Docker-Netzen gesperrt (letzte Stunde):"
    grep -oE "SRC=[^ ]+ DST=[^ ]+ .*DPT=[0-9]+" <<<"$log" | sed -E 's/ LEN.*PROTO=([A-Z]+).*DPT=/ \1 Port /' |
      sort | uniq -c | sort -rn | head -5 | while read -r n zeile; do info "$n×  $zeile"; done || true
  fi
  local zeilen
  zeilen=$(regeln)
  if [ -z "$zeilen" ]; then
    ok "keine Freigabe nötig (kein gesperrtes Ziel auf dem Docker-Host)"
    return
  fi
  local von nach port kommentar name
  while IFS='|' read -r von nach port kommentar name; do
    warn "$name: Weg $von → $nach:$port gesperrt – Freigabe: deploy/aufbau.sh firewall --anwenden"
  done <<<"$zeilen"
}

cmd_firewall() {
  local anwenden=0
  [ "${1:-}" = "--anwenden" ] && anwenden=1
  verbindungen >/dev/null || true
  titel "ufw-Regeln für Ziele auf dem Docker-Host"
  if [ -z "$BERICHT" ]; then
    fehler "Prüfung im Orchestrator-Container nicht möglich (läuft er? $COMPOSE ${PROFILES[*]} ps)"
    return 1
  fi
  local zeilen
  zeilen=$(regeln)
  if [ -z "$zeilen" ]; then
    ok "keine nötig – alle Ziele auf dem Docker-Host antworten (bzw. es gibt keine)"
    return 0
  fi
  local von nach port kommentar name
  while IFS='|' read -r von nach port kommentar name; do
    local regel=(ufw allow proto tcp from "$von" to "$nach" port "$port" comment "$kommentar")
    # zum Kopieren mit Anfuehrungszeichen (sonst leitet `->` die Ausgabe um)
    local zeigen="ufw allow proto tcp from $von to $nach port $port comment '$kommentar'"
    if [ "$anwenden" -eq 1 ]; then
      [ "$(id -u)" -eq 0 ] || { fehler "nur als root: sudo deploy/aufbau.sh firewall --anwenden"; return 1; }
      if "${regel[@]}" >/dev/null; then ok "$zeigen"; else fehler "$zeigen"; fi
    else
      info "sudo $zeigen"
    fi
  done <<<"$zeilen"
  if [ "$anwenden" -eq 1 ]; then
    FEHLER=0
    verbindungen
  else
    info ""
    info "Setzen: sudo deploy/aufbau.sh firewall --anwenden (nur Container-Netz, Gateway und Port)"
  fi
}

# --- Sicherung (age-verschluesselt, deploy/backup) -------------------------------------
BACKUP_DIR=${AUTOMATO_BACKUP_DIR:-/var/backups/automato}
IDENTITY_STANDARD=${HOME:-/root}/automato-backup.agekey

sicherung() {
  titel "Sicherung"
  if [ "${AUTOMATO_BACKUP_PLAIN:-}" = "1" ] && [ -z "${AUTOMATO_BACKUP_AGE_RECIPIENT:-}" ]; then
    warn "Sicherung unverschlüsselt (AUTOMATO_BACKUP_PLAIN=1) – besser: deploy/aufbau.sh backup-schluessel"
  elif [ -z "${AUTOMATO_BACKUP_AGE_RECIPIENT:-}" ]; then
    fehler "kein age-Empfänger – deploy/update.sh bricht bei der Sicherung ab. Einrichten: deploy/aufbau.sh backup-schluessel"
  else
    ok "verschlüsselt für ${AUTOMATO_BACKUP_AGE_RECIPIENT:0:12}…"
    command -v age >/dev/null 2>&1 || fehler "age fehlt (apt-get install -y age)"
    if [ -n "${AUTOMATO_BACKUP_AGE_IDENTITY:-}" ] && [ -r "$AUTOMATO_BACKUP_AGE_IDENTITY" ]; then
      ok "Identität für den Rückfall lesbar ($AUTOMATO_BACKUP_AGE_IDENTITY) – zusätzlich getrennt aufbewahren"
    else
      warn "keine lesbare Identität (AUTOMATO_BACKUP_AGE_IDENTITY) – deploy/update.sh --rollback kann nicht entschlüsseln"
    fi
  fi
  local neu
  neu=$(ls -t "$BACKUP_DIR"/automato-*.dump* 2>/dev/null | head -1 || true)
  if [ -z "$neu" ]; then
    warn "noch keine Sicherung in $BACKUP_DIR (täglich per cron: deploy/backup/README.md)"
  else
    local alter=$(( ($(date +%s) - $(stat -c %Y "$neu")) / 3600 ))
    if [ "$alter" -gt 26 ]; then warn "letzte Sicherung vor ${alter} h: $(basename "$neu")"
    else ok "letzte Sicherung vor ${alter} h: $(basename "$neu")"; fi
  fi
}

cmd_backup_schluessel() {
  titel "age-Schlüssel für die Sicherung"
  command -v age-keygen >/dev/null 2>&1 || { fehler "age fehlt: apt-get install -y age"; return 1; }
  local id="${AUTOMATO_BACKUP_AGE_IDENTITY:-$IDENTITY_STANDARD}"
  if [ -f "$id" ]; then
    ok "vorhandene Identität: $id"
  else
    (umask 077 && age-keygen -o "$id" >/dev/null 2>&1) || { fehler "age-keygen -o $id fehlgeschlagen"; return 1; }
    chmod 600 "$id"
    ok "neue Identität: $id (nur für $(id -un) lesbar)"
  fi
  local empfaenger
  empfaenger=$(age-keygen -y "$id") || { fehler "$id ist keine age-Identität"; return 1; }
  setze_env AUTOMATO_BACKUP_AGE_RECIPIENT "$empfaenger"
  setze_env AUTOMATO_BACKUP_AGE_IDENTITY "$id"
  ok "in $ENV_DATEI eingetragen: AUTOMATO_BACKUP_AGE_RECIPIENT, AUTOMATO_BACKUP_AGE_IDENTITY"
  info "Die Identität $id zusätzlich getrennt von der VM aufbewahren (z. B. Passwort-Tresor) –"
  info "ohne sie ist keine Sicherung lesbar. Danach: deploy/update.sh"
}

cmd_pruefen() {
  werkzeuge
  container
  netbox_stack
  health
  verbindungen || true
  firewall_pruefen
  sicherung
  echo
  if [ "$FEHLER" -eq 0 ]; then
    printf '\033[1mAufbau in Ordnung.\033[0m\n'
  else
    printf '\033[1mEs gibt Fehler (siehe oben).\033[0m Im UI: System → Verbindungen.\n'
  fi
  return "$FEHLER"
}

cmd_status() {
  container
  netbox_stack
  health
  return "$FEHLER"
}

case "${1:-pruefen}" in
  pruefen | check) cmd_pruefen ;;
  firewall) shift; cmd_firewall "${1:-}" ;;
  status) cmd_status ;;
  netbox-start) cmd_netbox_start ;;
  backup-schluessel) cmd_backup_schluessel ;;
  -h | --help) sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) echo "Unbekannter Befehl: $1 (pruefen, firewall [--anwenden], status, netbox-start, backup-schluessel)" >&2; exit 2 ;;
esac
