"""Prüfung: DNS aus dem AD - nicht prüfbar (1.19.0, `store/dns_checks.py`): Quelle bzw. Zonen
nicht gelesen, Rückwärtszone fehlt, PTR ausserhalb der gelesenen Zonen, NetBox nicht gelesen -
keine stillen Grenzen."""

from app.checks import Check
from app.models import FindingType
from app.store import dns_checks

CHECK = Check(
    "dns_ungeprueft",
    "analyse",
    ("DNS: nicht prüfbar", "DNS: not checkable"),
    (
        "Was die Prüfungen zu DNS nicht beurteilen können: AD-DNS-Quelle bzw. Zonen nicht "
        "gelesen, Rückwärtszone fehlt, PTR ausserhalb der gelesenen Zonen, NetBox nicht gelesen",
        "What the DNS checks cannot judge: AD DNS source or zones not collected, reverse zone "
        "missing, PTR outside the collected zones, NetBox not collected",
    ),
    frozenset({FindingType.DNS_UNCHECKED}),
    lambda session, tenant_id, cx: dns_checks.unchecked_issues(session, tenant_id, cx),
    order=54,
)
