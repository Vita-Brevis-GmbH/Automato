"""Prüfung: Spanning Tree: BPDU-Guard (1.19.0, `store/stp_findings.py`)."""

from app.checks import Check
from app.models import FindingType
from app.store import stp_findings

CHECK = Check(
    "stp_bpdu_guard",
    "analyse",
    ("Spanning Tree: BPDU-Guard", "Spanning Tree: BPDU guard"),
    (
        "Port err-disabled durch BPDU-Guard (andere Gründe nur in der Ansicht)",
        "Port err-disabled by BPDU guard (other reasons only in the view)",
    ),
    frozenset(
        {
            FindingType.STP_BPDU_GUARD,
        }
    ),
    lambda session, tenant_id, cx: stp_findings.bpdu_issues(session, tenant_id, cx),
    order=69,
)
