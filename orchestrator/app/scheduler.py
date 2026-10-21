"""Zeitgesteuerte Laeufe: legt zu jedem faelligen Termin eines Quellsystems einen Lauf an.

Jeder Orchestrator-Prozess darf die Schleife fahren: ein Termin wird per bedingtem UPDATE
auf `source_system.schedule_last_at` beansprucht, nur der Prozess mit dem Treffer legt den
Lauf an. Nach einem Ausfall wird nur der letzte verpasste Termin nachgeholt (kein Rueckstau).
Wartet oder laeuft fuer das Quellsystem schon ein Lauf (z. B. Agent offline), faellt der
Termin aus. Der Lauf selbst entsteht wie bei "Jetzt einlesen" (`store.runs`).
"""

import logging
import threading
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from app import schedule
from app.jobs import celery_app
from app.models import SourceSystem
from app.store import runs

log = logging.getLogger(__name__)
ACTOR = "system:scheduler"
_last_tick: datetime | None = None  # letzter Durchgang dieses Prozesses (Betriebszustand)


def last_tick() -> datetime | None:
    return _last_tick


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _claim(session: Session, src: SourceSystem, slot: datetime) -> bool:
    """Termin fuer dieses Quellsystem beanspruchen (genau ein Prozess gewinnt)."""
    result = session.execute(
        update(SourceSystem)
        .where(
            SourceSystem.id == src.id,
            or_(SourceSystem.schedule_last_at.is_(None), SourceSystem.schedule_last_at < slot),
        )
        .values(schedule_last_at=slot)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def next_run(expr: str | None, tz: str, now: datetime | None = None) -> datetime | None:
    """Naechster Termin (UTC) eines Zeitplans; None ohne/bei ungueltigem Zeitplan."""
    if not expr:
        return None
    try:
        cron = schedule.parse(expr)
    except ValueError:
        return None
    nxt = cron.next_after((now or datetime.now(UTC)).astimezone(ZoneInfo(tz)))
    return nxt.astimezone(UTC) if nxt else None


def tick(factory: sessionmaker, tz: str, now: datetime | None = None) -> list[str]:
    """Ein Durchgang: faellige Termine ausloesen. Rueckgabe = IDs der angelegten Laeufe."""
    now = _utc(now) or datetime.now(UTC)
    zone = ZoneInfo(tz)
    created: list[str] = []
    with factory() as session:
        sources = list(
            session.scalars(
                select(SourceSystem).where(
                    SourceSystem.schedule.is_not(None),
                    SourceSystem.enabled.is_(True),
                    SourceSystem.tenant_id.is_not(None),
                )
            )
        )
        for src in sources:
            try:
                cron = schedule.parse(src.schedule or "")
            except ValueError:
                continue  # beim Speichern geprueft; Altbestand still ueberspringen
            last = _utc(src.schedule_last_at)
            if last is None:  # neuer Zeitplan: ab jetzt zaehlen, nicht nachholen
                _claim(session, src, now)
                session.commit()
                continue
            slot = cron.previous(now.astimezone(zone))
            if slot is None or slot.astimezone(UTC) <= last:
                continue
            if not _claim(session, src, slot.astimezone(UTC)):
                session.rollback()
                continue  # anderer Prozess war schneller
            if runs.busy(session, src.id):
                session.commit()
                log.info("Zeitplan %s: Lauf ausgelassen, es laeuft/wartet schon einer", src.name)
                continue
            try:
                run = runs.queue_collect(
                    session,
                    platform=src.platform.value,
                    tenant_id=src.tenant_id,
                    source_system_id=src.id,
                    execution="inprocess",
                    actor=ACTOR,
                    trigger="schedule",
                )
            except runs.RunRejected as exc:
                session.commit()
                log.warning("Zeitplan %s: kein Lauf (%s)", src.name, exc)
                continue
            session.commit()
            if run.execution == "inprocess":
                celery_app.dispatch_collect(run.platform, run.id)
            created.append(run.id)
            log.info("Zeitplan %s: Lauf %s angelegt (%s)", src.name, run.id, run.execution)
    return created


def start(factory: sessionmaker, tz: str, interval: int) -> threading.Event:
    """Hintergrund-Schleife starten; das Event beendet sie."""
    ZoneInfo(tz)  # ungueltige Zeitzone sofort melden, nicht erst im Thread
    stop = threading.Event()

    def loop() -> None:
        global _last_tick
        _last_tick = datetime.now(UTC)  # Start zaehlt als Lebenszeichen
        while not stop.wait(interval):
            try:
                tick(factory, tz)
                _last_tick = datetime.now(UTC)
            except Exception:  # Schleife darf nicht sterben (DB kurz weg o. ae.)
                log.exception("Zeitplan-Durchgang fehlgeschlagen")

    threading.Thread(target=loop, name="automato-scheduler", daemon=True).start()
    log.info("Zeitplan aktiv (alle %s s, Zeitzone %s)", interval, tz)
    return stop
