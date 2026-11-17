"""Prüfung: Verkabelung im Detail (1.19.0, `store/cable_labels.checks`).

Steckbare Prüfung (ADR 0039): LLDP/CDP-Nachbar passt nicht zum Kabel in NetBox (gelesen mit der
Referenzquelle NetBox); Kabelnummer in NetBox und Automato verschieden; Duplex nicht voll - nur,
wenn beide Enden gelesen sind und ihr Duplex melden. Ohne Nachbar-Daten bzw. ohne Label in NetBox
kein Befund (unbekannt statt falsch)."""

from app.checks import Check
from app.models import FindingType


def _run(session, tenant_id, cx):
    from app.models import SubjectKind
    from app.store import cable_labels
    from app.store.reconcile import Issue

    out = []
    for item in cable_labels.checks(session, tenant_id):
        out.append(Issue(item["type"], SubjectKind.LINK, item["subject"], item["source_system_id"],
                         item["severity"], detail=item["detail"]))  # fmt: skip
    return out


CHECK = Check(
    "kabel",
    "analyse",
    ("Kabel: NetBox, Kabelnummer, Duplex", "Cables: NetBox, cable number, duplex"),
    (
        "LLDP/CDP-Nachbar passt nicht zum Kabel in NetBox, Kabelnummer in NetBox und Automato "
        "verschieden, Duplex nicht voll (beide Enden gelesen)",
        "LLDP/CDP neighbor does not match the cable in NetBox, cable number differs between "
        "NetBox and Automato, duplex not full (both ends read)",
    ),
    frozenset(
        {
            FindingType.CABLE_NEIGHBOR_MISMATCH,
            FindingType.CABLE_LABEL_MISMATCH,
            FindingType.LINK_HALF_DUPLEX,
        }
    ),
    _run,
    order=62,
)
