"""Prüfung: Spanning Tree: gemischte Modi (1.19.0, `store/stp_findings.py`)."""

from app.checks import Check
from app.models import FindingType
from app.store import stp_findings

CHECK = Check(
    "stp_modus",
    "analyse",
    ("Spanning Tree: gemischte Modi", "Spanning Tree: mixed modes"),
    (
        "Verbundene Switches (Verkabelung) mit verschiedenen Modi (PVST+, Rapid-PVST+, MST, "
        "aus) bzw. Ports an einer Grenze zu einem anderen Protokoll",
        "Connected switches (cabling) with different modes (PVST+, Rapid-PVST+, MST, off) or "
        "ports at a boundary to another protocol",
    ),
    frozenset(
        {
            FindingType.STP_MODE_MIXED,
        }
    ),
    lambda session, tenant_id, cx: stp_findings.mode_issues(session, tenant_id, cx),
    order=70,
)
