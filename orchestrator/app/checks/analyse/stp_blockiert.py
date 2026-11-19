"""Prüfung: Spanning Tree: unerwartet blockiert (1.19.0, `store/stp_findings.py`)."""

from app.checks import Check
from app.models import FindingType
from app.store import stp_findings

CHECK = Check(
    "stp_blockiert",
    "analyse",
    ("Spanning Tree: unerwartet blockiert", "Spanning Tree: unexpectedly blocked"),
    (
        "Blockierter Port, der nicht zu einem redundanten Weg zwischen Switches passt: "
        "Schutzfunktion (Root/Loop Guard, Bridge Assurance), Backup-Rolle, vPC-Peer-Link, "
        "Edge-Port oder Gegenstelle kein Switch (Firewall, ESXi-Host, Router); ohne bekannte "
        "Gegenstelle keine Aussage",
        "Blocked port that does not fit a redundant path between switches: protection feature "
        "(root/loop guard, bridge assurance), backup role, vPC peer link, edge port or peer not "
        "a switch (firewall, ESXi host, router); no statement without a known peer",
    ),
    frozenset(
        {
            FindingType.STP_PORT_BLOCKED,
        }
    ),
    lambda session, tenant_id, cx: stp_findings.blocked_issues(session, tenant_id, cx),
    order=68,
)
