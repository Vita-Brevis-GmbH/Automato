"""Prüfung: Verbindungen der Applikationen (Wege uneinheitlich, Contract enger als Firewall, Regel
nie gebraucht; ADR 0021).

Steckbare Prüfung (0.110.0, ADR 0039) mit eigenem Durchgang im Hintergrund
(`reconcile.refresh_app_findings`, mit Zeitbudget) - nicht im Abgleich nach jedem Lauf."""

from app.checks import Check
from app.models import FindingType

CHECK = Check(
    "applikationen",
    "doku",
    ("Verbindungen der Applikationen", "Connections of the applications"),
    (
        "Verbindung je nach Weg offen bzw. gesperrt, Contract enger als die Firewall, Regel einer "
        "Applikation nie auf dem Weg",
        "Connection open or blocked depending on the path, contract narrower than the firewall, "
        "rule of an application never on the path",
    ),
    frozenset(
        {
            FindingType.APP_CONNECTION_MISMATCH,
            FindingType.APP_CONTRACT_NARROWER,
            FindingType.APP_RULE_UNUSED,
        }
    ),
    None,
    step="applikationen",
    in_reconcile=False,
    order=95,
)
