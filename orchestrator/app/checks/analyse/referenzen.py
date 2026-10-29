"""Prüfung: Referenzquellen (IPAM, DDI ↔ NetBox, ACI-Subnetze; `store/references.checks`).

Steckbare Prüfung (0.110.0, ADR 0039)."""

from app.checks import Check
from app.models import FindingType


def _run(session, tenant_id, cx):
    from app.store import references

    return references.checks(session, tenant_id, cx.prefixes, cx.refs)


CHECK = Check(
    "referenzen",
    "analyse",
    ("IPAM-Abgleich", "IPAM reconciliation"),
    (
        "Name ≠ DNS-Name im IPAM, Subnetz fehlt im IPAM, VLAN weicht vom IPAM ab",
        "Name ≠ DNS name in the IPAM, subnet missing in the IPAM, VLAN differs from the IPAM",
    ),
    frozenset(
        {FindingType.NAME_MISMATCH, FindingType.SUBNET_NOT_IN_IPAM, FindingType.VLAN_MISMATCH}
    ),
    _run,
    order=51,
)
