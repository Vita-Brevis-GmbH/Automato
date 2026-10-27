"""Sprache der Konsole des Agenten: Deutsch (Standard) oder Englisch (0.88.0, ADR 0028 Schritt 4).

Nach dem Muster von `orchestrator/app/i18n.py`: der deutsche Text steht im Code, der englische
daneben (`L("erreichbar", "reachable")`). Die Konsole (`--check`, `--poll`, Selbst-Update, Hilfe)
folgt `AUTOMATO_LANG` (`de` | `en`, in agent.env bzw. der Umgebung), Standard Deutsch.

Was an den Orchestrator geht - Ergebnis und Meldung eines Laufs, Teilfehler je Switch, Grund der
Update-Pruefung (`X-Agent-Update`) - bleibt deutsch: der Orchestrator speichert es und uebersetzt
erst beim Abruf (`orchestrator/app/store/run_text.py`). Einstiege, die ein Ergebnis bauen, laufen
darum mit `@german_only`; Texte, die an den Orchestrator gehen *und* auf der Konsole stehen,
entstehen mit `both` zweimal. Neue Threads beginnen in der Sprache der Konsole - `carry` gibt die
Sprache des Aufrufers mit.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TypeVar

T = TypeVar("T")

LANGS = ("de", "en")
DEFAULT = "de"
ENV = "AUTOMATO_LANG"

_current: ContextVar[str | None] = ContextVar("automato_lang", default=None)


def console() -> str:
    """Sprache der Konsole aus `AUTOMATO_LANG` (`en`, `en-GB` … = Englisch, sonst Deutsch)."""
    code = os.environ.get(ENV, "").strip().lower().split("-")[0].split("_")[0]
    return code if code in LANGS else DEFAULT


def language_header() -> dict[str, str]:
    """`Accept-Language` an den Orchestrator, wenn die Konsole nicht deutsch ist: er speichert
    trotzdem deutsch und gibt die Meldung in der Quittung zusaetzlich uebersetzt zurueck."""
    code = console()
    return {} if code == DEFAULT else {"Accept-Language": code}


def lang() -> str:
    """Sprache des laufenden Blocks (`use`), sonst die der Konsole."""
    return _current.get() or console()


def L(de: T, en: T) -> T:  # noqa: N802 - gleich wie in Orchestrator und Oberflaeche
    """Text in der aktuellen Sprache - Deutsch zuerst, Englisch daneben."""
    return en if lang() == "en" else de


@contextmanager
def use(code: str) -> Iterator[None]:
    """Sprache fuer einen Block setzen."""
    token = _current.set(code if code in LANGS else DEFAULT)
    try:
        yield
    finally:
        _current.reset(token)


def german():
    """Block mit deutschen Texten - fuer alles, was an den Orchestrator geht."""
    return use(DEFAULT)


def german_only(func: Callable[..., T]) -> Callable[..., T]:
    """Dekorator fuer Einstiege, deren Ergebnis der Orchestrator speichert (Laeufe)."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with use(DEFAULT):
            return func(*args, **kwargs)

    return wrapper


def carry(func: Callable[..., T]) -> Callable[..., T]:
    """`func` fuer einen anderen Thread: laeuft dort in der Sprache des Aufrufers."""
    code = lang()

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with use(code):
            return func(*args, **kwargs)

    return wrapper


def both(make: Callable[[], str]) -> tuple[str, str]:
    """(deutsch, aktuelle Sprache): fuer Texte, die an den Orchestrator gehen und auf der
    Konsole stehen."""
    with german():
        de = make()
    return de, (de if lang() == DEFAULT else make())


class Failure(RuntimeError):
    """Fehler, dessen Text erst bei der Ausgabe entsteht (`str()` in der Sprache des Blocks):
    derselbe Fehler steht so deutsch im Grund an den Orchestrator und in `AUTOMATO_LANG` auf
    der Konsole (Selbst-Update)."""

    def __init__(self, text: Callable[[], str]) -> None:
        super().__init__()
        self._text = text

    def __str__(self) -> str:
        return self._text()
