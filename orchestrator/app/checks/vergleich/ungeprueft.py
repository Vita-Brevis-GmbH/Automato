"""Prüfung: L3-Abschnitte nicht (ganz) geprüft (1.19.0, ADR 0067 und ADR 0048 „keine stillen
Grenzen“): je Grund ein Hinweis mit der Zahl der Abschnitte und Beispielen."""

from app.checks import Check
from app.checks.vergleich import unchecked
from app.models import FindingType

CHECK = Check(
    "wege_ungeprueft",
    "vergleich",
    ("Wege vergleichen: nicht geprüft", "Compare paths: not checked"),
    (
        "Hinweis je Grund: L3-Abschnitte, die der Vergleich nicht (ganz) prüfen konnte (kein "
        "Kabel bzw. Weg über die Kabel bekannt, VLAN unbekannt, Kabel ohne NetBox, Switch ohne "
        "Spanning Tree) - gezählt statt still übergangen",
        "Note per reason: L3 sections the comparison could not (fully) check (no cable or path "
        "via the cables known, VLAN unknown, cables without NetBox, switch without Spanning "
        "Tree) - counted instead of silently skipped",
    ),
    frozenset({FindingType.PATH_L2_UNCHECKED}),
    unchecked,
    order=89,
)
