"""Prüfung: DNS aus dem AD - PTR widerspricht A-Record (1.19.0, `store/dns_checks.py`)."""

from app.checks import Check
from app.models import FindingType
from app.store import dns_checks

CHECK = Check(
    "dns_ptr",
    "analyse",
    ("DNS: PTR gegen A-Record", "DNS: PTR against A record"),
    (
        "PTR einer IP nennt keinen ihrer A-Records (Warnung, wenn A-Records bzw. der Name auf "
        "eine andere IP zeigen; sonst Hinweis) – AD-DNS-Zonen per Zonentransfer",
        "PTR of an IP names none of its A records (warning if A records or the name point to "
        "another IP; otherwise note) – AD DNS zones by zone transfer",
    ),
    frozenset({FindingType.DNS_PTR_MISMATCH}),
    lambda session, tenant_id, cx: dns_checks.ptr_issues(session, tenant_id, cx),
    order=52,
)
