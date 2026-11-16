# shellcheck shell=bash
# shellcheck disable=SC1111 # deutsche Anführungszeichen „…“ in Meldungen sind gewollt
# Eigene Automato-Instanzen je Kunde im Front-Proxy (1.16.0, ADR 0061) – eingebunden von
# deploy/front/front.sh (nutzt DOCKER, FRONT_STATE, kunden, lies_kunde, ok/info/warn/fehler,
# abbruch, proxy_laeuft, ziel_melden, titel).
#
# Mit `automato_instanz` im Soll-Stand (INSTANZ=<n> in kunden/<k>.conf) geht <kunde>-doc.<domain>
# hinter dem Tor an den Anschluss der Instanz automato-<n> (deploy/instanz/compose.yml) statt an die
# Oberfläche der Hauptinstanz (web-ui:5173): Netz automato-front-instanzen, Name
# automato-<n>-web-ui:5173. Läuft der Anschluss nicht im Netz, antwortet die Adresse 503 – nie ein
# anderes Ziel. `sync` erzeugt neu, sobald eine Instanz eines Kunden kommt oder geht (Timer).

INSTANZ_NETZ=automato-front-instanzen
INSTANZ_PORT=5173

instanz_ok() { [[ ${1:-} =~ ^[a-z][a-z0-9-]{1,30}$ ]]; }

instanz_pruefen() {  # wie das Kunden-Portal (store/portal.py INSTANZ) – Vertrauensgrenze
  instanz_ok "$1" || abbruch "Automato-Instanz '$1' ungültig: a-z, 0-9 und '-', 2–31 Zeichen, zuerst ein Buchstabe"
}

instanzen_da() {  # Instanzen, deren Anschluss im Netz des Proxys läuft (je Zeile ein Name, sortiert)
  local projekt
  while IFS= read -r projekt; do
    [[ "$projekt" == automato-* ]] || continue
    if instanz_ok "${projekt#automato-}"; then printf '%s\n' "${projekt#automato-}"; fi
  done < <($DOCKER ps --filter "network=$INSTANZ_NETZ" --filter label=com.docker.compose.service=anschluss \
    --format '{{.Label "com.docker.compose.project"}}' 2>/dev/null | sort -u || true)
  return 0
}

instanz_da() {  # $1 Instanz: läuft ihr Anschluss? (einmal je Aufruf von front.sh gefragt)
  if [ -z "${INSTANZEN_DA+x}" ]; then INSTANZEN_DA=" $(instanzen_da | tr '\n' ' ')"; fi
  [[ "$INSTANZEN_DA" == *" $1 "* ]]
}

# shellcheck disable=SC2034 # INSTANZ_ZIEL liest front.sh (schreibe_kunde)
instanz_ziel() {  # $1 Instanz: setzt INSTANZ_ZIEL, das Ziel von <kunde>-doc in route (Caddy) –
  # der Anschluss der Instanz bzw. 503, wenn er nicht läuft (nie ein anderes Ziel)
  if instanz_da "$1"; then
    INSTANZ_ZIEL=$'\t\treverse_proxy automato-'"$1-web-ui:$INSTANZ_PORT"
  else
    INSTANZ_ZIEL=$'\t\trespond "Automato-Instanz nicht erreichbar · instance not available" 503'
  fi
}

instanzen_gewollt() {  # Instanzen der Kunden mit Dokumentation (je Zeile, sortiert)
  local k
  for k in $(kunden); do
    lies_kunde "$k"
    if [ -n "$INSTANZ" ] && [ "$DOC" = ja ]; then printf '%s\n' "$INSTANZ"; fi
  done | sort -u
  return 0
}

instanzen_geaendert() {  # 0 = eine Instanz eines Kunden kam bzw. ging seit dem letzten Abgleich
  local gewollt jetzt vorher
  gewollt=$(instanzen_gewollt)
  [ -n "$gewollt" ] || return 1
  jetzt=$(comm -12 <(printf '%s\n' "$gewollt") <(instanzen_da))
  vorher=$(comm -12 <(printf '%s\n' "$gewollt") <(python3 -I -c 'import json, sys
try:
    ist = json.load(open(sys.argv[1], encoding="utf-8")).get("ist") or {}
except (OSError, ValueError, AttributeError):
    ist = {}
werte = ist.get("instanzen") if isinstance(ist, dict) else None
for n in sorted({v for v in werte if isinstance(v, str)} if isinstance(werte, list) else ()):
    print(n)' "$FRONT_STATE/status.json" 2>/dev/null || true))
  [ "$jetzt" != "$vorher" ]
}

pruefe_instanzen() {  # front.sh pruefen: nur, wenn ein Kunde eine eigene Instanz hat bzw. eine läuft
  local gewollt da k n
  gewollt=$(instanzen_gewollt)
  da=$(instanzen_da)
  [ -n "$gewollt$da" ] || return 0
  titel "Eigene Automato-Instanzen (ADR 0061)"
  if $DOCKER network inspect "$INSTANZ_NETZ" >/dev/null 2>&1; then
    ok "Netz $INSTANZ_NETZ vorhanden"
  else
    fehler "Netz $INSTANZ_NETZ fehlt – Proxy mit ≥ 1.16.0 neu starten: docker compose --profile core --profile proxy up -d proxy"
  fi
  for k in $(kunden); do
    lies_kunde "$k"
    if [ -z "$INSTANZ" ] || [ "$DOC" != ja ]; then continue; fi
    if instanz_da "$INSTANZ"; then
      ok "$k: https://$k-doc.$DOMAIN → Instanz automato-$INSTANZ"
    else
      fehler "$k: Instanz automato-$INSTANZ läuft nicht im Netz $INSTANZ_NETZ – https://$k-doc.$DOMAIN antwortet 503 (auf der VM: <basisordner>/$INSTANZ/deploy/betrieb.sh stand)"
    fi
  done
  # Altlasten (ADR 0059): nur melden – entfernt wird eine Instanz nur auf Auftrag aus dem Cockpit
  while IFS= read -r n; do
    [ -z "$n" ] || warn "Instanz automato-$n läuft, aber kein Kunde nutzt sie – bleibt (Abbau im Cockpit)"
  done < <(comm -13 <(printf '%s\n' "$gewollt") <(printf '%s\n' "$da"))
  if proxy_laeuft && [ -n "$gewollt" ]; then
    while IFS= read -r n; do
      if instanz_da "$n"; then
        ziel_melden "Automato-Instanz $n" "http://automato-$n-web-ui:$INSTANZ_PORT/api/health"
      fi
    done <<<"$gewollt"
  fi
  return 0
}
