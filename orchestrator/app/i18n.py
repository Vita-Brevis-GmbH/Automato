"""Sprache der Texte vom Server: Deutsch (Standard) oder Englisch (0.87.0, ADR 0028 Schritt 2/3).

Wie in der Oberflaeche steht der deutsche Text im Code und der englische daneben:
`L("gesperrt", "blocked")`. Die Sprache gilt je Anfrage - die Middleware `middleware` liest
`?lang=` (Downloads/Exporte) bzw. `Accept-Language` und setzt sie fuer die Dauer der Anfrage.
Ohne Anfrage (Celery, Zeitplan, Threads) gilt Deutsch; die CLI setzt sie fuer die Dauer des
Befehls (`--lang`, `AUTOMATO_LANG`, 0.88.0).

Regeln:
- `L` nur fuer Texte, die beim Abruf entstehen (Antwort, Export). Was gespeichert wird
  (Befund-Details, Laufmeldungen, Audit), bleibt deutsch: Einstiege, die speichern, laufen
  mit `german()` bzw. `@german_only`.
- Werte, die verglichen oder als Schluessel benutzt werden (Spaltennamen der Doku, Bereiche
  des Betriebszustands, Urteile, Arten), bleiben deutsch; uebersetzt wird erst bei der Ausgabe
  mit `term()` aus `TERMS`.
- Begriffe nach `docs/GLOSSAR.md`, Fachbegriffe englisch.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TypeVar
from urllib.parse import parse_qs

T = TypeVar("T")

LANGS = ("de", "en")
DEFAULT = "de"

_current: ContextVar[str] = ContextVar("automato_lang", default=DEFAULT)


def lang() -> str:
    """Sprache der laufenden Anfrage (`de` | `en`)."""
    return _current.get()


def L(de: T, en: T) -> T:  # noqa: N802 - gleich wie in der Oberflaeche
    """Text in der Sprache der Anfrage - Deutsch zuerst, Englisch daneben."""
    return en if _current.get() == "en" else de


def parse(value: str | None) -> str:
    """Sprache aus `Accept-Language` bzw. `?lang=`: erste unterstuetzte nach Gewicht, sonst de."""
    best, weight = DEFAULT, -1.0
    for i, part in enumerate((value or "").split(",")):
        tag, _, params = part.strip().partition(";")
        code = tag.strip().lower().split("-")[0]
        if code not in LANGS:
            continue
        q = 1.0
        for p in params.split(";"):
            k, _, v = p.strip().partition("=")
            if k == "q":
                try:
                    q = float(v)
                except ValueError:
                    q = 0.0
        if q <= 0:  # q=0 = nicht erwuenscht
            continue
        q -= i * 1e-6  # gleiches Gewicht: Reihenfolge entscheidet
        if q > weight:
            best, weight = code, q
    return best


@contextmanager
def use(code: str) -> Iterator[None]:
    """Sprache fuer einen Block setzen (Tests, Exporte mit eigener Sprachwahl)."""
    token = _current.set(code if code in LANGS else DEFAULT)
    try:
        yield
    finally:
        _current.reset(token)


def german():
    """Block mit deutschen Texten - fuer alles, was gespeichert wird."""
    return use(DEFAULT)


def german_only(func: Callable[..., T]) -> Callable[..., T]:
    """Dekorator fuer Einstiege, die Texte speichern (Abgleich, Befunde, Einspielen)."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with use(DEFAULT):
            return func(*args, **kwargs)

    return wrapper


# Feste deutsche Werte, die intern Schluessel bleiben und erst bei der Ausgabe uebersetzt werden
TERMS: dict[str, str] = {}


def term(de: str | None) -> str | None:
    """Deutscher Schluessel -> Anzeige in der Sprache der Anfrage (unbekannt = unveraendert)."""
    if de is None or _current.get() != "en":
        return de
    return TERMS.get(de, de)


def middleware(app):
    """ASGI-Middleware: Sprache je Anfrage aus `?lang=` bzw. `Accept-Language`."""

    async def wrapped(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        query = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
        code = (query.get("lang") or [""])[0]
        if code not in LANGS:
            headers = dict(scope.get("headers") or [])
            code = parse(headers.get(b"accept-language", b"").decode("latin-1"))
        token = _current.set(code)
        try:
            return await app(scope, receive, send)
        finally:
            _current.reset(token)

    return wrapped
