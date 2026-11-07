"""Ein Einspielen zugleich und ein Schreiber des abgeleiteten Zustands je Mandant (0.80.1).

Befund des Anwenders: „Einspielen fehlgeschlagen: DeadlockDetected … relation rule_index“ und
HTTP 500 beim Aktualisieren der letzten Laeufe. Beim Einlesen aller Geraete kamen mehrere
Ergebnisse zugleich an; jedes baute danach den Zustand des ganzen Mandanten neu auf (Findings,
Regelindex, Zwillinge paralleler Firewalls, Applikations-Befunde, Kunden) - zwei davon
sperrten sich gegenseitig die Zeilen. Dazu hielt jedes wartende Einspielen eine Verbindung aus
dem Pool, bis die Liste der Laeufe keine mehr bekam.

- `slot(run_id)`: Ergebnisse werden im Prozess nacheinander eingespielt; wer wartet, belegt
  noch keine Datenbank-Verbindung. Die Liste der Laeufe zeigt, welche Ergebnisse warten bzw.
  gerade eingespielt werden, und beendet sie nicht als haengend.
- `hold(session, tenant_id)`: Sperre des Mandanten in Postgres bis zum Ende der Transaktion
  (`pg_advisory_xact_lock`) - auch ueber Prozesse hinweg und fuer „Abgleich“, Paare,
  Applikationen und Kunden, die denselben Zustand neu schreiben. Mehrfach in derselben
  Transaktion ist gefahrlos. SQLite (Tests) braucht sie nicht.
- `transient(exc)`: Deadlock bzw. Serialisierungsfehler - das Einspielen wird wiederholt.
"""

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

NAMESPACE = 0x4175  # erster Schluessel der Sperre („Au“), zweiter = Mandant
WAIT_SECONDS = 30 * 60  # danach ohne Reihenfolge weiter (die Sperre in Postgres bleibt)
TRANSIENT = {"40P01", "40001"}  # deadlock_detected, serialization_failure
ATTEMPTS = 3

_gate = threading.Lock()
_state = threading.Lock()
_waiting: dict[str, float] = {}  # Lauf -> seit (monotonic)
_active: dict[str, float] = {}


@contextmanager
def slot(run_id: str) -> Iterator[None]:
    """Ergebnis des Laufs einspielen, sobald kein anderes mehr eingespielt wird."""
    with _state:
        _waiting[run_id] = time.monotonic()
    got = _gate.acquire(timeout=WAIT_SECONDS)
    if not got:
        log.warning("Lauf %s: wartet seit %d min aufs Einspielen - weiter", run_id,
                    WAIT_SECONDS // 60)  # fmt: skip
    with _state:
        _waiting.pop(run_id, None)
        _active[run_id] = time.monotonic()
    try:
        yield
    finally:
        with _state:
            _active.pop(run_id, None)
        if got:
            _gate.release()


def state(run_id: str) -> str | None:
    """`waiting` (Ergebnis angekommen, wartet), `active` (wird eingespielt) bzw. None."""
    with _state:
        if run_id in _active:
            return "active"
        return "waiting" if run_id in _waiting else None


def waiting_before(run_id: str) -> int:
    """Wie viele Ergebnisse vor diesem warten bzw. eingespielt werden."""
    with _state:
        since = _waiting.get(run_id)
        if since is None:
            return 0
        return len(_active) + sum(1 for t in _waiting.values() if t < since)


def hold(session: Session, tenant_id: int | None) -> None:
    """Mandanten bis zum Ende der Transaktion sperren (nur Postgres)."""
    if tenant_id is None or session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :tid)"), {"ns": NAMESPACE, "tid": tenant_id}
    )


def try_hold(session: Session, tenant_id: int | None) -> bool:
    """Wie `hold`, aber ohne zu warten (Zeitplan, ADR 0029): False, wenn gerade ein anderer
    den Mandanten sperrt - dann ein andermal."""
    if tenant_id is None or session.get_bind().dialect.name != "postgresql":
        return True
    return bool(
        session.execute(
            text("SELECT pg_try_advisory_xact_lock(:ns, :tid)"),
            {"ns": NAMESPACE, "tid": tenant_id},
        ).scalar()
    )


def transient(exc: BaseException) -> bool:
    """Deadlock bzw. Serialisierungsfehler der Datenbank - ein neuer Versuch hilft."""
    orig = getattr(exc, "orig", None) or exc
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return code in TRANSIENT
