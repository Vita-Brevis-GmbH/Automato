"""Prüfung: Kabel und Nachbarn auf dem Weg (1.19.0, ADR 0067, `store/path_compare`)."""

from app.checks import Check
from app.checks.vergleich import deviations
from app.models import FindingType

CHECK = Check(
    "kabel_weg",
    "vergleich",
    ("Wege vergleichen: Kabel und Nachbarn", "Compare paths: cables and neighbors"),
    (
        "Je L3-Abschnitt: ein Kabel auf dem Weg passt nicht zu NetBox (CDP/LLDP) bzw. hat einen "
        "Befund der Verkabelung (Speed, Duplex, VLANs, Port-Channel)",
        "Per L3 section: a cable on the path does not match NetBox (CDP/LLDP) or has a cabling "
        "finding (speed, duplex, VLANs, Port-Channel)",
    ),
    frozenset({FindingType.PATH_CABLE_MISMATCH}),
    lambda session, tenant_id, cx: deviations(
        session, tenant_id, cx, "cable", FindingType.PATH_CABLE_MISMATCH
    ),
    order=87,
)
