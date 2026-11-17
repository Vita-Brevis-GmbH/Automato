"""Prüfung: Spanning Tree: viele Topologieänderungen (1.19.0, `store/stp_findings.py`)."""

from app.checks import Check
from app.models import FindingType
from app.store import stp_findings

CHECK = Check(
    "stp_wechsel",
    "analyse",
    ("Spanning Tree: viele Topologieänderungen", "Spanning Tree: many topology changes"),
    (
        "Instanzen mit mindestens der Schwelle an Topologieänderungen je 24 h (Vorgabe 10, je "
        "Mandant einstellbar) aus dem Verlauf der Zähler der Läufe",
        "Instances with at least the limit of topology changes per 24 h (default 10, adjustable "
        "per tenant) from the history of the counters of the runs",
    ),
    frozenset(
        {
            FindingType.STP_TOPOLOGY_CHANGES,
        }
    ),
    lambda session, tenant_id, cx: stp_findings.tc_issues(session, tenant_id, cx),
    order=71,
)
