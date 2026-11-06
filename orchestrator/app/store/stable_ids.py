"""Gleiche Kennung beim erneuten Einlesen (0.141.0, ADR 0049).

Anwender 2026-11-06: „Die Berechnungen nur anstossen, wenn was geändert hat“ - ein Lauf ersetzt
Regeln, NAT, Objekte und VMs eines Quellsystems; bis 0.140.0 bekam dabei jede Zeile eine neue
Kennung, auch wenn sich nichts geaendert hatte. Doku und Verbindungen verweisen auf diese
Kennungen (Links, Befunde) - ein Stand ohne Aenderung waere so trotzdem veraltet gewesen.

Vor dem Ersetzen merkt `previous` die Kennungen je fachlichem Schluessel; beim Anlegen nimmt
`Ids.take` die alte Kennung desselben Schluessels (jede nur einmal). Neue Zeilen bekommen wie
bisher eine neue Kennung, weggefallene geben ihre frei. Nur innerhalb des Quellsystems, das gerade
eingespielt wird.

0.141.2 (Anwender: Link auf die Regel in den Findings „404“): mehrere Zeilen mit demselben
Schluessel (z. B. ACI-Regeln eines Subjects) behalten ihre Kennungen in ihrer Reihenfolge - bis
0.141.1 nur die erste, die anderen bekamen bei jedem Lauf eine neue. `Ids.plan` behaelt die
Kennung auch beim Umbenennen (`loose`: Schluessel ohne Namen, z. B. Policy-ID), wenn genau eine
alte und eine neue Zeile dazu passen. Unter SQLite (Tests) liegen neue Kennungen ueber allen
bisherigen - sonst vergibt SQLite eine, die eine spaeter angelegte alte Zeile braucht.
"""

from collections import defaultdict
from collections.abc import Callable, Hashable

from sqlalchemy import func, select
from sqlalchemy.orm import Session


class Ids:
    """Alte Kennungen je Schluessel - jede wird hoechstens einmal vergeben."""

    def __init__(self, found: dict[Hashable, list[int]] | None = None,
                 start: int | None = None) -> None:  # fmt: skip
        self._free = {k: list(v) for k, v in (found or {}).items()}
        self._next = start  # SQLite: naechste freie Kennung, sonst None (Sequenz der Datenbank)
        self.kept = 0

    def take(self, key: Hashable) -> int | None:
        free = self._free.get(key)
        if free:
            self.kept += 1
            return free.pop(0)
        return self._new()

    def plan(
        self, keys: list[Hashable], loose: Callable[[Hashable], Hashable | None] | None = None
    ) -> list[int | None]:
        """Kennungen fuer alle neuen Zeilen in ihrer Reihenfolge: zuerst gleicher Schluessel,
        dann `loose` (Schluessel ohne Namen) - nur, wenn genau eine uebrige alte und genau eine
        neue Zeile dazu passen (umbenannt)."""
        out: list[int | None] = []
        for key in keys:
            free = self._free.get(key)
            if free:
                self.kept += 1
                out.append(free.pop(0))
            else:
                out.append(None)
        if loose is not None:
            old: dict[Hashable, list[tuple[Hashable, int]]] = defaultdict(list)
            for key, free in self._free.items():
                if (lk := loose(key)) is not None:
                    old[lk] += [(key, x) for x in free]
            new: dict[Hashable, list[int]] = defaultdict(list)
            for i, key in enumerate(keys):
                if out[i] is None and (lk := loose(key)) is not None:
                    new[lk].append(i)
            for lk, idx in new.items():
                if len(idx) == 1 and len(old.get(lk, [])) == 1:
                    ((was, found),) = old[lk]
                    self._free[was].remove(found)
                    out[idx[0]] = found
                    self.kept += 1
        return [self._new() if x is None else x for x in out]

    def _new(self) -> int | None:
        if self._next is None:
            return None
        self._next += 1
        return self._next - 1


def previous(session: Session, id_column, key_columns: list, where) -> Ids:
    """Kennungen der Zeilen, die gleich ersetzt werden, je Schluessel (bei Dubletten in der
    Reihenfolge ihrer Kennung)."""
    found: dict[Hashable, list[int]] = defaultdict(list)
    stmt = select(id_column, *key_columns).where(where).order_by(id_column)
    for row in session.execute(stmt):
        found[tuple(_plain(v) for v in row[1:])].append(row[0])
    start = None
    if session.get_bind().dialect.name == "sqlite":
        start = (session.scalar(select(func.max(id_column))) or 0) + 1
    return Ids(found, start)


def _plain(value):
    """Enum -> Wert, damit Schluessel aus der Datenbank und aus dem Lauf gleich aussehen."""
    return getattr(value, "value", value)


def key(*values) -> tuple:
    return tuple(_plain(v) for v in values)
