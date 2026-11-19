"""Prüfung: Spanning Tree: Root (1.19.0, `store/stp_findings.py`)."""

from app.checks import Check
from app.models import FindingType
from app.store import stp_findings

CHECK = Check(
    "stp_root",
    "analyse",
    ("Spanning Tree: Root", "Spanning Tree: root"),
    (
        "Root je VLAN bzw. MST-Instanz gegen den Soll-Root (Standort, VLAN-Bereich); ohne Soll "
        "nur „Root ist <Gerät>“ als Info",
        "Root per VLAN or MST instance against the target root (site, VLAN range); without a "
        "target only “root is <device>” as info",
    ),
    frozenset(
        {
            FindingType.STP_ROOT_MISMATCH,
            FindingType.STP_ROOT_INFO,
        }
    ),
    lambda session, tenant_id, cx: stp_findings.root_issues(session, tenant_id, cx),
    order=67,
)
