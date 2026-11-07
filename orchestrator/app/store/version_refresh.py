"""Befunde nach einem Update neu (0.133.0).

Anwender 2026-11-04: „Ich sehe nun bei den BGP Filter, das es viel besser ist, aber ich habe immer
noch die gleiche Anzahl der Findings“ - die Ansichten rechnen mit der neuen Version, die Befunde
entstehen aber nur nach Läufen und Änderungen. Bis zum nächsten Lauf standen sie auf dem Stand
der alten Version.

Kurz nach dem Start (mit dem Zeitplan) rechnet der Orchestrator deshalb je Mandant einmal den
ganzen Abgleich und die Befunde der Applikationen neu, sobald die Version eine andere ist. Gemerkt
wird das im Audit-Log (`reconcile.version`, Subjekt `tenant:<id>@<Version>`; die Aktion zählt
nicht für den Stand der Doku). Gesperrte Mandanten (ein Lauf schreibt gerade) kommen danach dran.

0.133.2: danach (einmal je Start) rechnet ein eigener Thread die Doku aller Mandanten vor
(`doc_snapshots.warm_all`) - nach einem Neustart bzw. Update rechnete sonst bis zum nächsten Lauf
nichts vor.
"""

import logging
import threading

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import __version__, audit
from app.models import AuditLog, Tenant

log = logging.getLogger(__name__)

ACTION = "reconcile.version"
DELAY = 30  # Sekunden nach dem Start: erst Anfragen bedienen
RETRY = 300  # gesperrte Mandanten danach erneut
ROUNDS = 12  # hoechstens so oft (eine Stunde)


def subject(tenant_id: int) -> str:
    return f"tenant:{tenant_id}@{__version__}"


def done(session: Session, tenant_id: int) -> bool:
    """Befunde des Mandanten schon mit dieser Version gerechnet."""
    stmt = select(AuditLog.id).where(AuditLog.action == ACTION,
                                     AuditLog.subject == subject(tenant_id))  # fmt: skip
    return session.scalar(stmt.limit(1)) is not None


def refresh(factory: sessionmaker[Session]) -> tuple[list[int], list[int]]:
    """Je Mandant, der noch nicht mit dieser Version gerechnet ist: Abgleich und Befunde der
    Applikationen neu. -> (neu gerechnet, gesperrt bzw. fehlgeschlagen - spaeter erneut)."""
    from app.store import reconcile, tenant_lock  # spaet: reconcile -> app_paths -> ...

    with factory() as session:
        tenants = list(session.scalars(select(Tenant.id).order_by(Tenant.id)))
    fresh, later = [], []
    for tid in tenants:
        with factory() as session:
            if done(session, tid):
                continue
            if not tenant_lock.try_hold(session, tid):
                later.append(tid)  # ein Lauf schreibt gerade: danach
                continue
            try:
                counts = reconcile.reconcile_tenant(session, tid)
                # 0.139.1: mit dem Stand der Doku kommen sie beim Vorrechnen (derived)
                reconcile.refresh_app_findings(session, tid, derived=True)
                audit.record(session, actor="system", action=ACTION, subject=subject(tid),
                             detail={"version": __version__,
                                     "findings": sum(counts.values())})  # fmt: skip
                session.commit()
                fresh.append(tid)
                log.info("Mandant %s: Befunde mit Version %s neu gerechnet", tid, __version__)
            except Exception:
                session.rollback()
                later.append(tid)
                log.exception("Mandant %s: Befunde nach dem Update fehlgeschlagen", tid)
    return fresh, later


def start(factory: sessionmaker[Session], stop: threading.Event | None = None) -> threading.Thread:
    """Im Hintergrund kurz nach dem Start; gesperrte Mandanten alle `RETRY` Sekunden erneut."""

    def loop() -> None:
        from app.store import doc_snapshots  # spaet: doc_snapshots -> app_paths -> ...

        wait, warming = DELAY, False
        for _ in range(ROUNDS):
            if stop is not None and stop.wait(wait):
                return
            if stop is None:
                threading.Event().wait(wait)
            try:
                _fresh, later = refresh(factory)
            except Exception:  # z. B. Datenbank noch nicht migriert
                log.exception("Befunde nach dem Update nicht neu gerechnet")
                later = [0]
            if not warming:  # Doku vorrechnen (0.133.2) - eigener Thread, kann lange dauern
                warming = True
                threading.Thread(target=doc_snapshots.warm_all, args=(factory,),
                                 name="automato-doc-warm", daemon=True).start()  # fmt: skip
            if not later:
                return
            wait = RETRY

    thread = threading.Thread(target=loop, name="automato-version-refresh", daemon=True)
    thread.start()
    return thread
