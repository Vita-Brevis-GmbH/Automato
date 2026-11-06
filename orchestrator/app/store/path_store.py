"""Wege einmal je Mandant und Datenstand (0.140.0, ADR 0048).

Anwender 2026-11-06: „Wir rechnen heute nicht alle Wege durch und lassen auch sonst immer wieder
viel liegen, dies ist nicht zuverlaessig und daher gefaehrlich“ – bis 0.139 rechnete jede
Applikation (und jede Pruefung darin) ihre Wege neu: der Weg vom Monitoring, AD oder Backup zu
einem Server kam in fast jeder Applikation vor und wurde jedes Mal gerechnet, dazu je Applikation
die Policies aller Firewalls neu geladen. Darum gab es Grenzen, und die liessen Teile ungeprueft.

Hier liegt je Mandant ein Speicher, solange sein Datenstand gilt (`doc_snapshots._tenant_parts`:
letzter Lauf, Quellsysteme, letzte Aenderung, dazu die Version):

- **Wege** (`paths.trace`): Ergebnis je Aufruf (Quelle, Ziel, Start, Dienst, Art) und Sprache,
  gepackt (pickle) – jeder Abruf bekommt eine eigene Kopie, Aufrufer duerfen es ergaenzen.
- **Policies** (`policy_check.Checker`): die geladenen Policies je Firewall/VDOM samt Index und
  Interfaces – reine Daten, keine Objekte der Datenbank.
- **Urteile je Hop** (`policy_check.Checks.cache`): je Sprache; seit 0.140.0 je Adressklasse statt
  je IP (`policy_check.Checker.addr_class`).

Ein neuer Lauf bzw. eine Aenderung ergibt einen neuen Datenstand – der alte Speicher faellt weg.
Benutzt wird er dort, wo `active(...)` ihn setzt (Doku und Verbindungen im Hintergrund und beim
Oeffnen); die Weg-Suche rechnet wie bisher fuer sich. Nur lesend; nichts davon wird gespeichert.
"""

import logging
import pickle
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy.orm import Session

from app import __version__
from app.config import get_settings
from app.i18n import lang

log = logging.getLogger(__name__)

IDLE_SECONDS = 1800  # unbenutzter Speicher eines Mandanten faellt weg


class Store:
    """Speicher eines Mandanten fuer einen Datenstand."""

    def __init__(self, tenant_id: int, key: tuple) -> None:
        self.tenant_id = tenant_id
        self.key = key
        self.lock = threading.Lock()
        self.traces: OrderedDict[tuple, bytes] = OrderedDict()
        self.size = 0
        self.checker: dict[str, dict] = {}  # Caches des Checkers (reine Daten)
        self.decisions: dict[str, dict] = {}  # Urteile je Hop, je Sprache
        self.hits = 0
        self.misses = 0
        self.used = time.monotonic()

    # -- Wege -----------------------------------------------------------------------------

    def trace(self, key: tuple, compute):
        """Weg aus dem Speicher (eigene Kopie) bzw. neu gerechnet und gemerkt."""
        full = (lang(), *key)
        with self.lock:
            packed = self.traces.get(full)
            if packed is not None:
                self.traces.move_to_end(full)
                self.hits += 1
        if packed is not None:
            return pickle.loads(packed)  # noqa: S301 - eigene Daten aus dem Speicher, nie von aussen
        result = compute()
        try:
            packed = pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception:  # nicht packbar: nicht merken, Ergebnis gilt trotzdem
            log.debug("Weg nicht gemerkt (nicht packbar)", exc_info=True)
            return result
        with self.lock:
            self.misses += 1
            if full not in self.traces:
                self.traces[full] = packed
                self.size += len(packed)
                limit = get_settings().path_store_mb * 1024 * 1024
                while self.size > limit and self.traces:
                    _old, dropped = self.traces.popitem(last=False)
                    self.size -= len(dropped)
        return result

    def decisions_for(self) -> dict:
        """Urteile je Hop in der aktuellen Sprache (Texte sind uebersetzt)."""
        return self.decisions.setdefault(lang(), {})

    def stats(self) -> dict:
        return {"traces": len(self.traces), "bytes": self.size, "hits": self.hits,
                "misses": self.misses}  # fmt: skip


_stores: dict[int, Store] = {}
_guard = threading.Lock()
_current: ContextVar[Store | None] = ContextVar("automato_path_store", default=None)


def store_for(session: Session, tenant_id: int) -> Store:
    """Speicher des Mandanten zum aktuellen Datenstand (neu, wenn sich der Stand geaendert hat)."""
    from app.store import doc_snapshots  # spaet: doc_snapshots -> app_paths -> paths

    key = (__version__, repr(doc_snapshots._tenant_parts(session, tenant_id)),
           get_settings().app_traces_max)  # fmt: skip
    now = time.monotonic()
    with _guard:
        for tid, s in list(_stores.items()):  # lange unbenutzte Speicher freigeben
            if tid != tenant_id and now - s.used > IDLE_SECONDS:
                del _stores[tid]
        store = _stores.get(tenant_id)
        if store is None or store.key != key:
            if store is not None:
                log.info("Mandant %s: Wege-Speicher neu (Datenstand geaendert; %s)", tenant_id,
                         store.stats())  # fmt: skip
            store = _stores[tenant_id] = Store(tenant_id, key)
        store.used = now
        return store


@contextmanager
def active(session: Session, tenant_id: int) -> Iterator[Store]:
    """Speicher des Mandanten fuer die Dauer des Blocks setzen (Doku, Verbindungen)."""
    current = _current.get()
    if current is not None and current.tenant_id == tenant_id:
        yield current  # schon gesetzt (z. B. Doku innerhalb der Verbindungen)
        return
    store = store_for(session, tenant_id)
    token = _current.set(store)
    try:
        yield store
    finally:
        _current.reset(token)


def current(tenant_id: int) -> Store | None:
    """Gesetzter Speicher dieses Mandanten - None ausserhalb von `active`."""
    store = _current.get()
    return store if store is not None and store.tenant_id == tenant_id else None


def clear() -> None:
    """Alle Speicher verwerfen (Tests)."""
    with _guard:
        _stores.clear()
