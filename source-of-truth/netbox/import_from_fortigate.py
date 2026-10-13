#!/usr/bin/env python3
"""FortiGate-Adressobjekte -> NetBox importieren (IPAM-Seeding). NUR Labor/Bootstrap.

ACHTUNG - dieser Schritt SCHREIBT in NetBox (anders als der rein lesende Collector)
und nutzt daher den NetBox-SCHREIB-Token (Admin), nicht den read-only Collector-Token.
Der FortiGate-Zugriff bleibt rein lesend.

Zweck: einmaliger Bootstrap, wenn NetBox noch keine IPAM-Daten hat. Die FortiGate-
Adressobjekte werden als Ausgangsbasis uebernommen:
  - Netze  (ipmask, Maske < /32)  -> ipam/prefixes      (description = FortiGate-Name)
  - Hosts  (ipmask /32)           -> ipam/ip-addresses   (description/dns_name = Name)
Ranges, FQDN, Geography etc. werden uebersprungen (nicht als IPAM-Objekt abbildbar).
Danach sollte NetBox als Source of Truth gepflegt/kuratiert werden - die Firewall ist
nur der Startpunkt. Importierte Objekte tragen das Tag 'automato-import'.

Idempotent (vorhandene Prefixe/Adressen werden nicht ueberschrieben).

Voraussetzung: python3 + requests (siehe lab/install-prereqs.sh).
Verwendung:
  FORTIGATE_URL=https://10.0.0.1:4444 FORTIGATE_TOKEN=<readonly-key> \
  NETBOX_URL=http://localhost:8080 NETBOX_TOKEN=<netbox-schreib-token> \
    python3 import_from_fortigate.py
"""

import ipaddress
import os
import re
import sys
from collections import Counter

try:
    import requests
except ImportError:
    sys.exit("Bitte 'requests' installieren: pip install requests")

FG_URL = os.environ.get("FORTIGATE_URL", "").rstrip("/")
FG_TOKEN = os.environ.get("FORTIGATE_TOKEN", "")
FG_VERIFY = os.environ.get("FORTIGATE_VERIFY_TLS", "false").lower() == "true"
NB_URL = os.environ.get("NETBOX_URL", "http://localhost:8080").rstrip("/")
NB_TOKEN = os.environ.get("NETBOX_TOKEN", "")

if not FG_URL or not FG_TOKEN:
    sys.exit("FORTIGATE_URL und FORTIGATE_TOKEN (read-only) noetig.")
if not NB_TOKEN:
    sys.exit("NETBOX_TOKEN (Schreib-Token, Admin) noetig - siehe setup.sh-Ausgabe.")

if not FG_VERIFY:
    requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]

TAG = "automato-import"
_DNS_SAFE = re.compile(r"[A-Za-z0-9._-]+")

fg = requests.Session()
fg.headers.update({"Authorization": f"Bearer {FG_TOKEN}", "Accept": "application/json"})
nb = requests.Session()
nb.headers.update({"Authorization": f"Token {NB_TOKEN}", "Accept": "application/json"})


def fg_get(path: str) -> list[dict]:
    r = fg.get(f"{FG_URL}/api/v2/cmdb/{path}", timeout=30, verify=FG_VERIFY)
    r.raise_for_status()
    return r.json().get("results", [])


def nb_upsert(endpoint: str, match: dict, data: dict) -> tuple[dict, bool]:
    """GET per match; sonst POST. Rueckgabe (objekt, created)."""
    r = nb.get(f"{NB_URL}/api/{endpoint}", params=match, timeout=15)
    r.raise_for_status()
    results = r.json()["results"]
    if results:
        return results[0], False
    r = nb.post(f"{NB_URL}/api/{endpoint}", json={**match, **data}, timeout=15)
    r.raise_for_status()
    return r.json(), True


def to_network(subnet: str):
    """FortiOS-Subnetz -> ip_network. '10.0.0.0 255.255.255.0' | '10.0.0.0/24' | '10.0.0.1'."""
    subnet = subnet.strip()
    if " " in subnet:
        ip, mask = subnet.split()
        return ipaddress.ip_network(f"{ip}/{mask}", strict=False)
    return ipaddress.ip_network(subnet, strict=False)


def main() -> None:
    nb_upsert("extras/tags/", {"slug": TAG}, {"name": "Automato Import"})

    addresses = fg_get("firewall/address")
    counts = Counter()
    skipped = Counter()

    for obj in addresses:
        name = obj.get("name", "")
        otype = obj.get("type", "ipmask")

        if otype == "ipmask" and obj.get("subnet"):
            try:
                net = to_network(obj["subnet"])
            except ValueError:
                skipped["ungueltig"] += 1
                continue
            if net.prefixlen == 0 or net.network_address.is_unspecified:
                skipped["all/none"] += 1  # 0.0.0.0/0 bzw. 0.0.0.0 nicht importieren
                continue
            if net.prefixlen == net.max_prefixlen:  # Host
                host = f"{net.network_address}/{net.max_prefixlen}"
                data = {"status": "active", "description": name, "tags": [{"slug": TAG}]}
                if _DNS_SAFE.fullmatch(name):
                    data["dns_name"] = name
                _, created = nb_upsert("ipam/ip-addresses/", {"address": host}, data)
                counts["ip_neu" if created else "ip_vorhanden"] += 1
            else:  # Netz
                _, created = nb_upsert(
                    "ipam/prefixes/",
                    {"prefix": str(net)},
                    {"status": "active", "description": name, "tags": [{"slug": TAG}]},
                )
                counts["prefix_neu" if created else "prefix_vorhanden"] += 1
        else:
            skipped[otype] += 1

    print(f"FortiGate-Adressobjekte gelesen: {len(addresses)}")
    print(
        "In NetBox: "
        f"Prefixe neu={counts['prefix_neu']} (vorhanden {counts['prefix_vorhanden']}), "
        f"IP-Adressen neu={counts['ip_neu']} (vorhanden {counts['ip_vorhanden']})"
    )
    if skipped:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(skipped.items()))
        print(f"Uebersprungen (nicht als IPAM-Objekt abbildbar): {detail}")
    print(
        "Hinweis: NetBox ist ab jetzt die Source of Truth - die FortiGate-Objekte sind nur "
        "der Startpunkt. Bitte in NetBox pruefen/kuratieren (Tag 'automato-import')."
    )


if __name__ == "__main__":
    main()
