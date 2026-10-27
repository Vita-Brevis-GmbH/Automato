"""Nach dem Aendern einer Applikation nachziehen - im Hintergrund (0.74.1).

Die Abweichungen Regel <-> Weg (`reconcile.refresh_app_findings`, 0.59.0) und die Findings der
Kunden (`customers.derive_findings`) brauchten bei grossen Umgebungen Sekunden bis Minuten; das
Speichern wartete darauf (Anwender: „es speichert die Werte nicht sofort, Wartezeiten von
3–5 min“). Jetzt ist die Aenderung sofort gespeichert, die Antwort kommt danach, und das
Nachziehen laeuft mit eigener Session hinter der Antwort.

Mehrere Aenderungen kurz hintereinander werden zusammengefasst: ist fuer die Applikation schon
ein Durchgang vorgemerkt, der noch nicht begonnen hat, kommt kein weiterer dazu. Je Mandant
laeuft hoechstens ein Durchgang zugleich (im Prozess), damit sich die Befunde nicht ueberholen.
"""

import logging
import threading
from collections import defaultdict

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session, sessionmaker

log = logging.getLogger(__name__)

_lock = threading.Lock()
_pending: set[tuple[int, int]] = set()
_tenant_locks: dict[int, threading.Lock] = defaultdict(threading.Lock)


def schedule(background: BackgroundTasks, factory: sessionmaker[Session], tenant_id: int,
             application_id: int) -> bool:  # fmt: skip
    """Nachziehen fuer die Applikation vormerken; False, wenn schon eines aussteht."""
    key = (tenant_id, application_id)
    with _lock:
        if key in _pending:
            return False
        _pending.add(key)
    background.add_task(run, factory, tenant_id, application_id)
    return True


def schedule_tenant(background: BackgroundTasks, factory: sessionmaker[Session],
                    tenant_id: int) -> bool:  # fmt: skip
    """Alles nachziehen, was vom Weg ueber die L3Outs abhaengt (ADR 0034: Firewall hinter einem
    L3Out von Hand gepflegt) - Befunde der Applikationen und Zonen, Zugehoerigkeit der Kunden;
    wie oben hinter der Antwort. Schluessel (Mandant, 0) = alle Applikationen."""
    key = (tenant_id, 0)
    with _lock:
        if key in _pending:
            return False
        _pending.add(key)
    background.add_task(run_tenant, factory, tenant_id)
    return True


def run_tenant(factory: sessionmaker[Session], tenant_id: int) -> None:
    from app.store import customers, reconcile  # spaet: reconcile -> app_paths -> applications

    with _tenant_locks[tenant_id]:
        with _lock:
            _pending.discard((tenant_id, 0))
        session = factory()
        try:
            reconcile.refresh_app_findings(session, tenant_id)
            reconcile.refresh_zone_findings(session, tenant_id)
            customers.derive(session, tenant_id)
            session.commit()
        except Exception:
            session.rollback()
            log.exception("Mandant %s: Befunde nach L3Out-Zuordnung fehlgeschlagen", tenant_id)
        finally:
            session.close()


def pending(tenant_id: int, application_id: int) -> bool:
    with _lock:
        return (tenant_id, application_id) in _pending


def run(factory: sessionmaker[Session], tenant_id: int, application_id: int) -> None:
    from app.store import customers, reconcile  # spaet: reconcile -> app_paths -> applications

    with _tenant_locks[tenant_id]:
        with _lock:
            # ab hier fuehrt eine weitere Aenderung zu einem neuen Durchgang
            _pending.discard((tenant_id, application_id))
        session = factory()
        try:
            reconcile.refresh_app_findings(session, tenant_id, application_id)
            customers.derive_findings(session, tenant_id)
            session.commit()
        except Exception:
            session.rollback()
            log.exception("Applikation %s: Befunde nachziehen fehlgeschlagen", application_id)
        finally:
            session.close()
