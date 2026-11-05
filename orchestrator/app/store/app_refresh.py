"""Nach dem Aendern einer Applikation nachziehen - im Hintergrund (0.74.1).

Die Abweichungen Regel <-> Weg (`reconcile.refresh_app_findings`, 0.59.0) und die Findings der
Kunden (`customers.derive_findings`) brauchten bei grossen Umgebungen Sekunden bis Minuten; das
Speichern wartete darauf (Anwender: „es speichert die Werte nicht sofort, Wartezeiten von
3–5 min“). Jetzt ist die Aenderung sofort gespeichert, die Antwort kommt danach, und das
Nachziehen laeuft mit eigener Session hinter der Antwort.

Mehrere Aenderungen kurz hintereinander werden zusammengefasst: ist fuer die Applikation schon
ein Durchgang vorgemerkt, der noch nicht begonnen hat, kommt kein weiterer dazu. Je Mandant
laeuft hoechstens ein Durchgang zugleich (im Prozess), damit sich die Befunde nicht ueberholen.

Seit 0.108.0 (Anwender: „das Speichern dauert zu lange“) ebenso die Befunde der Zonen nach dem
Speichern einer Zone, des Netzwerkdesigns bzw. des Grundschemas (`schedule_zones`) und die
Befunde nach einem geaenderten Verbund paralleler Firewalls (`schedule_regroup`); danach rechnet
`doc_snapshots` die Doku der zuletzt angesehenen Applikationen vor - seit 0.131.0 auch die
Routing-Filter (`bgp_cache`).
"""

import logging
import threading
from collections import defaultdict

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session, sessionmaker

log = logging.getLogger(__name__)

_lock = threading.Lock()
_pending: set[tuple] = set()  # (Mandant, Applikation; 0 = alle) bzw. (Art, Mandant)
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


def pending_count(tenant_id: int) -> int:
    """Ausstehende Durchgaenge (Befunde nachziehen) des Mandanten - fuer die Uebersicht."""
    with _lock:  # Schluessel (Mandant, Applikation) bzw. (Art, Mandant)
        return sum(1 for k in _pending if (k[1] if isinstance(k[0], str) else k[0]) == tenant_id)


def _claim(key: tuple) -> bool:
    with _lock:
        if key in _pending:
            return False
        _pending.add(key)
        return True


def _release(key: tuple) -> None:
    with _lock:
        _pending.discard(key)


def schedule_zones(background: BackgroundTasks, factory: sessionmaker[Session],
                   tenant_id: int) -> bool:  # fmt: skip
    """Befunde der Zonen (Absicht, ausserhalb des Schemas, Zonentyp) hinter der Antwort neu."""
    if not _claim(("zones", tenant_id)):
        return False
    background.add_task(run_zones, factory, tenant_id)
    return True


def run_zones(factory: sessionmaker[Session], tenant_id: int) -> None:
    from app.store import bgp_cache, doc_snapshots, reconcile  # spaet: reconcile -> zones

    with _tenant_locks[tenant_id]:
        _release(("zones", tenant_id))
        session = factory()
        try:
            reconcile.refresh_zone_findings(session, tenant_id)
            session.commit()
        except Exception:
            session.rollback()
            log.exception("Mandant %s: Befunde der Zonen fehlgeschlagen", tenant_id)
        finally:
            session.close()
    doc_snapshots.warm(factory, tenant_id)
    bgp_cache.warm(factory, tenant_id)  # 0.131.0: Routing-Filter


def schedule_regroup(background: BackgroundTasks, factory: sessionmaker[Session],
                     tenant_id: int) -> bool:  # fmt: skip
    """Verbund paralleler Firewalls geaendert (0.18.0): Befunde des Mandanten und der
    Applikationen (Verbund = ein Hop) hinter der Antwort neu; die Zwillinge im Regelindex
    (`parallel.mark`) setzt das Speichern selbst."""
    if not _claim(("regroup", tenant_id)):
        return False
    background.add_task(run_regroup, factory, tenant_id)
    return True


def run_regroup(factory: sessionmaker[Session], tenant_id: int) -> None:
    from app.store import bgp_cache, doc_snapshots, reconcile  # spaet: reconcile -> app_paths

    with _tenant_locks[tenant_id]:
        _release(("regroup", tenant_id))
        session = factory()
        try:
            reconcile.reconcile_tenant(session, tenant_id)
            reconcile.refresh_app_findings(session, tenant_id, derived=True)
            session.commit()
        except Exception:
            session.rollback()
            log.exception("Mandant %s: Befunde nach dem Verbund fehlgeschlagen", tenant_id)
        finally:
            session.close()
    doc_snapshots.warm(factory, tenant_id)
    bgp_cache.warm(factory, tenant_id)  # 0.131.0: Routing-Filter


def run_tenant(factory: sessionmaker[Session], tenant_id: int) -> None:
    # spaet: reconcile -> app_paths
    from app.store import bgp_cache, customers, doc_snapshots, reconcile

    with _tenant_locks[tenant_id]:
        with _lock:
            _pending.discard((tenant_id, 0))
        session = factory()
        try:
            reconcile.refresh_app_findings(session, tenant_id, derived=True)
            reconcile.refresh_zone_findings(session, tenant_id)
            customers.derive(session, tenant_id)
            session.commit()
        except Exception:
            session.rollback()
            log.exception("Mandant %s: Befunde nach L3Out-Zuordnung fehlgeschlagen", tenant_id)
        finally:
            session.close()
    doc_snapshots.warm(factory, tenant_id)
    bgp_cache.warm(factory, tenant_id)  # 0.131.0: Routing-Filter


def schedule_runs(background: BackgroundTasks, factory: sessionmaker[Session], tenant_id: int,
                  application_id: int | None = None, full: bool = True) -> bool:  # fmt: skip
    """Nach einem Lauf (0.139.0, Anwender: „Das Neuberechnen der Apps und Wege muss besser
    gesteuert werden“): die Abweichungen Regel <-> Weg der Applikationen - bis 0.138.0 bis zu
    `app_findings_seconds` im Einspielen jedes Laufs - danach Doku und Routing-Filter, alles
    hinter der Antwort. Zusammengefasst: steht schon ein Durchgang aus, der noch nicht begonnen
    hat, kommt keiner dazu - zehn Laeufe hintereinander rechnen so nicht zehnmal. Nach einem
    Lauf nur der Regeln einer Applikation (`application_id`) nur diese, ohne Routing-Filter
    (`full=False`)."""
    key = ("runs", tenant_id, application_id or 0)
    if not _claim(key):
        return False
    background.add_task(run_runs, factory, tenant_id, application_id, full)
    return True


def run_runs(factory: sessionmaker[Session], tenant_id: int, application_id: int | None = None,
             full: bool = True) -> None:  # fmt: skip
    from app.store import bgp_cache, doc_snapshots, reconcile  # spaet: reconcile -> app_paths

    with _tenant_locks[tenant_id]:
        _release(("runs", tenant_id, application_id or 0))  # ab hier: ein neuer Lauf = neu
        session = factory()
        try:
            reconcile.refresh_app_findings(session, tenant_id, application_id, derived=True)
            session.commit()
        except Exception:
            session.rollback()
            log.exception("Mandant %s: Befunde der Applikationen nach dem Lauf fehlgeschlagen",
                          tenant_id)  # fmt: skip
        finally:
            session.close()
    if application_id is not None:  # nur diese Applikation, gleich (auch waehrend eines Durchgangs)
        doc_snapshots.refresh_app(factory, tenant_id, application_id)
    else:
        doc_snapshots.warm(factory, tenant_id)
    if full:
        bgp_cache.warm(factory, tenant_id)  # 0.131.0: Routing-Filter


def pending(tenant_id: int, application_id: int) -> bool:
    with _lock:
        return (tenant_id, application_id) in _pending


def run(factory: sessionmaker[Session], tenant_id: int, application_id: int) -> None:
    from app.store import customers, doc_snapshots, reconcile  # spaet: reconcile -> app_paths

    with _tenant_locks[tenant_id]:
        with _lock:
            # ab hier fuehrt eine weitere Aenderung zu einem neuen Durchgang
            _pending.discard((tenant_id, application_id))
        session = factory()
        try:
            reconcile.refresh_app_findings(session, tenant_id, application_id, derived=True)
            customers.derive_findings(session, tenant_id)
            session.commit()
        except Exception:
            session.rollback()
            log.exception("Applikation %s: Befunde nachziehen fehlgeschlagen", application_id)
        finally:
            session.close()
    # 0.139.0: gleich, ohne auf einen Durchgang des Mandanten zu warten bzw. einen anzustossen
    doc_snapshots.refresh_app(factory, tenant_id, application_id)
