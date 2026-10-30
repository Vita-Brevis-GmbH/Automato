"""Wohin eine Zeile der Applikations-Doku gehoert - an einer Stelle (0.104.0, Modularisierung),
seit 0.118.0 nach dem **Regelwerk** des Mandanten (`appdoc_ruleset`, ADR 0043).

Die Merkmale setzen `appdoc.mark_categories` und `appdoc._covered_rows` (Relevanz mit Ursache,
`OUT_BROAD`, `TO_INTERNET`, `HELPER`, `L3OUT_COVERED`, `CAT10_OUT`, `DISABLED`); hier steht,
welche Bedingung des Regelwerks eine Zeile trifft (`matches`), und `decide` liest das Regelwerk
von oben nach unten: die erste eingeschaltete Regel, die trifft, entscheidet die Wirkung
(`appdoc_ruleset.EFFECTS`); eine Ausnahme (direkt zugeordnet) hebt die genannten Regeln darunter
auf; trifft keine, steht die Zeile in ihrer Kategorie (`TABLE`).
"""

from collections.abc import Collection

from app.i18n import L
from app.store import appdoc, appdoc_path, appdoc_ruleset, relevance
from app.store.appdoc_ruleset import EXCEPT, MARK, TABLE

# Kurztext an der Zeile bei der Wirkung `mark` (Spalte Aktion)
_MARKS = {"disabled": ("deaktiviert", "disabled")}


def matches(key: str, row: dict, assigned: Collection[int] = (),
            excluded: Collection[int] = ()) -> bool:  # fmt: skip
    """Trifft die Bedingung `key` die Zeile? `assigned`/`excluded` = Regeln, die der
    Applikation direkt zugeordnet bzw. ausgeschlossen sind (0.60.0)."""
    rid = row.get(appdoc.RULE)
    impossible = row.get(appdoc.REL) == relevance.IMPOSSIBLE
    off = impossible and row.get(appdoc.REL_CAUSE) == appdoc_path.OFF_PATH
    broad = row.get(appdoc.SCOPE) in appdoc.BROAD
    found = {
        "excluded": lambda: rid in excluded,
        "assigned": lambda: rid in assigned,
        "impossible": lambda: impossible and not off,
        "off_path": lambda: off and broad,
        "off_path_specific": lambda: off and not broad,
        "to_internet": lambda: bool(row.get(appdoc.TO_INTERNET)),
        "out_broad": lambda: bool(row.get(appdoc.OUT_BROAD)),
        "disabled": lambda: bool(row.get(appdoc.DISABLED)),
        "l3out_helper": lambda: bool(row.get(appdoc.HELPER)),
        "l3out_covered": lambda: bool(row.get(appdoc.L3OUT_COVERED)),
        "cat10_out": lambda: bool(row.get(appdoc.CAT10_OUT)),
    }.get(key)
    return bool(found and found())


def decide(row: dict, profile: appdoc_ruleset.Profile, assigned: Collection[int] = (),
           excluded: Collection[int] = ()) -> tuple[str, str | None]:  # fmt: skip
    """Wirkung und entscheidende Bedingung einer Zeile nach dem Regelwerk `profile`."""
    lifted: set[str] = set()
    for rule in profile.active():
        if rule.key in lifted or not matches(rule.key, row, assigned, excluded):
            continue
        if rule.effect == EXCEPT:
            lifted |= set(rule.lifts)
            continue
        return rule.effect, rule.key
    return TABLE, None


def mark(row: dict) -> None:
    """Zeile mit der Wirkung `mark`: Kurztext der Bedingung an der Aktion („deaktiviert“)."""
    text = _MARKS.get(row.get(appdoc.DECIDED_BY) or "")
    if row.get(appdoc.DECIDED) == MARK and text:
        row[appdoc.ACTION] = f"{row.get(appdoc.ACTION) or ''} ({L(*text)})".strip()
