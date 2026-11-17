"""Prüfung: DNS aus dem AD - A-Records auf IPs, die NetBox nicht kennt (1.19.0,
`store/dns_checks.py`): Hinweis je AD-DNS-Quelle, nur gezählt."""

from app.checks import Check
from app.models import FindingType
from app.store import dns_checks

CHECK = Check(
    "dns_unbekannt",
    "analyse",
    ("DNS: IP nicht in NetBox", "DNS: IP not in NetBox"),
    (
        "A-Records des AD-DNS auf IPs, die die gelesene NetBox nicht kennt – Hinweis je Quelle, "
        "nur gezählt (der NetBox-Sync legt keine IP an)",
        "A records of the AD DNS pointing to IPs the collected NetBox does not know – note per "
        "source, only counted (the NetBox sync does not create IPs)",
    ),
    frozenset({FindingType.DNS_IP_UNKNOWN}),
    lambda session, tenant_id, cx: dns_checks.unknown_issues(session, tenant_id, cx),
    order=53,
)
