"""Wohin eine Zeile der Applikations-Doku gehoert - an einer Stelle (0.104.0, Modularisierung).

Bisher entschieden `appdoc._envelope` (Doku je System), `applications.build_doc` und
`applications._hits_and_tables` (Applikations-Doku) je fuer sich, welche Zeilen in den
Kategorien stehen, welche nur Treffer sind und welche ganz wegfallen - jede neue Regel der
Anwender („nur Zugriffe auf die Applikation“, „breite Quelle ausgehend nicht“) musste an allen
drei Stellen nachgezogen werden. Die Merkmale setzt weiter `appdoc.mark_categories`; hier steht
nur, was sie bedeuten:

- `DROPPED`: ausgehende Firewall-Regel mit breiter Quelle (`OUT_BROAD`, 0.103.0) - kein Zugriff
  der Applikation, nirgends in der Doku, nur gezaehlt; direkt zugeordnete Regeln bleiben.
- `IMPOSSIBLE`: routingtechnisch nicht moeglich (ADR 0019) - nicht in den Kategorien, nur
  gezaehlt (Applikation) bzw. mit Begruendung gelistet (Doku je System).
- `HIT`: nicht in den Kategorien (`CAT_HIDDEN`: Hilfs-Contract ueber einen L3Out mit Firewall,
  Contract nicht enger als die Firewall, unspezifisch ausgehend) - als Treffer in „Regeln &
  Contracts“ und den Verbindungen bleibt er.
- `TABLE`: in der Kategorie.
"""

from collections.abc import Collection

from app.store import appdoc, relevance

TABLE, HIT, IMPOSSIBLE, DROPPED = "table", "hit", "impossible", "dropped"


def place(row: dict, assigned: Collection[int] = ()) -> str:
    """Ort einer Zeile (`TABLE`, `HIT`, `IMPOSSIBLE`, `DROPPED`); `assigned` = Regeln, die der
    Applikation direkt zugeordnet sind (0.60.0)."""
    if row.get(appdoc.OUT_BROAD) and row.get(appdoc.RULE) not in assigned:
        return DROPPED
    if row.get(appdoc.REL) == relevance.IMPOSSIBLE:
        return IMPOSSIBLE
    if row.get(appdoc.CAT_HIDDEN):
        return HIT
    return TABLE


def in_categories(row: dict, assigned: Collection[int] = ()) -> bool:
    return place(row, assigned) == TABLE
