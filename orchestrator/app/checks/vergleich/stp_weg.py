"""Prüfung: Spanning Tree blockiert den Weg (1.19.0, ADR 0067, `store/path_compare`)."""

from app.checks import Check
from app.checks.vergleich import deviations
from app.models import FindingType

CHECK = Check(
    "stp_weg",
    "vergleich",
    ("Wege vergleichen: Spanning Tree", "Compare paths: Spanning Tree"),
    (
        "Je L3-Abschnitt: jeder Weg im VLAN führt über einen Port, den Spanning Tree blockiert "
        "(Zustand je VLAN bzw. MST-Instanz, err-disabled)",
        "Per L3 section: every path in the VLAN runs via a port blocked by Spanning Tree (state "
        "per VLAN or MST instance, err-disabled)",
    ),
    frozenset({FindingType.PATH_STP_BLOCKED}),
    lambda session, tenant_id, cx: deviations(
        session, tenant_id, cx, "stp", FindingType.PATH_STP_BLOCKED
    ),
    order=88,
)
