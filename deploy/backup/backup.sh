#!/usr/bin/env bash
# Sicherung der Automato-VM: Datenbank (pg_dump -Fc), Schluessel der Geraete-Zugangsdaten und
# eingespielte Agent-Signatur - verschluesselt mit age (https://age-encryption.org).
#
#   AUTOMATO_BACKUP_AGE_RECIPIENT=age1... deploy/backup/backup.sh [zielverzeichnis]
#
# Umgebung: AUTOMATO_BACKUP_DIR (Standard /var/backups/automato), AUTOMATO_BACKUP_KEEP_DAYS
# (14), COMPOSE (Standard "docker compose"). Ohne age-Empfaenger bricht das Skript ab;
# unverschluesselt nur bewusst mit AUTOMATO_BACKUP_PLAIN=1 (Dev). Der Schluessel liegt in einer
# eigenen Datei: ohne ihn sind die gespeicherten Zugangsdaten nach einer Wiederherstellung
# unlesbar - beide zusammen aufbewahren, den age-Identitaetsschluessel getrennt davon.
set -euo pipefail
cd "$(dirname "$0")/../.."
. deploy/env.sh # AUTOMATO_BACKUP_* auch aus .env (0.42.0)

DEST=${1:-${AUTOMATO_BACKUP_DIR:-/var/backups/automato}}
RECIPIENT=${AUTOMATO_BACKUP_AGE_RECIPIENT:-}
KEEP_DAYS=${AUTOMATO_BACKUP_KEEP_DAYS:-14}
COMPOSE=${COMPOSE:-docker compose}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)

if [ -z "$RECIPIENT" ] && [ "${AUTOMATO_BACKUP_PLAIN:-}" != "1" ]; then
  echo "FEHLER: AUTOMATO_BACKUP_AGE_RECIPIENT fehlt (age-Empfaenger, 'age-keygen -y')." >&2
  echo "        Einmal einrichten: deploy/aufbau.sh backup-schluessel (schreibt ihn in .env)." >&2
  echo "        Unverschluesselt nur bewusst mit AUTOMATO_BACKUP_PLAIN=1." >&2
  exit 2
fi
umask 077
mkdir -p "$DEST"
chmod 700 "$DEST"

SUFFIX=""
[ -n "$RECIPIENT" ] && SUFFIX=".age"
written=()
cleanup() { # bei Abbruch keine halben Dateien liegen lassen
  if [ "${ok:-0}" != 1 ]; then rm -f "${written[@]}"; fi
}
trap cleanup EXIT

store() { # stdin -> <ziel>[.age]
  local out="$1$SUFFIX"
  written+=("$out")
  if [ -n "$RECIPIENT" ]; then age -r "$RECIPIENT" -o "$out"; else cat >"$out"; fi
}

DUMP="$DEST/automato-$STAMP.dump"
$COMPOSE exec -T postgres pg_dump -U automato -d automato -Fc | store "$DUMP"
echo "Datenbank: $DUMP$SUFFIX"

if $COMPOSE exec -T orchestrator test -f /var/lib/automato/credentials.key; then
  $COMPOSE exec -T orchestrator cat /var/lib/automato/credentials.key | store "$DEST/credentials-$STAMP.key"
  echo "Schluessel der Zugangsdaten: $DEST/credentials-$STAMP.key$SUFFIX"
else
  echo "Hinweis: keine Schluesseldatei (AUTOMATO_CREDENTIALS_KEY aus Konfiguration/Secret-Provider?)"
fi
if $COMPOSE exec -T orchestrator test -f /var/lib/automato/agent-package.sig; then
  $COMPOSE exec -T orchestrator cat /var/lib/automato/agent-package.sig | store "$DEST/agent-package-$STAMP.sig"
fi
ok=1

SIZE=$(stat -c %s "$DUMP$SUFFIX")
ENC=$([ -n "$RECIPIENT" ] && echo true || echo false)
printf '{"at":"%s","file":"%s","encrypted":%s,"size":%s}\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(basename "$DUMP$SUFFIX")" "$ENC" "$SIZE" |
  $COMPOSE exec -T orchestrator sh -c 'cat > /var/lib/automato/last-backup.json'

find "$DEST" -maxdepth 1 -type f \( -name 'automato-*.dump*' -o -name 'credentials-*.key*' \
  -o -name 'agent-package-*.sig*' \) -mtime +"$KEEP_DAYS" -print -delete | sed 's/^/entfernt (alt): /'
echo "Sicherung fertig ($SIZE Bytes)."
