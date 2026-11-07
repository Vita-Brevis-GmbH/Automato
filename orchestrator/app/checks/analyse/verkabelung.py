"""Prüfung: Verkabelung und L2 (0.62.0–0.65.0).

Steckbare Prüfung (0.110.0, ADR 0039) - aus `store/reconcile.py` hierher verschoben."""

from sqlalchemy.orm import Session

from app.checks import Check
from app.models import (
    FindingType,
    Severity,
    SubjectKind,
)
from app.store.reconcile import Issue


def _cabling_issues(session: Session, tenant_id: int) -> list[Issue]:
    """Verkabelung (0.62.0, `store/cabling.checks`): Speed, Duplex, VLANs bzw. Port-Channel
    zwischen zwei gelesenen Switches abweichend, Verbindung nur von einer Seite gemeldet; seit
    0.63.0 VLAN der VMs am Uplink ihres ESXi-Hosts nicht erlaubt (`store/l2.checks`), seit 0.64.0
    Redundanz und VLAN-Wege (`store/l2_topo.checks`)."""
    from app.store import cabling, l2, l2_topo

    out: list[Issue] = []
    cab = cabling.build(session, tenant_id)
    items = cabling.checks(session, tenant_id, cab) + l2.checks(session, tenant_id, cab=cab)
    items += l2_topo.checks(session, tenant_id, cab)
    for item in items:
        detail = {k: v for k, v in item.items() if k not in ("type", "subject", "severity")}
        out.append(Issue(FindingType(item["type"]), SubjectKind.LINK, item["subject"],
                         item.get("source_system_id"), Severity(item["severity"]),
                         detail=detail))  # fmt: skip
    return out


CHECK = Check(
    "verkabelung",
    "analyse",
    ("Verkabelung und L2", "Cabling and L2"),
    (
        "Speed, Duplex, VLANs bzw. Port-Channel zwischen zwei Switches abweichend, einseitig "
        "gemeldet, VLAN am ESXi-Uplink, Redundanz und VLAN-Wege, vPC",
        "Speed, duplex, VLANs or Port-Channel differing between two switches, reported one-sided, "
        "VLAN on the ESXi uplink, redundancy and VLAN paths, vPC",
    ),
    frozenset(
        {
            FindingType.LINK_SPEED_MISMATCH,
            FindingType.LINK_DUPLEX_MISMATCH,
            FindingType.LINK_VLAN_MISMATCH,
            FindingType.LINK_LAG_MISMATCH,
            FindingType.LINK_ONE_SIDED,
            FindingType.UPLINK_VLAN_MISSING,
            FindingType.L2_SINGLE_LINK,
            FindingType.L2_SINGLE_SWITCH,
            FindingType.L2_SINGLE_POINT,
            FindingType.LAG_DEGRADED,
            FindingType.VLAN_SPLIT,
            FindingType.VPC_MISMATCH,
        }
    ),
    lambda session, tenant_id, cx: _cabling_issues(session, tenant_id),
    order=61,
)
