"""Exporte im Hintergrund (1.21.0, ADR 0069).

Anwender 2026-11-18: „Zudem geht es sehr lange, bis der Export steht, könnte man dies als
Hintergrunddienst laufen lassen?“ - der Export der Applikations-Doku (je Applikation ein ZIP mit
dem Export-Passwort) rechnete in der Anfrage, bei vielen Applikationen minutenlang.

- **Auftrag:** die Anfrage legt eine Zeile `export_job` an und kehrt sofort zurück; der Auftrag
  rechnet in einem Thread des Orchestrators (`start`), höchstens `export_parallel` zugleich -
  weitere warten. Fortschritt (n von m, aktuelle Applikation) und Fehler je Applikation stehen
  in der Zeile, die Oberfläche fragt sie ab - auch nach dem Neuladen.
- **Export-Passwort nur im Speicher:** es kommt mit der Anfrage, lebt nur in der Arbeit des
  Threads und verschlüsselt dort jede Datei; nie in Datenbank, Redis, Protokoll, Audit oder
  Datei. Fertige Datei = ein ZIP (ohne Kompression) mit den verschlüsselten ZIPs je Applikation.
- **Ablage:** `export_dir` (Volume orchestrator-data; der Container schreibt sonst nirgends),
  `<id>.part` während des Rechnens, danach `<id>.zip`; höchstens `export_max_mb` je Export.
- **Nur der Auftraggeber** (`owner` = Principal.subject) sieht, bricht ab, lädt herunter und
  entfernt seinen Auftrag; alle anderen bekommen 404.
- **Ablauf:** `export_hours` nach dem Ende (bzw. dem Anlegen) löscht `purge` Zeile und Datei -
  beim Start, alle 10 Minuten (`start_cleanup`) und bei jeder Liste; Dateien ohne Auftrag ebenso.
- **Neustart:** ein wartender bzw. laufender Auftrag gilt als abgebrochen (`abort_interrupted`,
  sichtbar mit Grund) - das Passwort ist mit dem Prozess weg, neu starten muss der Benutzer.

Rein lesend gegenüber den Systemen; ein Prozess je Datenbank (siehe Dockerfile).
"""

import logging
import re
import threading
import uuid
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.i18n import L
from app.models import ExportJob

log = logging.getLogger(__name__)

WAITING, RUNNING, DONE, FAILED, CANCELLED, ABORTED = (
    "waiting", "running", "done", "failed", "cancelled", "aborted")  # fmt: skip
ACTIVE = (WAITING, RUNNING)
# Gruende (gespeichert als Schluessel, uebersetzt bei der Ausgabe)
RESTART, TOO_LARGE, STORAGE, ERROR, BY_USER = "restart", "too_large", "storage", "error", "user"
CLEANUP_SECONDS = 600
_NAME = re.compile(r"^[0-9a-f]{32}\.(zip|part)$")

_lock = threading.Lock()
_cancel: dict[str, threading.Event] = {}  # laufende bzw. wartende Auftraege dieses Prozesses
_threads: dict[str, threading.Thread] = {}
_slots: threading.BoundedSemaphore | None = None


class Cancelled(Exception):
    """Vom Benutzer abgebrochen bzw. der Auftrag ist weg (Mandant geloescht)."""


class TooLarge(Exception):
    """Grenze `export_max_mb` ueberschritten."""


def now() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def folder() -> Path:
    return Path(get_settings().export_dir)


def file_of(job_id: str) -> Path:
    return folder() / f"{job_id}.zip"


def _part(job_id: str) -> Path:
    return folder() / f"{job_id}.part"


def _ttl() -> timedelta:
    return timedelta(hours=max(1, get_settings().export_hours))


def create(session: Session, kind: str, owner: str, tenant_id: int, total: int,
           filename: str) -> ExportJob:  # fmt: skip
    """Auftrag anlegen (wartet, bis der Thread ihn aufnimmt)."""
    at = now()
    job = ExportJob(id=uuid.uuid4().hex, kind=kind, owner=owner, tenant_id=tenant_id,
                    status=WAITING, total=total, done=0, errors=[], filename=filename,
                    created_at=at, expires_at=at + _ttl())  # fmt: skip
    session.add(job)
    session.flush()
    return job


def active_of(session: Session, owner: str) -> ExportJob | None:
    """Wartender bzw. laufender Auftrag des Benutzers (einer zugleich)."""
    return session.scalar(
        select(ExportJob).where(ExportJob.owner == owner, ExportJob.status.in_(ACTIVE)).limit(1)
    )


def mine(session: Session, owner: str, tenant_id: int | None = None,
         kind: str | None = None) -> list[ExportJob]:  # fmt: skip
    stmt = select(ExportJob).where(ExportJob.owner == owner)
    if tenant_id is not None:
        stmt = stmt.where(ExportJob.tenant_id == tenant_id)
    if kind is not None:
        stmt = stmt.where(ExportJob.kind == kind)
    return list(session.scalars(stmt.order_by(ExportJob.created_at.desc(), ExportJob.id)))


def own(session: Session, job_id: str, owner: str) -> ExportJob | None:
    """Auftrag nur fuer den Auftraggeber (sonst None - die API antwortet 404)."""
    job = session.get(ExportJob, job_id) if re.fullmatch(r"[0-9a-f]{32}", job_id or "") else None
    return job if job is not None and job.owner == owner else None


def cancelling(job_id: str) -> bool:
    with _lock:
        event = _cancel.get(job_id)
    return event is not None and event.is_set()


def running_here(job_id: str) -> bool:
    """Rechnet bzw. wartet der Auftrag in diesem Prozess?"""
    with _lock:
        return job_id in _cancel


def cancel(job_id: str) -> bool:
    """Abbrechen anstossen - der Thread beendet nach der aktuellen Applikation."""
    with _lock:
        event = _cancel.get(job_id)
    if event is None:
        return False
    event.set()
    return True


def reason_text(job: ExportJob) -> str | None:
    """Grund eines Fehlers bzw. Abbruchs in der Sprache der Anfrage."""
    limit = get_settings().export_max_mb
    texts = {
        RESTART: L("Der Orchestrator wurde neu gestartet – Export abgebrochen, bitte neu starten "
                   "(das Export-Passwort wird nicht gespeichert)",
                   "The orchestrator was restarted – export aborted, please start it again "
                   "(the export password is not stored)"),
        TOO_LARGE: L(f"Export grösser als {limit} MB – weniger Applikationen wählen",
                     f"Export larger than {limit} MB – select fewer applications"),
        STORAGE: L("Ablage für Exporte nicht beschreibbar – Protokoll des Orchestrators",
                   "Export storage not writable – see the orchestrator log"),
        ERROR: L("Export fehlgeschlagen – Protokoll des Orchestrators",
                 "Export failed – see the orchestrator log"),
        BY_USER: L("vom Benutzer abgebrochen", "cancelled by the user"),
    }  # fmt: skip
    return texts.get(job.message or "", job.message)


class Progress:
    """Fortschritt eines Auftrags: n von m, aktuelle Applikation, Fehler je Applikation - in der
    Zeile des Auftrags (Commit je Schritt, die Oberflaeche fragt ab). Prueft je Schritt, ob
    abgebrochen wurde."""

    def __init__(self, session: Session, job_id: str, event: threading.Event):
        self.session, self.job_id, self.event = session, job_id, event
        self.errors: list[dict] = []

    def step(self, done: int, current: str | None) -> None:
        if self.event.is_set():
            raise Cancelled
        job = self.session.get(ExportJob, self.job_id)
        if job is None:  # Mandant geloescht bzw. entfernt
            raise Cancelled
        job.done, job.current = done, (current or "")[:200] or None
        job.errors = list(self.errors)
        self.session.commit()

    def error(self, application_id: int, name: str, text: str) -> None:
        self.errors.append({"application_id": application_id, "name": name[:200],
                            "error": text[:300]})  # fmt: skip


class Bundle:
    """Fertige Datei: ein ZIP ohne Kompression (die Teile sind schon verschluesselte ZIPs) mit
    der Grenze `export_max_mb`."""

    def __init__(self, z: zipfile.ZipFile, limit: int):
        self.z, self.limit, self.size = z, limit, 0
        self.names: set[str] = set()

    def unique(self, name: str) -> str:
        stem, dot, ext = name.rpartition(".")
        stem, ext = (stem, f".{ext}") if dot else (name, "")
        out, n = name, 2
        while out.lower() in self.names:
            out, n = f"{stem}-{n}{ext}", n + 1
        self.names.add(out.lower())
        return out

    def add(self, name: str, data: bytes | str) -> None:
        body = data.encode() if isinstance(data, str) else data
        if self.size + len(body) > self.limit:
            raise TooLarge
        self.z.writestr(name, body, compress_type=zipfile.ZIP_STORED)
        self.size += len(body)


Work = Callable[[Session, Progress, Bundle], None]


def spawn(target: Callable[[], None], job_id: str) -> None:
    """Thread des Auftrags starten (Tests ersetzen das, um ihn gezielt laufen zu lassen)."""
    thread = threading.Thread(target=target, name=f"automato-export-{job_id[:8]}", daemon=True)
    with _lock:
        _threads[job_id] = thread
    thread.start()


def join(job_id: str, timeout: float | None = None) -> None:
    with _lock:
        thread = _threads.get(job_id)
    if thread is not None:
        thread.join(timeout)


def start(factory: sessionmaker[Session], job_id: str, work: Work) -> None:
    """Auftrag im Hintergrund rechnen lassen. `work` haelt das Export-Passwort - nur im Speicher
    dieses Threads; die Zeile `export_job` kennt es nie."""
    event = threading.Event()
    with _lock:
        _cancel[job_id] = event
    spawn(lambda: _run(factory, job_id, work, event), job_id)


def _semaphore() -> threading.BoundedSemaphore:
    global _slots
    with _lock:
        if _slots is None:
            _slots = threading.BoundedSemaphore(max(1, get_settings().export_parallel))
        return _slots


def _finish(factory: sessionmaker[Session], job_id: str, status: str,
            message: str | None = None, size: int | None = None) -> None:  # fmt: skip
    with factory() as session:
        job = session.get(ExportJob, job_id)
        if job is None:
            return
        at = now()
        job.status, job.message, job.size = status, message, size
        job.finished_at, job.expires_at, job.current = at, at + _ttl(), None
        session.commit()


def _run(factory: sessionmaker[Session], job_id: str, work: Work,
         event: threading.Event) -> None:  # fmt: skip
    slots = _semaphore()
    try:
        while not slots.acquire(timeout=1):  # warten, bis ein Platz frei ist - abbrechbar
            if event.is_set():
                _finish(factory, job_id, CANCELLED, BY_USER)
                return
        try:
            _work(factory, job_id, work, event)
        finally:
            slots.release()
    except Exception:  # noqa: BLE001 - der Thread darf nie still sterben
        log.exception("Export %s: Abschluss fehlgeschlagen", job_id)
        try:  # sichtbar als Fehler statt „laeuft“ bis zum naechsten Start
            _part(job_id).unlink(missing_ok=True)
            _finish(factory, job_id, FAILED, ERROR)
        except Exception:  # noqa: BLE001 - z. B. Datenbank weg: der Start raeumt auf
            log.exception("Export %s: Stand nicht gespeichert", job_id)
    finally:
        with _lock:
            _cancel.pop(job_id, None)
            _threads.pop(job_id, None)


def _work(factory: sessionmaker[Session], job_id: str, work: Work,
          event: threading.Event) -> None:  # fmt: skip
    part = _part(job_id)
    with factory() as session:
        job = session.get(ExportJob, job_id)
        if job is None:
            return
        if event.is_set():
            _finish(factory, job_id, CANCELLED, BY_USER)
            return
        job.status, job.started_at = RUNNING, now()
        session.commit()
        try:
            folder().mkdir(parents=True, exist_ok=True)
            handle = part.open("wb")
        except OSError:
            log.exception("Export %s: Ablage %s nicht beschreibbar", job_id, folder())
            _finish(factory, job_id, FAILED, STORAGE)
            return
        status, message = DONE, None
        try:
            with handle, zipfile.ZipFile(handle, "w", zipfile.ZIP_STORED) as z:
                work(session, Progress(session, job_id, event),
                     Bundle(z, get_settings().export_max_mb * 1024 * 1024))  # fmt: skip
        except Cancelled:
            session.rollback()
            status, message = CANCELLED, BY_USER
        except TooLarge:
            session.rollback()
            status, message = FAILED, TOO_LARGE
        except Exception:  # noqa: BLE001 - als Fehler sichtbar, Einzelheiten im Protokoll
            session.rollback()
            log.exception("Export %s fehlgeschlagen", job_id)
            status, message = FAILED, ERROR
    if status != DONE:
        part.unlink(missing_ok=True)
        _finish(factory, job_id, status, message)
        return
    target = file_of(job_id)
    try:
        part.replace(target)
        size = target.stat().st_size
    except OSError:
        log.exception("Export %s: Datei nicht abgelegt", job_id)
        _finish(factory, job_id, FAILED, STORAGE)
        return
    _finish(factory, job_id, DONE, None, size)


def abort_interrupted(factory: sessionmaker[Session]) -> int:
    """Beim Start des Orchestrators: wartende bzw. laufende Auftraege sind mit dem alten Prozess
    gestorben (das Passwort mit ihm) - als abgebrochen sichtbar, halbe Dateien weg."""
    with factory() as session:
        jobs = [j for j in session.scalars(select(ExportJob).where(ExportJob.status.in_(ACTIVE)))
                if not running_here(j.id)]  # fmt: skip
        for job in jobs:
            abort(job, ABORTED, RESTART)
        session.commit()
        return len(jobs)


def abort(job: ExportJob, status: str, reason: str) -> None:
    """Auftrag ohne Thread beenden (Neustart bzw. Abbruch eines verwaisten Auftrags)."""
    at = now()
    job.status, job.message, job.current = status, reason, None
    job.finished_at, job.expires_at = at, at + _ttl()
    _part(job.id).unlink(missing_ok=True)


def remove(session: Session, job: ExportJob) -> None:
    file_of(job.id).unlink(missing_ok=True)
    _part(job.id).unlink(missing_ok=True)
    session.delete(job)
    session.flush()


def purge(session: Session) -> int:
    """Abgelaufene Auftraege samt Datei loeschen, dazu Dateien ohne Auftrag (Reste) - nie die
    eines Auftrags, der in diesem Prozess rechnet. Gleichzeitig aufgerufen schadet nicht."""
    at = now()
    gone = [job_id for job_id, status in session.execute(
        select(ExportJob.id, ExportJob.status).where(ExportJob.expires_at < at))
        if not (status in ACTIVE and running_here(job_id))]  # fmt: skip
    for job_id in gone:  # Ablauf zaehlt ab dem Ende; was noch rechnet, bleibt
        file_of(job_id).unlink(missing_ok=True)
        _part(job_id).unlink(missing_ok=True)
    if gone:
        session.execute(delete(ExportJob).where(ExportJob.id.in_(gone)))
    count = len(gone)
    base = folder()
    if base.is_dir():
        known = set(session.scalars(select(ExportJob.id)))
        for path in base.iterdir():
            job_id = path.name.split(".")[0]
            if _NAME.match(path.name) and job_id not in known and not running_here(job_id):
                path.unlink(missing_ok=True)
                count += 1
    session.commit()
    return count


def start_cleanup(factory: sessionmaker[Session], stop: threading.Event | None = None
                  ) -> threading.Event:  # fmt: skip
    """Aufraeumen alle 10 Minuten (unabhaengig vom Zeitplan)."""
    stop = stop or threading.Event()

    def loop() -> None:
        while not stop.wait(CLEANUP_SECONDS):
            try:
                with factory() as session:
                    purge(session)
            except Exception:  # noqa: BLE001 - Schleife darf nicht sterben (DB kurz weg o. ae.)
                log.exception("Exporte nicht aufgeraeumt")

    threading.Thread(target=loop, name="automato-export-cleanup", daemon=True).start()
    return stop
