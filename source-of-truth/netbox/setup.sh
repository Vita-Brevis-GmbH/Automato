#!/usr/bin/env bash
# Lab-Setup fuer NetBox als Source of Truth (Model 2, UC1). NUR fuer Labor/Test.
#
# Nutzt das offizielle netbox-docker und pinnt die NetBox-Version bewusst auf die
# 4.6er-Linie: ab NetBox 4.7 werden API-Tokens gehasht (v2, Header
# "Bearer nbt_<key>.<token>") und koennen nicht mehr mit festem Wert angelegt
# werden - der Automato-NetBox-Adapter nutzt die klassischen Tokens
# ("Authorization: Token <key>"). 4.6.x hat diese noch.
# Legt Admin-User (UI-Login) + Schreib-Token (Seeding) und einen read-only
# Collector-Token an (Least Privilege, ADR 0005). Idempotent.
#
# Voraussetzungen: docker + docker compose v2, git, curl, openssl.
# Verwendung:
#   ./setup.sh                       # Standardwerte
#   NETBOX_PORT=8080 ./setup.sh      # anderer Port
#   NETBOX_RESET=1 ./setup.sh        # DB/Volumes zuvor loeschen (z. B. Versionswechsel)
set -euo pipefail

NETBOX_DIR="${NETBOX_DIR:-netbox-docker}"
NETBOX_PORT="${NETBOX_PORT:-8000}"
# Gepinnte NetBox-Version (klassische Tokens). Ueberschreibbar via NETBOX_VERSION.
NETBOX_VERSION="${NETBOX_VERSION:-v4.6.10-5.0.2}"
NETBOX_RESET="${NETBOX_RESET:-0}"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASS="${ADMIN_PASS:-admin1234!}"
COLLECTOR_TOKEN="${COLLECTOR_TOKEN:-$(openssl rand -hex 20)}"
ADMIN_TOKEN="${ADMIN_TOKEN:-$(openssl rand -hex 20)}"
# v1-Tokens brauchen genau 40 Zeichen; ungueltige Werte verwerfen und neu erzeugen.
[ "${#COLLECTOR_TOKEN}" -eq 40 ] || { echo "WARN: COLLECTOR_TOKEN ungueltig -> neu erzeugt"; COLLECTOR_TOKEN="$(openssl rand -hex 20)"; }
[ "${#ADMIN_TOKEN}" -eq 40 ] || { echo "WARN: ADMIN_TOKEN ungueltig -> neu erzeugt"; ADMIN_TOKEN="$(openssl rand -hex 20)"; }

for tool in docker git curl openssl; do
  command -v "$tool" >/dev/null 2>&1 || { echo "FEHLER: '$tool' nicht gefunden"; exit 1; }
done
docker compose version >/dev/null 2>&1 || { echo "FEHLER: docker compose v2 noetig"; exit 1; }

# 1. netbox-docker holen bzw. aktualisieren
if [ ! -d "$NETBOX_DIR" ]; then
  git clone -b release --depth 1 https://github.com/netbox-community/netbox-docker.git "$NETBOX_DIR"
fi
cd "$NETBOX_DIR"

# 1b. NetBox-Image-Version pinnen (compose liest VERSION aus dieser .env).
echo "VERSION=${NETBOX_VERSION}" > .env
export VERSION="${NETBOX_VERSION}"

# 1c. Optional: bestehende DB/Volumes loeschen. Noetig u. a. beim Wechsel von einer
# hoeheren NetBox-Version (z. B. 4.7) zurueck auf die gepinnte - deren Schema laesst
# sich nicht "downgraden", der Container bliebe sonst crashen.
if [ "$NETBOX_RESET" = "1" ]; then
  echo "NETBOX_RESET=1 -> loesche vorhandene NetBox-Volumes ..."
  docker compose down -v --remove-orphans 2>/dev/null || true
fi

# 2. Overrides: Host-Port + grosszuegigerer Healthcheck.
# netbox-docker liefert kein Host-Port-Mapping; und der erste Start (Migrationen +
# gunicorn) dauert auf frischer VM/DB laenger, als der Default-Healthcheck erlaubt -
# sonst gelten die abhaengigen Container (worker/housekeeping) als "unhealthy".
cat > docker-compose.override.yml <<YAML
services:
  netbox:
    ports:
      - "${NETBOX_PORT}:8080"
    healthcheck:
      start_period: 300s
      interval: 15s
      retries: 15
YAML

# 3. Stack starten - bewusst NUR den netbox-Dienst (+ seine Abhaengigkeiten
# postgres/redis). Die Worker haengen an "netbox: healthy"; wuerde man sie hier
# schon mitstarten, bricht ein langsamer Erststart mit
# "container ... is unhealthy" ab. Fuer UC1 (lesend ueber die REST-API) reicht
# der netbox-Dienst; die Worker kommen am Ende best-effort dazu.
echo "NetBox-Version: ${NETBOX_VERSION}"
docker compose pull
docker compose up -d netbox

# 4. Auf Erreichbarkeit warten (NetBox-Migrationen brauchen beim ersten Start Zeit)
echo "Warte auf NetBox unter http://localhost:${NETBOX_PORT}/ ..."
ok=""
for _ in $(seq 1 90); do
  if curl -fsS "http://localhost:${NETBOX_PORT}/login/" >/dev/null 2>&1; then ok=1; break; fi
  sleep 5
done
[ -n "$ok" ] || { echo "FEHLER: NetBox nicht rechtzeitig erreichbar (docker compose logs netbox)"; exit 1; }

# 5. User + Tokens anlegen (idempotent, via Django-Shell im Container)
COLLECTOR_TOKEN="$COLLECTOR_TOKEN" ADMIN_TOKEN="$ADMIN_TOKEN" \
ADMIN_USER="$ADMIN_USER" ADMIN_PASS="$ADMIN_PASS" \
docker compose exec -T \
  -e COLLECTOR_TOKEN -e ADMIN_TOKEN -e ADMIN_USER -e ADMIN_PASS \
  netbox /opt/netbox/netbox/manage.py shell <<'PY'
import os
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType

try:
    from users.models import ObjectPermission, Token
except ImportError:  # falls sich das App-Layout in kuenftigen Versionen aendert
    from netbox.users.models import ObjectPermission, Token  # type: ignore

User = get_user_model()


def set_v1_token(user, value, *, write_enabled):
    # Legacy-v1-Token: Klartext in der DB, Auth-Header "Token <value>" (klassisch).
    # Ab NetBox 4.6 sind neue (v2) Tokens gehasht und koennen nicht mit festem Wert
    # angelegt werden; v1 wird weiter unterstuetzt. Vorhandene Tokens des Users
    # entfernen -> fester, bekannter Wert (idempotent). value muss 40 Zeichen haben.
    Token.objects.filter(user=user).delete()
    Token(user=user, version=1, token=value, write_enabled=write_enabled).save()


# Admin fuer UI-Login + Schreib-Token fuers Seeding
admin, _ = User.objects.get_or_create(username=os.environ["ADMIN_USER"])
admin.is_superuser = admin.is_staff = True
admin.set_password(os.environ["ADMIN_PASS"])
admin.save()
set_v1_token(admin, os.environ["ADMIN_TOKEN"], write_enabled=True)

# Collector: KEIN Superuser. In NetBox braucht ein Nicht-Superuser eine
# ObjectPermission (actions=["view"]), um ueber die API lesen zu duerfen -
# Django-Permissions allein genuegen nicht. Die eigene NetBox-Group ist dafuer
# nicht noetig; wir haengen die view-Permission direkt an den User.
collector, _ = User.objects.get_or_create(username="automato-collector")
collector.is_superuser = collector.is_staff = False
collector.save()
perm, _ = ObjectPermission.objects.get_or_create(
    name="automato-readonly-view", defaults={"actions": ["view"]}
)
perm.enabled = True
perm.actions = ["view"]
perm.save()
perm.object_types.set(ContentType.objects.all())  # alle Objekttypen lesen
# Zuweisung an den User - Relationsname je nach NetBox-Version (M2M liegt auf der
# User-Seite als object_permissions, Rueckname auf der Permission als users).
try:
    collector.object_permissions.add(perm)
except AttributeError:
    perm.users.add(collector)

# Read-only Token (write_enabled=False -> kann nur lesen)
set_v1_token(collector, os.environ["COLLECTOR_TOKEN"], write_enabled=False)

print("SETUP_OK")
PY

# 6. Restliche Dienste (worker/housekeeping) best-effort nachziehen. Fuer UC1
# (lesend) nicht noetig; ein Fehler hier darf das Setup nicht scheitern lassen.
if ! docker compose up -d; then
  echo "Hinweis: worker/housekeeping noch nicht healthy - fuer UC1 (nur Lesen ueber" >&2
  echo "die REST-API) unkritisch. Spaeter erneut: 'docker compose up -d'." >&2
fi

cat <<EOF

=== NetBox-Lab bereit ===
URL:                         http://localhost:${NETBOX_PORT}/
Admin-Login (UI):            ${ADMIN_USER} / ${ADMIN_PASS}
Collector-Token (read-only): ${COLLECTOR_TOKEN}
Admin-Token (Schreiben/Seed):${ADMIN_TOKEN}

Fuer Automato in die .env (Model-2-Mandant):
  AUTOMATO_NETBOX_URL=http://localhost:${NETBOX_PORT}
  AUTOMATO_NETBOX_TOKEN=${COLLECTOR_TOKEN}

Optional Lab-Daten seeden:
  NETBOX_URL=http://localhost:${NETBOX_PORT} NETBOX_TOKEN=${ADMIN_TOKEN} python3 ../seed_lab.py
EOF
