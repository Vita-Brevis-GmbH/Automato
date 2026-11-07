"""Prüfungen der Findings als steckbare Bausteine (0.110.0, ADR 0039).

Anwender 2026-10-29 (Roadmap Version 1 Nr. 1): „Findings als steckbare Prüfungen“. Je Prüfung
eine Datei in einem Paket je Modul - `app/checks/<modul>/<prüfung>.py` mit `CHECK = Check(...)`:
Schlüssel, Modul (ADR 0036), Titel und Zweck (deutsch, englisch), die Befund-Typen und die
Funktion `run(session, tenant_id, cx) -> list[Issue]`. Eine neue Prüfung ist eine neue Datei;
der Abgleich (`store/reconcile`) findet sie selbst und muss nicht geändert werden.

- **Durchgang:** alle mit `in_reconcile` laufen im Abgleich nach jedem Lauf bzw. „Neu
  abgleichen“; mit `step` (`zonen`, `quellen`, `applikationen`) ausserdem bzw. nur in einem
  eigenen Teil-Durchgang (Speichern einer Zone, Zeitplan, Applikationen im Hintergrund) - dort
  rechnet nur diese Prüfung.
- **Je Mandant abschaltbar** (`tenant_check`, Admin, Audit): eine ausgeschaltete Prüfung läuft
  nicht, ihre Befunde fallen beim nächsten Abgleich weg. Ist ihr Modul aus, läuft sie auch nicht.
- **Laufzeit und Anzahl** der letzten Durchgänge je Mandant hält der Prozess (`stats`).
- Eine Prüfung liefert nur Befunde ihrer Typen (`STRICT` in den Tests prüft das).

Nur lesend gegenüber allen Quellsystemen; kein neuer Schreibweg.
"""

import importlib
import logging
import pkgutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import FindingType

log = logging.getLogger(__name__)

STEPS = ("zonen", "quellen", "applikationen")
STRICT = False  # Tests: Befund eines fremden Typs ist ein Fehler (sonst nur Warnung)


@dataclass
class Context:
    """Gemeinsame Eingaben eines Durchgangs (einmal gerechnet, von mehreren Prüfungen genutzt)."""

    refs: object | None = None  # references.context
    prefixes: object | None = None  # Präfixe der Namenskonvention
    settings: object | None = None
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Check:
    key: str
    module: str  # Modul nach ADR 0036 (app/modules.py)
    title: tuple[str, str]  # deutsch, englisch
    purpose: tuple[str, str]
    types: frozenset[FindingType]
    run: Callable[[Session, int, Context], list] | None
    step: str | None = None  # eigener Teil-Durchgang (STEPS)
    in_reconcile: bool = True  # läuft im Abgleich nach jedem Lauf
    order: int = 100

    def out(self) -> dict:
        return {
            "key": self.key,
            "module": self.module,
            "title": L(*self.title),
            "purpose": L(*self.purpose),
            "types": sorted(t.value for t in self.types),
            "step": self.step,
            "in_reconcile": self.in_reconcile,
        }


_registry: list[Check] | None = None
_stats: dict[tuple[int, str], dict] = {}  # (Mandant, Prüfung) -> {ran_at, ms, count}


def all_checks() -> list[Check]:
    """Alle Prüfungen aus `app/checks/<modul>/<prüfung>.py` (geordnet nach `order`, Schlüssel)."""
    global _registry
    if _registry is None:
        found: list[Check] = []
        for pkg in pkgutil.iter_modules(__path__):
            if not pkg.ispkg:
                continue
            sub = importlib.import_module(f"{__name__}.{pkg.name}")
            for mod in pkgutil.iter_modules(sub.__path__):
                check = getattr(importlib.import_module(f"{sub.__name__}.{mod.name}"), "CHECK",
                                None)  # fmt: skip
                if isinstance(check, Check):
                    found.append(check)
        _registry = sorted(found, key=lambda c: (c.order, c.key))
    return list(_registry)


def by_key(key: str) -> Check | None:
    return next((c for c in all_checks() if c.key == key), None)


def types_of(step: str) -> frozenset[FindingType]:
    """Befund-Typen eines Teil-Durchgangs (`zonen`, `quellen`, `applikationen`)."""
    out: set[FindingType] = set()
    for c in all_checks():
        if c.step == step:
            out |= c.types
    return frozenset(out)


def _rows(session: Session, tenant_id: int) -> dict[str, bool]:
    from app.models import TenantCheck  # spaet: Modelle laden app.db

    key = ("checks", tenant_id)
    if key not in session.info:
        session.info[key] = dict(session.execute(
            select(TenantCheck.check, TenantCheck.enabled)
            .where(TenantCheck.tenant_id == tenant_id)).all())  # fmt: skip
    return session.info[key]


def own_state(session: Session, tenant_id: int | None, key: str) -> bool:
    """Eigene Schaltung der Prüfung (ohne Modul); ohne Mandant an."""
    return True if tenant_id is None else _rows(session, tenant_id).get(key, True)


def enabled(session: Session, tenant_id: int | None, check: Check) -> bool:
    """Läuft die Prüfung für den Mandanten: eingeschaltet und ihr Modul an."""
    from app import modules

    return own_state(session, tenant_id, check.key) and modules.enabled(session, tenant_id,
                                                                         check.module)  # fmt: skip


def active(session: Session, tenant_id: int, step: str | None = None) -> list[Check]:
    """Laufende Prüfungen: ohne `step` die des Abgleichs, sonst die des Teil-Durchgangs."""
    return [c for c in all_checks() if c.run is not None
            and (c.step == step if step else c.in_reconcile)
            and enabled(session, tenant_id, c)]  # fmt: skip


def switch(session: Session, tenant_id: int, key: str, on: bool, actor: str) -> None:
    """Prüfung für den Mandanten ein- bzw. ausschalten (Admin, Audit beim Aufrufer)."""
    from app.models import TenantCheck

    if by_key(key) is None:
        raise KeyError(key)
    row = session.get(TenantCheck, (tenant_id, key))
    if on:
        if row is not None:
            session.delete(row)  # Vorgabe: an
    elif row is None:
        session.add(TenantCheck(tenant_id=tenant_id, check=key, enabled=False, changed_by=actor))
    else:
        row.enabled, row.changed_by, row.changed_at = False, actor, datetime.now(UTC)
    session.flush()
    session.info.pop(("checks", tenant_id), None)
    _stats.pop((tenant_id, key), None)  # Dauer und Anzahl gelten fuer den naechsten Durchgang


def run(check: Check, session: Session, tenant_id: int, cx: Context) -> list:
    """Prüfung ausführen, Dauer und Anzahl merken; Befunde fremder Typen melden."""
    start = time.monotonic()
    issues = list(check.run(session, tenant_id, cx) or [])
    foreign = sorted({i.type.value for i in issues} - {t.value for t in check.types})
    if foreign:
        message = f"Prüfung {check.key} liefert fremde Befund-Typen: {', '.join(foreign)}"
        if STRICT:
            raise ValueError(message)
        log.warning(message)
    _stats[(tenant_id, check.key)] = {
        "ran_at": datetime.now(UTC).isoformat(),
        "ms": round((time.monotonic() - start) * 1000),
        "count": len({i.key for i in issues}),
    }
    return issues


def stats(tenant_id: int, key: str) -> dict | None:
    return _stats.get((tenant_id, key))


def note(tenant_id: int, key: str, ms: float, count: int) -> None:
    """Dauer und Anzahl einer Prüfung, die ihren eigenen Durchgang hat (Applikationen)."""
    _stats[(tenant_id, key)] = {"ran_at": datetime.now(UTC).isoformat(), "ms": round(ms),
                                "count": count}  # fmt: skip


__all__ = ["STEPS", "Check", "Context", "active", "all_checks", "by_key", "enabled", "note",
           "own_state", "run", "stats", "switch", "types_of"]  # fmt: skip
