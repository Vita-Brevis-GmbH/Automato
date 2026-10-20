#!/usr/bin/env bash
# Schreib-Benutzer fuer den Automato-NetBox-Sync anlegen (ADR 0010). Idempotent.
#
# Legt den Benutzer "automato-sync" an (kein Superuser) mit
#   - Lesen auf alle Objekttypen (fuer die Vorschau und Referenzen) und
#   - Anlegen/Aendern/Loeschen NUR auf den Objekttypen, die der Sync schreibt
#     (Tags, Tenants, Sites, Hersteller, Geraeterollen/-typen, Geraete, Interfaces,
#      VRFs, VLAN-Gruppen, VLANs, Prefixe, IP-Ranges, IP-Adressen)
# und einen Legacy-v1-Token mit Schreibrecht. Der Token gehoert in Automato ins
# NetBox-Quellsystem unter "Schreib-Token (optional, nur NetBox-Sync)".
# Andere Benutzer/Tokens (admin, automato-collector) bleiben unveraendert.
#
# Verwendung (auf dem NetBox-Host, NetBox laeuft bereits):
#   ./sync-user.sh                      # neuer Zufalls-Token
#   SYNC_TOKEN=<40 Zeichen> ./sync-user.sh   # festen Token setzen
set -euo pipefail

NETBOX_DIR="${NETBOX_DIR:-netbox-docker}"
SYNC_TOKEN="${SYNC_TOKEN:-$(openssl rand -hex 20)}"
[ "${#SYNC_TOKEN}" -eq 40 ] || { echo "WARN: SYNC_TOKEN ungueltig -> neu erzeugt"; SYNC_TOKEN="$(openssl rand -hex 20)"; }

cd "$(dirname "$0")/$NETBOX_DIR" 2>/dev/null || cd "$NETBOX_DIR" || {
  echo "FEHLER: $NETBOX_DIR nicht gefunden - erst ./setup.sh ausfuehren"; exit 1
}

SYNC_TOKEN="$SYNC_TOKEN" docker compose exec -T -e SYNC_TOKEN \
  netbox /opt/netbox/netbox/manage.py shell <<'PY'
import os
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType

try:
    from users.models import ObjectPermission, Token
except ImportError:  # falls sich das App-Layout in kuenftigen Versionen aendert
    from netbox.users.models import ObjectPermission, Token  # type: ignore

# Objekttypen, die der Sync schreibt (= sot_gateway.adapters.netbox_writer.WRITABLE)
WRITABLE = [
    ("extras", "tag"),
    ("tenancy", "tenant"),
    ("dcim", "site"),
    ("dcim", "manufacturer"),
    ("dcim", "devicerole"),
    ("dcim", "devicetype"),
    ("dcim", "device"),
    ("dcim", "interface"),
    ("ipam", "vrf"),
    ("ipam", "vlangroup"),
    ("ipam", "vlan"),
    ("ipam", "prefix"),
    ("ipam", "iprange"),
    ("ipam", "ipaddress"),
]

User = get_user_model()
user, _ = User.objects.get_or_create(username="automato-sync")
user.is_superuser = user.is_staff = False
user.save()


def permission(name, actions, type_ids):
    perm, _ = ObjectPermission.objects.get_or_create(name=name, defaults={"actions": actions})
    perm.enabled = True
    perm.actions = actions
    perm.save()
    perm.object_types.set(type_ids)
    try:
        user.object_permissions.add(perm)
    except AttributeError:
        perm.users.add(user)


permission(
    "automato-sync-view", ["view"], list(ContentType.objects.values_list("pk", flat=True))
)
write_ids = [ContentType.objects.get(app_label=a, model=m).pk for a, m in WRITABLE]
permission("automato-sync-write", ["view", "add", "change", "delete"], write_ids)

# Legacy-v1-Token (Header "Token <wert>"), mit Schreibrecht; alte Tokens des Users ersetzen
Token.objects.filter(user=user).delete()
Token(user=user, version=1, token=os.environ["SYNC_TOKEN"], write_enabled=True).save()
print("SYNC_USER_OK")
PY

cat <<EOF

=== NetBox-Sync-Benutzer bereit ===
Benutzer:     automato-sync (kein Superuser; schreibt nur die Sync-Objekttypen)
Sync-Token:   ${SYNC_TOKEN}

In Automato: Mandanten & Standorte -> Quellsysteme -> NetBox -> Bearbeiten ->
  "Schreib-Token (optional, nur NetBox-Sync)" = obiger Sync-Token
EOF
