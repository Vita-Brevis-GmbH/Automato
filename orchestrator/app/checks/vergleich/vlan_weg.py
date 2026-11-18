"""Prüfung: VLAN auf dem Weg nicht überall erlaubt (1.19.0, ADR 0067, `store/path_compare`)."""

from app.checks import Check
from app.checks.vergleich import deviations
from app.models import FindingType

CHECK = Check(
    "vlan_weg",
    "vergleich",
    ("Wege vergleichen: VLAN erlaubt", "Compare paths: VLAN allowed"),
    (
        "Je L3-Abschnitt (Next-Hop zu einem anderen gelesenen Gerät): das VLAN des Ausgangs ist "
        "auf einem Trunk bzw. Port des Wegs über die Kabel nicht erlaubt bzw. der Eingang liegt "
        "in einem anderen VLAN",
        "Per L3 section (next-hop to another read device): the VLAN of the egress is not allowed "
        "on a trunk or port of the path via the cables or the ingress is in another VLAN",
    ),
    frozenset({FindingType.PATH_VLAN_MISSING}),
    lambda session, tenant_id, cx: deviations(
        session, tenant_id, cx, "vlan", FindingType.PATH_VLAN_MISSING
    ),
    order=86,
)
