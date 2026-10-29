"""Doku je Applikation als Stand im Hintergrund (0.108.0, ADR 0037).

Anwender 2026-10-28: „das Speichern dauert zu lange“ - Doku im Hintergrund rechnen. Die Doku
(`applications.build_doc`) und die Verbindungen mit dem Plan (`app_paths.build`) brauchten bei
grossen Umgebungen je Aufruf Sekunden bis Minuten, nach jedem Speichern erneut.

- **Stand:** je Applikation, Art (`doc`, `connections`) und Variante (Sprache, Namen der
  Gegenstellen, Ausschnitt eines Kunden) das Ergebnis als JSON (gepackt, `DocSnapshot`).
- **Fingerabdruck** der Eingaben (`fingerprint`): Version der Plattform, die Applikation
  (geaendert, ihr Audit), der letzte Lauf eines Quellsystems des Mandanten (Geraete und IPAM),
  die Quellsysteme selbst und die letzte Aenderung an der Konfiguration (Audit ohne Lesen, Laeufe,
  Befunde - Zonen, Kategorien, Namensprofile, Verbund, L3Outs …). Gleicher Abdruck = gleicher
  Stand; neu gerechnet wird nur, wenn sich etwas aendert, das die Doku betrifft.
- **Sofort zeigen** (`serve`): passt der Stand, kommt er sofort. Sonst kommt der letzte Stand
  mit `fresh=false, building=true`, und er wird hinter der Antwort neu gerechnet - nur wenn die
  letzte Berechnung schnell war (`doc_stale_seconds`), wartet die Anfrage darauf. Ohne Stand
  wird einmal gewartet. Befunde zeigt die Doku immer aktuell (`applications.doc_findings`).
- **Vorrechnen** (`warm`): nach Laeufen und Aenderungen die zuletzt angesehenen Staende
  (`doc_warm_days`) neu, in einem Zeitbudget (`doc_warm_seconds`).

Je Stand rechnet im Prozess hoechstens ein Durchgang zugleich. Abgeleitete Daten, nur lesend
gegenueber den Systemen; `doc_snapshots=false` schaltet alles ab (Doku wie bis 0.107.0).
"""

import hashlib
import json
import logging
import threading
import time
import zlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import BackgroundTasks
from fastapi.encoders import jsonable_encoder
from sqlalchemy import and_, func, not_, or_, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app import __version__
from app.config import get_settings
from app.i18n import lang, use
from app.models import Application, AuditLog, CollectorRun, DocSnapshot, SourceSystem

log = logging.getLogger(__name__)

DOC, CONNECTIONS = "doc", "connections"
KINDS = (DOC, CONNECTIONS)
# Audit, das die Doku nicht aendert: Lesen, Laeufe (zaehlen ueber `CollectorRun`), Agenten,
# Befunde (immer aktuell), NetBox-Sync, Versionen des Zonenplans, Vorschlaege, Benutzer der Kunden
_QUIET = ("read.", "collector_run.", "agent.", "finding.", "reconcile.", "retention.",
          "netbox_sync.", "zone_plan.version_", "zone_suggestion.", "customer_user.",
          "check.")  # fmt: skip
# Aenderungen an einer Applikation zaehlen nur fuer sie (Subjekt = ihre ID); der Import betrifft
# mehrere und zaehlt fuer alle
_OWN, _ALL_APPS = "application.", "application.imported"

_lock = threading.Lock()
_building: set[tuple[int, str, str]] = set()  # (Applikation, Art, Variante)
_warm_locks: dict[int, threading.Lock] = defaultdict(threading.Lock)


def enabled() -> bool:
    return bool(get_settings().doc_snapshots)


def variant(names: bool = False, rule_ids: set[int] | None = None) -> str:
    """Variante eines Stands: Sprache der Anfrage, Namen der Gegenstellen, Ausschnitt."""
    scope = "all"
    if rule_ids is not None:  # Kunden-Benutzer (ADR 0018): ihre Regeln
        text = ",".join(str(i) for i in sorted(rule_ids))
        scope = "c" + hashlib.sha256(text.encode()).hexdigest()[:20]
    return f"{lang()}|{'n' if names else '-'}|{scope}"


def fingerprint(session: Session, app: Application, var: str) -> str:
    """Abdruck der Eingaben eines Stands (siehe Modul)."""
    tid = app.tenant_id
    runs = session.scalar(
        select(func.max(CollectorRun.finished_at)).where(CollectorRun.tenant_id == tid)
    )
    sources = session.execute(
        select(SourceSystem.id, SourceSystem.enabled, SourceSystem.reference_run_id)
        .where(SourceSystem.tenant_id == tid).order_by(SourceSystem.id)
    ).all()  # fmt: skip
    quiet = or_(*(AuditLog.action.like(f"{p}%") for p in _QUIET))
    others = and_(AuditLog.action.like(f"{_OWN}%"), AuditLog.action != _ALL_APPS)
    config = session.scalar(select(func.max(AuditLog.id)).where(not_(quiet), not_(others)))
    own = session.scalar(
        select(func.max(AuditLog.id)).where(
            AuditLog.action.like(f"{_OWN}%"), AuditLog.subject == str(app.id)
        )
    )
    parts = [__version__, app.id, str(app.updated_at), str(runs), [tuple(s) for s in sources],
             config, own, var]  # fmt: skip
    return hashlib.sha256(repr(parts).encode()).hexdigest()


def _pack(obj: dict) -> bytes:
    return zlib.compress(json.dumps(obj, separators=(",", ":")).encode(), 6)


def _unpack(data: bytes) -> dict:
    return json.loads(zlib.decompress(data))


@dataclass
class Params:
    """Was eine Variante zum Rechnen braucht - auch fuer das Vorrechnen gespeichert."""

    rule_ids: list[int] | None = None
    names: bool = False

    def out(self) -> dict:
        return {"rule_ids": self.rule_ids, "names": self.names}


def compute(session: Session, app: Application, kind: str, params: Params) -> dict:
    """Doku bzw. Verbindungen mit Plan neu rechnen - als JSON-faehiges Ergebnis."""
    from app.store import app_paths, app_plan  # spaet: app_paths -> applications -> ...
    from app.store import applications as store

    if kind == DOC:
        ids = set(params.rule_ids) if params.rule_ids is not None else None
        return jsonable_encoder(store.build_doc(session, app, ids, names=params.names))
    out = app_paths.build(session, app)
    # Netzwerkplan (0.67.0), kompakt nach AP/EPG (0.74.0)
    out["plan"] = app_plan.build(out["connections"], members=out["members"])
    return jsonable_encoder(out)


@dataclass
class Served:
    data: dict
    built_at: datetime
    fresh: bool  # passt zu den Eingaben
    building: bool  # wird gerade neu gerechnet
    seconds: float  # Dauer der letzten Berechnung
    stored: bool  # aus dem gespeicherten Stand (Befunde dann aktuell dazu)

    def state(self) -> dict:
        return {"built_at": self.built_at.isoformat(), "fresh": self.fresh,
                "building": self.building, "seconds": round(self.seconds, 1)}  # fmt: skip


def _row(session: Session, app_id: int, kind: str, var: str) -> DocSnapshot | None:
    return session.scalar(
        select(DocSnapshot).where(
            DocSnapshot.application_id == app_id, DocSnapshot.kind == kind,
            DocSnapshot.variant == var)
    )  # fmt: skip


def _save(session: Session, app_id: int, kind: str, var: str, fp: str, result: dict,
          seconds: float, params: Params) -> datetime:  # fmt: skip
    """Stand speichern bzw. ersetzen (gleichzeitig gerechnet: der letzte gilt). Klappt es nicht
    (gleichzeitig angelegt, SQLite gesperrt), bleibt der alte - beim naechsten Mal neu."""
    now = datetime.now(UTC)
    data = _pack({"params": params.out(), "result": result})
    try:
        with session.begin_nested():
            row = _row(session, app_id, kind, var)
            if row is None:
                row = DocSnapshot(application_id=app_id, kind=kind, variant=var)
                session.add(row)
            row.fingerprint, row.built_at, row.seconds, row.data = fp, now, seconds, data
            row.viewed_at = now
    except (IntegrityError, OperationalError):
        log.info("Stand %s/%s/%s nicht gespeichert (gleichzeitig geschrieben)", app_id, kind, var)
    return now


def _viewed(session: Session, row: DocSnapshot) -> None:
    """Zuletzt angesehen (fuer das Vorrechnen) - nebenbei, darf nie die Anfrage scheitern lassen."""
    try:
        with session.begin_nested():
            row.viewed_at = datetime.now(UTC)
    except OperationalError:  # SQLite: gleichzeitig geschrieben
        pass


def _build(session: Session, app: Application, kind: str, var: str, params: Params
           ) -> tuple[dict, float, str]:  # fmt: skip
    fp = fingerprint(session, app, var)  # vor dem Rechnen: was danach dazukommt, zaehlt neu
    started = time.perf_counter()
    result = compute(session, app, kind, params)
    return result, time.perf_counter() - started, fp


def building(app_id: int, kind: str, var: str) -> bool:
    with _lock:
        return (app_id, kind, var) in _building


def _claim(key: tuple[int, str, str]) -> bool:
    with _lock:
        if key in _building:
            return False
        _building.add(key)
        return True


def _release(key: tuple[int, str, str]) -> None:
    with _lock:
        _building.discard(key)


def rebuild(factory: sessionmaker[Session], app_id: int, kind: str, var: str, params: Params,
            code: str, claimed: bool = False) -> bool:  # fmt: skip
    """Stand mit eigener Session neu rechnen (im Hintergrund); False, wenn schon einer rechnet
    bzw. die Applikation weg ist."""
    key = (app_id, kind, var)
    if not claimed and not _claim(key):
        return False
    session = factory()
    try:
        app = session.get(Application, app_id)
        if app is None:
            return False
        with use(code):
            result, seconds, fp = _build(session, app, kind, var, params)
        _save(session, app_id, kind, var, fp, result, seconds, params)
        session.commit()
        return True
    except Exception:
        session.rollback()
        log.exception("Stand %s der Applikation %s fehlgeschlagen", kind, app_id)
        return False
    finally:
        session.close()
        _release(key)


def schedule(background: BackgroundTasks, factory: sessionmaker[Session], app_id: int,
             kind: str, var: str, params: Params) -> bool:  # fmt: skip
    """Neu rechnen hinter der Antwort vormerken; True, wenn es (jetzt oder schon) rechnet."""
    key = (app_id, kind, var)
    if not _claim(key):
        return True
    background.add_task(rebuild, factory, app_id, kind, var, params, lang(), True)
    return True


def serve(session: Session, factory: sessionmaker[Session], background: BackgroundTasks,
          app: Application, kind: str, rule_ids: set[int] | None = None, names: bool = False,
          force: bool = False, wait: bool = False) -> Served:  # fmt: skip
    """Stand der Applikation: passend sofort, sonst den letzten und neu im Hintergrund (siehe
    Modul). `force`: neu rechnen, auch wenn er passt (Schaltflaeche „neu berechnen“); `wait`:
    nie einen alten Stand (Export)."""
    params = Params(sorted(rule_ids) if rule_ids is not None else None, names)
    var = variant(names, rule_ids) + ("" if kind == DOC else f"|{kind}")
    if not enabled():
        started = time.perf_counter()
        result = compute(session, app, kind, params)
        return Served(result, datetime.now(UTC), True, False, time.perf_counter() - started,
                      False)  # fmt: skip
    row = _row(session, app.id, kind, var)
    if row is not None:
        now = datetime.now(UTC)
        fresh = row.fingerprint == fingerprint(session, app, var)
        if fresh and not force:
            if _aware(row.viewed_at) < now - timedelta(minutes=10):
                _viewed(session, row)  # fuer das Vorrechnen (nicht bei jedem Aufruf schreiben)
            return Served(_unpack(row.data)["result"], _aware(row.built_at), True,
                          building(app.id, kind, var), row.seconds, True)  # fmt: skip
        slow = row.seconds >= get_settings().doc_stale_seconds
        # langsam bzw. rechnet schon (Vorrechnen): letzter Stand, neu hinter der Antwort
        if not wait and (force or slow or building(app.id, kind, var)):
            data = _unpack(row.data)["result"]
            built, seconds = _aware(row.built_at), row.seconds
            _viewed(session, row)
            schedule(background, factory, app.id, kind, var, params)
            return Served(data, built, False, True, seconds, True)
    # erstes Mal, schnell genug bzw. warten: hier rechnen und speichern
    result, seconds, fp = _build(session, app, kind, var, params)
    built = _save(session, app.id, kind, var, fp, result, seconds, params)
    return Served(result, built, True, False, seconds, False)


def state(session: Session, app: Application, kind: str, rule_ids: set[int] | None = None,
          names: bool = False) -> dict:  # fmt: skip
    """Nur der Zustand des Stands (Abfrage der Oberflaeche, waehrend er rechnet)."""
    var = variant(names, rule_ids) + ("" if kind == DOC else f"|{kind}")
    if not enabled():
        return {"built_at": None, "fresh": True, "building": False, "seconds": 0.0}
    row = session.execute(
        select(DocSnapshot.fingerprint, DocSnapshot.built_at, DocSnapshot.seconds).where(
            DocSnapshot.application_id == app.id, DocSnapshot.kind == kind,
            DocSnapshot.variant == var)
    ).first()  # fmt: skip
    busy = building(app.id, kind, var)
    if row is None:
        return {"built_at": None, "fresh": False, "building": busy, "seconds": 0.0}
    fresh = row.fingerprint == fingerprint(session, app, var)
    return {"built_at": _aware(row.built_at).isoformat(), "fresh": fresh, "building": busy,
            "seconds": round(row.seconds, 1)}  # fmt: skip


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def warm(factory: sessionmaker[Session], tenant_id: int, application_id: int | None = None
         ) -> int:  # fmt: skip
    """Zuletzt angesehene Staende des Mandanten (bzw. der Applikation) neu rechnen, die nicht
    mehr passen - nach Laeufen und Aenderungen, im Zeitbudget. -> Anzahl neu gerechnet."""
    settings = get_settings()
    if not enabled() or settings.doc_warm_days <= 0:
        return 0
    from app import modules  # spaet: Module kennen die Store-Dateien

    with _warm_locks[tenant_id]:
        session = factory()
        try:
            if not modules.enabled(session, tenant_id, "doku"):  # Modul aus (ADR 0036)
                return 0
            since = datetime.now(UTC) - timedelta(days=settings.doc_warm_days)
            stmt = (
                select(DocSnapshot.id, DocSnapshot.application_id, DocSnapshot.kind,
                       DocSnapshot.variant, DocSnapshot.fingerprint)
                .join(Application, Application.id == DocSnapshot.application_id)
                .where(Application.tenant_id == tenant_id, DocSnapshot.viewed_at >= since)
                .order_by(DocSnapshot.viewed_at.desc())
            )  # fmt: skip
            if application_id is not None:
                stmt = stmt.where(DocSnapshot.application_id == application_id)
            rows = session.execute(stmt).all()
            todo = []
            for sid, app_id, kind, var, fp in rows:
                app = session.get(Application, app_id)
                if app is not None and fingerprint(session, app, var) != fp:
                    todo.append((sid, app_id, kind, var))
            params = {
                sid: _unpack(data)["params"]
                for sid, data in session.execute(
                    select(DocSnapshot.id, DocSnapshot.data).where(
                        DocSnapshot.id.in_([t[0] for t in todo]))
                )
            } if todo else {}  # fmt: skip
        finally:
            session.close()
        done, started = 0, time.monotonic()
        budget = settings.doc_warm_seconds
        for sid, app_id, kind, var in todo:
            if budget and time.monotonic() - started > budget:
                log.info("Mandant %s: %d Staende nicht vorgerechnet (Zeitbudget)", tenant_id,
                         len(todo) - done)  # fmt: skip
                break
            p = params.get(sid) or {}
            code = var.split("|", 1)[0]
            if rebuild(factory, app_id, kind, var,
                       Params(p.get("rule_ids"), bool(p.get("names"))), code):  # fmt: skip
                done += 1
        return done
