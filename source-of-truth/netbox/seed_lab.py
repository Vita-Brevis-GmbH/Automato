#!/usr/bin/env python3
"""Minimale Lab-Daten in NetBox fuer den Model-2-Durchstich (UC1). NUR fuer Labor/Test.

Legt Zonen-Tags, eine Site, Prefixe (mit Zone-Tag am Subnetz - so wie ADR 0004
die Zone verortet) und ein paar IP-Adressen an, damit das Adress-Matching des
FortiGate-Collectors etwas zu treffen hat. Idempotent.

Voraussetzung: pip install requests
Verwendung:
  NETBOX_URL=http://localhost:8000 NETBOX_TOKEN=<schreib-token> python3 seed_lab.py
"""

import os
import sys

try:
    import requests
except ImportError:
    sys.exit("Bitte 'requests' installieren: pip install requests")

URL = os.environ.get("NETBOX_URL", "http://localhost:8000").rstrip("/")
TOKEN = os.environ.get("NETBOX_TOKEN")
if not TOKEN:
    sys.exit("NETBOX_TOKEN (Schreib-Token) noetig - siehe Ausgabe von setup.sh")

session = requests.Session()
session.headers.update({"Authorization": f"Token {TOKEN}", "Accept": "application/json"})


def upsert(endpoint: str, match: dict, data: dict) -> dict:
    """GET per match; sonst POST. Gibt das Objekt zurueck (idempotent)."""
    resp = session.get(f"{URL}/api/{endpoint}", params=match, timeout=15)
    resp.raise_for_status()
    results = resp.json()["results"]
    if results:
        return results[0]
    resp = session.post(f"{URL}/api/{endpoint}", json={**match, **data}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    # Zonen als Tags (Zone haengt laut ADR 0004 am Subnetz)
    for slug, name in [("zone-server", "Zone Server"), ("zone-dmz", "Zone DMZ")]:
        upsert("extras/tags/", {"slug": slug}, {"name": name})

    upsert("dcim/sites/", {"slug": "lab-dc"}, {"name": "Lab DC", "status": "active"})

    for cidr, zone in [("10.10.10.0/24", "zone-server"), ("10.10.20.0/24", "zone-dmz")]:
        upsert("ipam/prefixes/", {"prefix": cidr}, {"status": "active", "tags": [{"slug": zone}]})

    for addr, dns in [
        ("10.10.10.5/24", "web01.lab.local"),
        ("10.10.10.6/24", "app01.lab.local"),
        ("10.10.20.5/24", "proxy01.lab.local"),
    ]:
        upsert("ipam/ip-addresses/", {"address": addr}, {"status": "active", "dns_name": dns})

    print("Lab-Daten angelegt (Tags, Site, 2 Prefixe, 3 IP-Adressen).")


if __name__ == "__main__":
    main()
