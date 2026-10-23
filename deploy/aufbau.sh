#!/usr/bin/env bash
# Aufbau der Automato-VM pruefen - und jeweils pruefen, ob es geht (0.40.0, nach dem Muster
# von Magisters plattform-aufbau.sh: jede Pruefung sagt, was fehlt und wie man es behebt).
#
#   deploy/aufbau.sh pruefen              Werkzeuge, Docker, Container, Health, Verbindungen
#                                         aus dem Orchestrator-Container, ufw-Sperren
#   deploy/aufbau.sh firewall             noetige ufw-Regeln zeigen (Container -> Docker-Host)
#   deploy/aufbau.sh firewall --anwenden  ... und setzen (root), danach erneut pruefen
#   deploy/aufbau.sh status               Container und Health kurz
#
# Die Verbindungen prueft der Orchestrator selbst (`automato check-connections`, wie System ->
# Verbindungen im UI): Datenbank, Redis, Keycloak, jedes zentral gelesene Quellsystem - auf
# genau dem Weg, den Einlesen und NetBox-Sync nehmen. Typischer Befund: ufw sperrt den Weg vom
# Docker-Netz zu einem Dienst auf dem Host (z. B. NetBox auf :8080 ueber host.docker.internal).
# Die Regel dafuer ist schmal: nur das Netz des Containers, nur die Gateway-Adresse, nur der Port.
#
# Exit 0 = alles erreichbar, 1 = mindestens ein Fehler. Aendert nichts ausser mit --anwenden.
# Profile ueber AUTOMATO_COMPOSE_PROFILES (Standard "--profile core"), wie deploy/update.sh.
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE=${COMPOSE:-docker compose}
read -r -a PROFILES <<<"${AUTOMATO_COMPOSE_PROFILES:---profile core}"
HEALTH_URL=${AUTOMATO_HEALTH_URL:-http://127.0.0.1:8000/health}

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
}

container() {
  titel "Container"
  local zeilen
  if ! zeilen=$($COMPOSE "${PROFILES[@]}" ps -a --format '{{.Service}}|{{.State}}|{{.Health}}' 2>/dev/null); then
    fehler "docker compose ps fehlgeschlagen – im Repo-Verzeichnis aufrufen"; return
  fi
  [ -n "$zeilen" ] || { fehler "keine Container – starten: $COMPOSE ${PROFILES[*]} up -d"; return; }
  local dienst zustand gesund
  while IFS='|' read -r dienst zustand gesund; do
    [ -n "$dienst" ] || continue
    if [ "$zustand" != "running" ]; then
      fehler "$dienst: $zustand – Logs: $COMPOSE logs --tail 50 $dienst"
    elif [ -n "$gesund" ] && [ "$gesund" != "healthy" ]; then
      warn "$dienst: läuft, Health $gesund"
    else
      ok "$dienst${gesund:+ ($gesund)}"
    fi
  done <<<"$zeilen"
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

cmd_pruefen() {
  werkzeuge
  container
  health
  verbindungen || true
  firewall_pruefen
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
  health
  return "$FEHLER"
}

case "${1:-pruefen}" in
  pruefen | check) cmd_pruefen ;;
  firewall) shift; cmd_firewall "${1:-}" ;;
  status) cmd_status ;;
  -h | --help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) echo "Unbekannter Befehl: $1 (pruefen, firewall [--anwenden], status)" >&2; exit 2 ;;
esac
