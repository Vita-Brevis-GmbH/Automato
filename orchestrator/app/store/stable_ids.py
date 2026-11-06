"""Gleiche Kennung beim erneuten Einlesen (0.141.0, ADR 0049).

Anwender 2026-11-06: „Die Berechnungen nur anstossen, wenn was geändert hat“ - ein Lauf ersetzt
Regeln, NAT, Objekte und VMs eines Quellsystems; bis 0.140.0 bekam dabei jede Zeile eine neue
Kennung, auch wenn sich nichts geaendert hatte. Doku und Verbindungen verweisen auf diese
Kennungen (Links, Befunde) - ein Stand ohne Aenderung waere so trotzdem veraltet gewesen.

Vor dem Ersetzen merkt `previous` die Kennungen je fachlichem Schluessel; beim Anlegen nimmt
`Ids.take` die alte Kennung desselben Schluessels (jede nur einmal). Neue Zeilen bekommen wie
bisher eine neue Kennung, weggefallene geben ihre frei. Nur innerhalb des Quellsystems, das gerade
eingespielt wird.
"""

from collections.abc import Hashable

from sqlalchemy import select
from sqlalchemy.orm import Session


class Ids:
    """Alte Kennungen je Schluessel - jede wird hoechstens einmal vergeben."""

    def __init__(self, found: dict[Hashable, int] | None = None) -> None:
        self._free = dict(found or {})
        self.kept = 0

    def take(self, key: Hashable) -> int | None:
        found = self._free.pop(key, None)
        if found is not None:
            self.kept += 1
        return found


def previous(session: Session, id_column, key_columns: list, where) -> Ids:
    """Kennungen der Zeilen, die gleich ersetzt werden, je Schluessel (bei Dubletten die
    aelteste)."""
    found: dict[Hashable, int] = {}
    stmt = select(id_column, *key_columns).where(where).order_by(id_column)
    for row in session.execute(stmt):
        found.setdefault(tuple(_plain(v) for v in row[1:]), row[0])
    return Ids(found)


def _plain(value):
    """Enum -> Wert, damit Schluessel aus der Datenbank und aus dem Lauf gleich aussehen."""
    return getattr(value, "value", value)


def key(*values) -> tuple:
    return tuple(_plain(v) for v in values)
