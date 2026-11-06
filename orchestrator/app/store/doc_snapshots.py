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
  (`doc_warm_days`) neu, in einem Zeitbudget (`doc_warm_seconds`). Seit 0.133.2 (Anwender:
  „Ich habe noch immer 83 App, welche nie berechnet wurden“) mit `doc_warm_all` Doku und
  Verbindungen jeder Applikation im Standard (ohne Namen, alle Regeln) - auch nie geoeffnete,
  schnelle zuerst, bis nichts mehr aussteht; ein weiterer Anstoss waehrend des Vorrechnens wartet
  nicht, sondern laesst es danach erneut schauen. Was mit einem Fehler abbricht, bleibt bis zu
  neuen Eingaben stehen und zeigt den Fehler in der Uebersicht. Seit 0.139.0 in Durchgaengen:
  was beim Start aussteht, wird je Stand einmal gerechnet; Laeufe waehrenddessen merken nur einen
  weiteren Durchgang (`warm`, `round_of`).

- **Uebersicht** (`overview`, 0.129.0): je Applikation des Mandanten, ob ihre Staende passen,
  veraltet sind, gerade rechnen bzw. noch nie gerechnet wurden, dazu ob das Vorrechnen laeuft.

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
from app.i18n import DEFAULT, lang, use
from app.models import Application, AuditLog, DocSnapshot, SourceSystem

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
# 0.133.1 (Anwender: „der "rechnet gerade" verschwindet immer wieder … er aktuell bei den
# Verbindungen am Messen ist“): je Applikation seit wann und wie weit (Wege fertig von allen)
_progress: dict[int, dict] = {}
_warm_locks: dict[int, threading.Lock] = defaultdict(threading.Lock)
# 0.133.2: Anstoss waehrend des Vorrechnens (Mandant) - danach erneut schauen, statt zu warten
_warm_again: set[int] = set()
# 0.139.0: laufender Durchgang je Mandant (Nummer, Staende, offene je Applikation, seit)
_rounds: dict[int, dict] = {}
# 0.133.2 (Anwender: „Er bricht immer bei dieser Anzahl ab“): letzter Fehler je Stand mit dem
# Abdruck der Eingaben - bis sich diese aendern, rechnet das Vorrechnen ihn nicht erneut
_failed: dict[tuple[int, str, str], dict] = {}
# 0.141.0: warum ein Stand neu rechnen muss (Aenderung, die ihn betrifft) - fuer die Uebersicht
_reasons: dict[tuple[int, str, str], str] = {}


def enabled() -> bool:
    return bool(get_settings().doc_snapshots)


def variant(names: bool = False, rule_ids: set[int] | None = None) -> str:
    """Variante eines Stands: Sprache der Anfrage, Namen der Gegenstellen, Ausschnitt."""
    scope = "all"
    if rule_ids is not None:  # Kunden-Benutzer (ADR 0018): ihre Regeln
        text = ",".join(str(i) for i in sorted(rule_ids))
        scope = "c" + hashlib.sha256(text.encode()).hexdigest()[:20]
    return f"{lang()}|{'n' if names else '-'}|{scope}"


def _standard(var: str) -> bool:
    """Stand im Standard (ohne Namen der Gegenstellen, alle Regeln) - in jeder Sprache."""
    parts = var.split("|")
    return len(parts) >= 3 and parts[1] == "-" and parts[2] == "all"


def _tenant_parts(session: Session, tid: int, full_only: bool = False) -> tuple:
    """Eingaben des Mandanten im Abdruck: Datenstand, Quellsysteme, letzte Aenderung.

    Seit 0.141.0 (ADR 0049) ist der Datenstand die Nummer der letzten Aenderung am Inhalt
    (`data_state.generation`) statt des Zeitpunkts des letzten Laufs - ein Lauf ohne Aenderung
    aendert nichts. `full_only` (Routing-Filter): nur Aenderungen an Routing bzw. Inventar."""
    from app.store import data_state  # spaet: data_state gehoert zum Worker

    generation = data_state.generation(session, tid, net_only=full_only)
    sources = session.execute(
        select(SourceSystem.id, SourceSystem.enabled)
        .where(SourceSystem.tenant_id == tid).order_by(SourceSystem.id)
    ).all()  # fmt: skip
    quiet = or_(*(AuditLog.action.like(f"{p}%") for p in _QUIET))
    others = and_(AuditLog.action.like(f"{_OWN}%"), AuditLog.action != _ALL_APPS)
    config = session.scalar(select(func.max(AuditLog.id)).where(not_(quiet), not_(others)))
    return generation, [tuple(s) for s in sources], config


def _base(app: Application, var: str, tenant: tuple, own: int | None) -> str:
    """Abdruck ohne den Datenstand (0.141.0): Version, Applikation, Quellsysteme, Konfiguration,
    Variante - aendert sich davon etwas, rechnet der Stand neu."""
    _generation, sources, config = tenant
    parts = [__version__, app.id, str(app.updated_at), sources, config, own, var,
             get_settings().app_traces_max]  # 0.130.0: andere Grenze = neu rechnen  # fmt: skip
    return hashlib.sha256(repr(parts).encode()).hexdigest()


def _join(base: str, generation: int) -> str:
    return hashlib.sha256(f"{base}|{generation}".encode()).hexdigest()


def _fp(app: Application, var: str, tenant: tuple, own: int | None) -> str:
    return _join(_base(app, var, tenant, own), tenant[0])


def _own(session: Session, app: Application) -> int | None:
    return session.scalar(
        select(func.max(AuditLog.id)).where(
            AuditLog.action.like(f"{_OWN}%"), AuditLog.subject == str(app.id)
        )
    )


@dataclass
class Inputs:
    """Abdruck eines Stands vor dem Rechnen: ganz, ohne Datenstand, Datenstand."""

    fp: str
    base: str
    generation: int


def inputs(session: Session, app: Application, var: str) -> Inputs:
    tenant = _tenant_parts(session, app.tenant_id)
    base = _base(app, var, tenant, _own(session, app))
    return Inputs(_join(base, tenant[0]), base, tenant[0])


def fingerprint(session: Session, app: Application, var: str) -> str:
    """Abdruck der Eingaben eines Stands (siehe Modul)."""
    return inputs(session, app, var).fp


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
    """Doku bzw. Verbindungen mit Plan neu rechnen - als JSON-faehiges Ergebnis. Mit dem
    Wege-Speicher des Mandanten (0.140.0, ADR 0048): Wege, Policies und Urteile je Hop teilen
    alle Applikationen eines Datenstands."""
    from app.store import path_store

    with path_store.active(session, app.tenant_id):
        return _compute(session, app, kind, params)


def _compute(session: Session, app: Application, kind: str, params: Params) -> dict:
    from app.store import app_paths, app_plan  # spaet: app_paths -> applications -> ...
    from app.store import applications as store

    if kind == DOC:
        ids = set(params.rule_ids) if params.rule_ids is not None else None
        return jsonable_encoder(store.build_doc(session, app, ids, names=params.names))

    since = time.time()

    def progress(done: int, total: int) -> None:
        with _lock:
            _progress[app.id] = {"since": since, "done": done, "total": total}

    try:
        out = app_paths.build(session, app, progress=progress)
    finally:
        with _lock:
            _progress.pop(app.id, None)
    # Netzwerkplan (0.67.0), kompakt nach AP/EPG (0.74.0)
    started = time.monotonic()
    out["plan"] = app_plan.build(out["connections"], members=out["members"])
    timing = out.get("timing") or {}
    timing["plan"] = round(time.monotonic() - started, 2)
    log.info("Applikation %s: Verbindungen in %.1f s (%s Wege %.1f s, Doku %.1f s, Plan %.1f s, "
             "%s Abfragen)", app.id, timing.get("total", 0) + timing["plan"], timing.get("count"),
             timing.get("traces", 0), timing.get("doc", 0), timing["plan"],
             timing.get("queries"))  # fmt: skip
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


def _save(session: Session, app_id: int, kind: str, var: str, fp: "Inputs", result: dict,
          seconds: float, params: Params, viewed: bool = True) -> datetime:  # fmt: skip
    """Stand speichern bzw. ersetzen (gleichzeitig gerechnet: der letzte gilt). Klappt es nicht
    (gleichzeitig angelegt, SQLite gesperrt), bleibt der alte - beim naechsten Mal neu.
    `viewed=False` (Vorrechnen, 0.133.2): zuletzt angesehen bleibt - vorgerechnet ist nicht
    angesehen, sonst rechnete das Vorrechnen einen Stand fuer immer neu."""
    from app.store import footprint  # spaet: footprint -> data_state

    now = datetime.now(UTC)
    data = _pack({"params": params.out(), "result": result})
    try:
        trace = footprint.of(kind, result)
    except Exception:  # noqa: BLE001 - ohne Fussabdruck rechnet der Stand beim naechsten Mal neu
        log.exception("Fussabdruck des Stands %s/%s/%s nicht ermittelt", app_id, kind, var)
        trace = None
    try:
        with session.begin_nested():
            row = _row(session, app_id, kind, var)
            if row is None:
                row = DocSnapshot(application_id=app_id, kind=kind, variant=var, viewed_at=now)
                session.add(row)
            row.fingerprint, row.built_at, row.seconds, row.data = fp.fp, now, seconds, data
            row.base, row.data_seq, row.footprint = fp.base, fp.generation, trace
            if viewed:
                row.viewed_at = now
    except (IntegrityError, OperationalError):
        log.info("Stand %s/%s/%s nicht gespeichert (gleichzeitig geschrieben)", app_id, kind, var)
    with _lock:
        _failed.pop((app_id, kind, var), None)
    if kind == CONNECTIONS and _standard(var):
        _derive(session, app_id, result)
    return now


def _derive(session: Session, app_id: int, result: dict) -> None:
    """Befunde der Applikation aus ihren eben gerechneten Verbindungen (0.139.1,
    `reconcile.derive_app_findings`) - statt die Wege fuer die Befunde ein zweites Mal zu rechnen;
    danach die Zugehoerigkeit der Befunde zu den Kunden. Darf den Stand nie verlieren."""
    from app.store import customers, reconcile  # spaet: reconcile -> app_paths -> ...

    try:
        with session.begin_nested():
            if reconcile.derive_app_findings(session, app_id, result) is not None:
                app = session.get(Application, app_id)
                customers.derive_findings(session, app.tenant_id)
    except OperationalError:  # SQLite: gleichzeitig geschrieben - beim naechsten Stand erneut
        log.info("Befunde der Applikation %s nicht nachgezogen (gleichzeitig geschrieben)", app_id)
    except Exception:  # noqa: BLE001 - Befunde nachziehen darf den Stand nicht kosten
        log.exception("Befunde aus den Verbindungen der Applikation %s fehlgeschlagen", app_id)


def _viewed(session: Session, row: DocSnapshot) -> None:
    """Zuletzt angesehen (fuer das Vorrechnen) - nebenbei, darf nie die Anfrage scheitern lassen."""
    try:
        with session.begin_nested():
            row.viewed_at = datetime.now(UTC)
    except OperationalError:  # SQLite: gleichzeitig geschrieben
        pass


def _build(session: Session, app: Application, kind: str, var: str, params: Params
           ) -> tuple[dict, float, Inputs]:  # fmt: skip
    fp = inputs(session, app, var)  # vor dem Rechnen: was danach dazukommt, zaehlt neu
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
        if not any(k[0] == key[0] for k in _building):
            _progress.pop(key[0], None)


def progress_of(app_id: int) -> dict | None:
    """Wie weit die Verbindungen der Applikation gerade sind (Wege fertig von allen, seit)."""
    with _lock:
        p = _progress.get(app_id)
        return dict(p) if p else None


def rebuild(factory: sessionmaker[Session], app_id: int, kind: str, var: str, params: Params,
            code: str, claimed: bool = False, viewed: bool = True) -> bool:  # fmt: skip
    """Stand mit eigener Session neu rechnen (im Hintergrund); False, wenn schon einer rechnet,
    die Applikation weg ist bzw. die Berechnung fehlschlaegt (Fehler gemerkt, `failure`)."""
    key = (app_id, kind, var)
    if not claimed and not _claim(key):
        return False
    session = factory()
    fp = None
    try:
        app = session.get(Application, app_id)
        if app is None:
            return False
        with use(code):
            fp = inputs(session, app, var)  # vor dem Rechnen (wie `_build`)
            started = time.perf_counter()
            result = compute(session, app, kind, params)
            seconds = time.perf_counter() - started
        _save(session, app_id, kind, var, fp, result, seconds, params, viewed)
        session.commit()
        return True
    except Exception as exc:
        session.rollback()
        log.exception("Stand %s der Applikation %s fehlgeschlagen", kind, app_id)
        with _lock:
            _failed[key] = {"fp": fp.fp if fp else None,
                            "error": f"{type(exc).__name__}: {exc}"[:300],
                            "at": datetime.now(UTC).isoformat()}  # fmt: skip
        return False
    finally:
        session.close()
        _release(key)


def failure(app_id: int, kind: str, var: str) -> dict | None:
    """Letzter Fehler des Stands (0.133.2) - mit dem Abdruck, bei dem er auftrat."""
    with _lock:
        f = _failed.get((app_id, kind, var))
        return dict(f) if f else None


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
        if not fresh and not force and carry(session, app.tenant_id, app.id, row_ids={row.id}):
            fresh = True  # 0.141.0: Aenderung betrifft den Stand nicht - uebernommen
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


def overview(session: Session, tenant_id: int) -> dict:
    """Stand der Doku je Applikation des Mandanten (0.129.0, Anwender: „ein Fenster erstellen,
    wo man sieht, wie der Stand der Neuberechnungen der Dokumentationen ist“): je Applikation und
    Art (Doku, Verbindungen) die Staende (Varianten) mit passend/veraltet, ob gerade einer rechnet,
    letzte Berechnung und Dauer; Zustand der Applikation `building` (rechnet), `stale` (veraltet -
    `auto`: wird vorgerechnet, sonst beim naechsten Oeffnen), `never` (noch nie gerechnet), sonst
    `fresh`. Dazu, ob das Vorrechnen fuer den Mandanten laeuft. Nur lesend.

    0.133.2: mit `doc_warm_all` ist `never` auch, wem die Verbindungen fehlen (`auto`: werden
    vorgerechnet); `error` = letzter Fehler einer Berechnung mit denselben Eingaben (je Art
    `kinds[..].error`) - dann nicht vorgerechnet, bis sich etwas aendert."""
    settings = get_settings()
    lock = _warm_locks.get(tenant_id)
    apps = list(session.scalars(
        select(Application).where(Application.tenant_id == tenant_id).order_by(Application.name)
    ))  # fmt: skip
    from app.store import app_refresh, data_state, runs  # spaet: app_refresh -> doc_snapshots

    head = {"enabled": enabled(), "warming": bool(lock and lock.locked()),
            "round": round_of(tenant_id),  # 0.139.0: Durchgang des Vorrechnens
            # 0.141.0: wartet auf offene Laeufe; Datenstand (Nummer der letzten Aenderung)
            "waiting_runs": (runs.open_count(session, tenant_id)
                             if app_refresh.waiting(tenant_id) else 0),  # fmt: skip
            "generation": data_state.generation(session, tenant_id),
            "warm_days": settings.doc_warm_days,
            "warm_all": settings.doc_warm_days > 0 and bool(settings.doc_warm_all)}  # fmt: skip
    counts = dict.fromkeys(("fresh", "stale", "building", "never"), 0)
    if not enabled() or not apps:
        return {**head, "counts": counts, "total": len(apps), "last_built_at": None,
                "apps": []}  # fmt: skip
    ids = [a.id for a in apps]
    tenant = _tenant_parts(session, tenant_id)
    own = dict(session.execute(
        select(AuditLog.subject, func.max(AuditLog.id))
        .where(AuditLog.action.like(f"{_OWN}%"), AuditLog.subject.in_([str(i) for i in ids]))
        .group_by(AuditLog.subject)
    ).all())  # fmt: skip
    rows = session.execute(
        select(DocSnapshot.application_id, DocSnapshot.kind, DocSnapshot.variant,
               DocSnapshot.fingerprint, DocSnapshot.built_at, DocSnapshot.seconds,
               DocSnapshot.viewed_at)
        .where(DocSnapshot.application_id.in_(ids))
    ).all()  # fmt: skip
    by_app: dict[int, list] = defaultdict(list)
    for r in rows:
        by_app[r.application_id].append(r)
    with _lock:
        busy, failed, reasons = set(_building), dict(_failed), dict(_reasons)
    since = datetime.now(UTC) - timedelta(days=settings.doc_warm_days)
    warming = settings.doc_warm_days > 0
    every = warming and bool(settings.doc_warm_all)  # 0.133.2: alle im Standard
    out, last = [], None
    for a in apps:
        kinds, stale, auto, error = {}, 0, False, None
        for kind in KINDS:
            snaps = [r for r in by_app.get(a.id, []) if r.kind == kind]
            fresh = [r for r in snaps if r.fingerprint == _fp(a, r.variant, tenant,
                                                               own.get(str(a.id)))]  # fmt: skip
            old = [r for r in snaps if r not in fresh]
            stale += len(old)
            auto = auto or any((every and _standard(r.variant)) or _aware(r.viewed_at) >= since
                               for r in old)  # fmt: skip
            # 0.133.2: mit denselben Eingaben fehlgeschlagen - nicht vorgerechnet, Fehler zeigen
            err = None
            for (aid, k, var), f in failed.items():
                if (
                    aid == a.id
                    and k == kind
                    and f.get("fp") == _fp(a, var, tenant, own.get(str(a.id)))
                ):
                    err = f
                    break
            built = max((_aware(r.built_at) for r in snaps), default=None)
            if built and (last is None or built > last):
                last = built
            kinds[kind] = {
                "variants": len(snaps), "fresh": len(fresh),
                "building": any(k[0] == a.id and k[1] == kind for k in busy),
                "built_at": built.isoformat() if built else None,
                "seconds": round(max((r.seconds for r in snaps), default=0.0), 1),
            }  # fmt: skip
            # 0.141.0: warum veraltet - die Aenderung, die den Stand betrifft
            why = next((reasons[(a.id, kind, r.variant)] for r in old
                        if (a.id, kind, r.variant) in reasons), None)  # fmt: skip
            if why:
                kinds[kind]["reason"] = why
            if err is not None:
                kinds[kind]["error"] = {"text": err["error"], "at": err["at"]}
                error = error or err["error"]
        if any(k["building"] for k in kinds.values()):
            state = "building"
            if (p := progress_of(a.id)) and p.get("total"):  # 0.133.1: wie weit
                kinds[CONNECTIONS]["progress"] = {
                    "done": p["done"], "total": p["total"],
                    "seconds": round(time.time() - p["since"]),
                }  # fmt: skip
        elif not by_app.get(a.id) or (every and not kinds[CONNECTIONS]["variants"]):
            state = "never"  # 0.133.2: mit `doc_warm_all` auch, wenn Verbindungen fehlen
        elif stale:
            state = "stale"
        else:
            state = "fresh"
        counts[state] += 1
        auto = warming and ((state == "stale" and auto) or (state == "never" and every))
        out.append({"id": a.id, "name": a.name, "state": state, "stale": stale,
                    "auto": auto and error is None, "error": error, "kinds": kinds})  # fmt: skip
    return {**head, "counts": counts, "total": len(apps),
            "last_built_at": last.isoformat() if last else None, "apps": out}  # fmt: skip


@dataclass
class _Todo:
    """Ein Stand zum Vorrechnen (0.133.2): geschaetzte Dauer, Schluessel, Abdruck jetzt, Zeile."""

    estimate: float
    key: tuple[int, str, str]
    fp: str
    sid: int | None  # None: noch nie gerechnet


def _todo(session: Session, tenant_id: int, application_id: int | None) -> list[_Todo]:
    """Was das Vorrechnen jetzt rechnen soll, je Applikation (schnelle zuerst, 0.139.1): Staende,
    die nicht mehr passen -
    im Standard immer (`doc_warm_all`), sonst zuletzt angesehen (`doc_warm_days`) - und mit
    `doc_warm_all` Doku bzw. Verbindungen im Standard, die noch nie gerechnet wurden (Dauer
    geschaetzt nach der Doku der Applikation). Ohne die, die mit denselben Eingaben fehlschlugen
    bzw. gerade rechnen. Eingaben des Mandanten einmal je Aufruf (wie `overview`)."""
    settings = get_settings()
    stmt = select(Application).where(Application.tenant_id == tenant_id)
    if application_id is not None:
        stmt = stmt.where(Application.id == application_id)
    apps = {a.id: a for a in session.scalars(stmt)}
    if not apps:
        return []
    tenant = _tenant_parts(session, tenant_id)
    own = dict(session.execute(
        select(AuditLog.subject, func.max(AuditLog.id))
        .where(AuditLog.action.like(f"{_OWN}%"), AuditLog.subject.in_([str(i) for i in apps]))
        .group_by(AuditLog.subject)
    ).all())  # fmt: skip
    rows = session.execute(
        select(DocSnapshot.id, DocSnapshot.application_id, DocSnapshot.kind,
               DocSnapshot.variant, DocSnapshot.fingerprint, DocSnapshot.seconds,
               DocSnapshot.viewed_at)
        .where(DocSnapshot.application_id.in_(list(apps)))
    ).all()  # fmt: skip
    since = datetime.now(UTC) - timedelta(days=settings.doc_warm_days)
    every = bool(settings.doc_warm_all)
    with _lock:
        busy, failed = set(_building), dict(_failed)
    out: list[_Todo] = []

    def add(estimate: float, app: Application, kind: str, var: str, sid: int | None) -> None:
        key = (app.id, kind, var)
        fp = _fp(app, var, tenant, own.get(str(app.id)))
        if key in busy or (failed.get(key) or {}).get("fp") == fp:
            return
        out.append(_Todo(estimate, key, fp, sid))

    have: dict[tuple[int, str], float] = {}  # Standard je Applikation und Art: Dauer
    for r in rows:
        app = apps[r.application_id]
        if _standard(r.variant):
            have[(app.id, r.kind)] = max(have.get((app.id, r.kind), 0.0), r.seconds)
        if not (every and _standard(r.variant)) and _aware(r.viewed_at) < since:
            continue
        if r.fingerprint != _fp(app, r.variant, tenant, own.get(str(app.id))):
            add(r.seconds, app, r.kind, r.variant, r.id)
    if every:
        with use(DEFAULT):  # Sprache der Plattform (nicht die der ausloesenden Anfrage)
            base = variant()  # ohne Namen, alle Regeln
        for app in apps.values():
            if (app.id, DOC) not in have:
                add(0.0, app, DOC, base, None)
            if (app.id, CONNECTIONS) not in have:  # Verbindungen dauern laenger als die Doku
                add(have.get((app.id, DOC), 1.0) * 2, app, CONNECTIONS, f"{base}|{CONNECTIONS}",
                    None)  # fmt: skip
    # 0.139.1: Applikation fuer Applikation (Doku, dann Verbindungen) - die schnellen zuerst; bis
    # dahin erst alle Dokus, dann alle Verbindungen (lange keine Applikation ganz fertig)
    per_app: dict[int, float] = defaultdict(float)
    for t in out:
        per_app[t.key[0]] += t.estimate
    out.sort(key=lambda t: (per_app[t.key[0]], t.key[0], t.key[1] != DOC, t.key[2]))
    return out


def _start_warm(tenant_id: int) -> bool:
    """Vorrechnen fuer den Mandanten beginnen; laeuft es schon, schaut es danach erneut (kein
    Warten - ein Anstoss je Lauf blockierte sonst je einen Thread, 0.133.2)."""
    with _lock:
        if _warm_locks[tenant_id].acquire(blocking=False):
            _warm_again.discard(tenant_id)
            return True
        _warm_again.add(tenant_id)
        return False


def _end_warm(tenant_id: int) -> bool:
    """Vorrechnen beenden - ausser es kam waehrenddessen ein Anstoss (dann False: weiter)."""
    with _lock:
        if tenant_id in _warm_again:
            _warm_again.discard(tenant_id)
            return False
        _warm_locks[tenant_id].release()
        return True


def round_of(tenant_id: int) -> dict | None:
    """Durchgang des Vorrechnens (0.139.0): Nummer, Applikationen fertig von allen, Staende,
    seit wann, ob danach ein weiterer folgt (`again`: Lauf bzw. Aenderung waehrenddessen)."""
    with _lock:
        r = _rounds.get(tenant_id)
        if r is None:
            return None
        return {"number": r["number"], "apps_done": len(r["apps"]) - len(r["open"]),
                "apps_total": len(r["apps"]), "done": r["done"], "total": r["total"],
                "since": r["since"], "application_id": r["application_id"],
                "again": tenant_id in _warm_again}  # fmt: skip


def _round_start(tenant_id: int, number: int, todo: list[_Todo], application_id: int | None
                 ) -> None:  # fmt: skip
    left: dict[int, int] = defaultdict(int)
    for t in todo:
        left[t.key[0]] += 1
    with _lock:
        _rounds[tenant_id] = {"number": number, "total": len(todo), "done": 0,
                              "apps": set(left), "open": dict(left), "since": time.time(),
                              "application_id": application_id}  # fmt: skip


def _round_step(tenant_id: int, app_id: int) -> None:
    with _lock:
        r = _rounds.get(tenant_id)
        if r is None:
            return
        r["done"] += 1
        r["open"][app_id] -= 1
        if r["open"][app_id] <= 0:
            r["open"].pop(app_id)


def _fresh(session: Session, item: _Todo) -> bool:
    """Passt der Stand schon (z. B. beim Oeffnen gerechnet, waehrend der Durchgang lief)?"""
    app_id, kind, var = item.key
    row = session.execute(
        select(DocSnapshot.fingerprint).where(
            DocSnapshot.application_id == app_id, DocSnapshot.kind == kind,
            DocSnapshot.variant == var)
    ).first()  # fmt: skip
    app = session.get(Application, app_id)
    return row is not None and app is not None and row.fingerprint == fingerprint(session, app, var)


def carry(
    session: Session,
    tenant_id: int,
    application_id: int | None = None,
    row_ids: set[int] | None = None,
) -> int:
    """Staende ohne Neuberechnung auf den aktuellen Datenstand heben, wenn keine Aenderung seit
    ihrer Berechnung sie betrifft (0.141.0, ADR 0049, `footprint.affected`). Nur wenn sonst nichts
    anders ist (Version, Applikation, Konfiguration, Quellsysteme: `_base`); fehlt eine Aenderung
    (aufgeraeumt) bzw. der Fussabdruck, rechnet der Stand neu. Den Grund merkt `_reasons`.
    -> Anzahl uebernommen."""
    from app.store import data_state, footprint  # spaet: footprint -> data_state

    tenant = _tenant_parts(session, tenant_id)
    generation = tenant[0]
    stmt = select(Application).where(Application.tenant_id == tenant_id)
    if application_id is not None:
        stmt = stmt.where(Application.id == application_id)
    apps = {a.id: a for a in session.scalars(stmt)}
    if not apps or not generation:
        return 0
    query = select(DocSnapshot).where(
        DocSnapshot.application_id.in_(list(apps)), DocSnapshot.base.is_not(None),
        DocSnapshot.data_seq.is_not(None), DocSnapshot.data_seq < generation)  # fmt: skip
    if row_ids is not None:
        query = query.where(DocSnapshot.id.in_(list(row_ids)))
    rows = list(session.scalars(query))
    if not rows:
        return 0
    own = dict(session.execute(
        select(AuditLog.subject, func.max(AuditLog.id))
        .where(AuditLog.action.like(f"{_OWN}%"),
               AuditLog.subject.in_([str(r.application_id) for r in rows]))
        .group_by(AuditLog.subject)
    ).all())  # fmt: skip
    # Geraete aller Staende der Applikation: die Doku nutzt die Wege ihrer Verbindungen
    devices: dict[int, set[int]] = defaultdict(set)
    for app_id, data in session.execute(
        select(DocSnapshot.application_id, DocSnapshot.footprint)
        .where(DocSnapshot.application_id.in_({r.application_id for r in rows}))
    ):  # fmt: skip
        fp = footprint.read(data)
        devices[app_id] |= set((fp or {}).get("devices") or [])
    lowest = min(r.data_seq for r in rows)
    found = data_state.changes(session, tenant_id, lowest, generation)
    by_seq = {c.seq: c for c in found or []}
    ctx = footprint.Context(session, found or [])
    carried = 0
    for r in rows:
        app = apps[r.application_id]
        key = (app.id, r.kind, r.variant)
        if r.base != _base(app, r.variant, tenant, own.get(str(app.id))):
            continue  # anderes als Daten geaendert: neu rechnen
        fp = footprint.read(r.footprint)
        todo = [by_seq.get(n) for n in range(r.data_seq + 1, generation + 1)]
        if fp is None or None in todo:
            _note(key, "ohne Fussabdruck" if fp is None else "Aenderungen nicht mehr bekannt")
            continue
        p = footprint.Print(fp, devices.get(app.id, set()))
        why = next((w for c in todo for hit, w in [footprint.affected(p, c, ctx)] if hit), None)
        if why:
            _note(key, why)
            continue
        r.fingerprint = _join(r.base, generation)
        r.data_seq = generation
        carried += 1
        with _lock:
            _reasons.pop(key, None)
    session.flush()
    if carried or len(rows) > carried:
        log.info("Mandant %s: Datenstand %s - %d Staende uebernommen (nicht betroffen), %d neu zu "
                 "rechnen", tenant_id, generation, carried, len(rows) - carried)  # fmt: skip
    return carried


def _note(key: tuple[int, str, str], why: str) -> None:
    with _lock:
        _reasons[key] = why


def reason(app_id: int, kind: str, var: str) -> str | None:
    """Warum der Stand neu rechnen muss (0.141.0) - z. B. „Regel 12 (Urteil auf dem Weg)“."""
    with _lock:
        return _reasons.get((app_id, kind, var))


def _carry(factory: sessionmaker[Session], tenant_id: int, application_id: int | None) -> int:
    session = factory()
    try:
        done = carry(session, tenant_id, application_id)
        session.commit()
        return done
    except Exception:  # noqa: BLE001 - Uebernehmen ist ein Abkuerzung, Rechnen geht immer
        session.rollback()
        log.exception("Mandant %s: Staende nicht uebernommen", tenant_id)
        return 0
    finally:
        session.close()


def warm(factory: sessionmaker[Session], tenant_id: int, application_id: int | None = None
         ) -> int:  # fmt: skip
    """Staende des Mandanten (bzw. der Applikation) vorrechnen, die nicht mehr passen bzw. noch
    nie gerechnet wurden (`_todo`) - nach Laeufen, Aenderungen und dem Start.

    In Durchgaengen (0.139.0, Anwender: „Wenn ich Imports am Laufen habe, startet er jedes Mal
    neu, wenn ein Gerät importiert hat … vorberechnen ungeachtet der Daten mal starten und dann
    wenn alle vorberechnet sind, die komplette Berechnung durchführen“): zu Beginn steht fest,
    was aussteht (schnelle zuerst), und jeder Stand wird genau einmal gerechnet - mit den Daten,
    die beim Rechnen gelten. Ein Lauf bzw. eine Aenderung waehrenddessen bricht nichts ab,
    sondern merkt einen weiteren Durchgang mit allem, was danach nicht mehr passt. Bis 0.138.0
    galt nach jedem Lauf alles als neu, die schnellen kamen immer wieder zuerst dran und die
    langsamen nie. Endet nach `doc_warm_seconds` (0 = ohne Grenze). -> Anzahl neu gerechnet."""
    settings = get_settings()
    if not enabled() or settings.doc_warm_days <= 0:
        return 0
    from app import modules  # spaet: Module kennen die Store-Dateien

    if not _start_warm(tenant_id):
        return 0
    done, started, number = 0, time.monotonic(), 0
    budget = settings.doc_warm_seconds
    held = True
    try:
        while True:
            number += 1
            if count := _open_runs(factory, tenant_id):  # 0.141.0: erst nach den Laeufen
                held = False
                return _pause(tenant_id, count, done)
            _carry(factory, tenant_id, application_id)  # 0.141.0: nicht betroffene uebernehmen
            session = factory()
            try:
                todo = (_todo(session, tenant_id, application_id)
                        if modules.enabled(session, tenant_id, "doku") else [])  # fmt: skip
            finally:
                session.close()
            _round_start(tenant_id, number, todo, application_id)
            if todo:  # 0.139.1: im Log sichtbar (bis dahin still bis zu den Verbindungen)
                log.info("Mandant %s: Durchgang %d des Vorrechnens - %d Staende von %d "
                         "Applikationen", tenant_id, number, len(todo),
                         len({t.key[0] for t in todo}))  # fmt: skip
            over, paused = False, 0
            for position, item in enumerate(todo, 1):
                if paused := _open_runs(factory, tenant_id):
                    log.info("Mandant %s: Vorrechnen nach %d von %d Staenden angehalten - "
                             "%d Laeufe offen", tenant_id, position - 1, len(todo),
                             paused)  # fmt: skip
                    break
                if budget and time.monotonic() - started > budget:
                    log.info("Mandant %s: Vorrechnen nach %d Staenden beendet (Zeitbudget)",
                             tenant_id, done)  # fmt: skip
                    over = True
                    break
                app_id, kind, var = item.key
                session = factory()
                try:
                    params = {}
                    if _fresh(session, item):
                        item = None
                    elif item.sid is not None:
                        data = session.scalar(select(DocSnapshot.data)
                                              .where(DocSnapshot.id == item.sid))  # fmt: skip
                        params = _unpack(data)["params"] if data else {}
                finally:
                    session.close()
                t_item = time.monotonic()
                if item is not None and rebuild(
                        factory, app_id, kind, var,
                        Params(params.get("rule_ids"), bool(params.get("names"))),
                        var.split("|", 1)[0], viewed=False):  # fmt: skip
                    done += 1
                    log.info("Vorrechnen %d/%d: Applikation %s %s in %.1f s", position, len(todo),
                             app_id, "Doku" if kind == DOC else "Verbindungen",
                             time.monotonic() - t_item)  # fmt: skip
                _round_step(tenant_id, app_id)
            if paused:
                held = False
                return _pause(tenant_id, paused, done)
            if number > 1 or todo:
                log.info("Mandant %s: Durchgang %d des Vorrechnens fertig (%d Staende)",
                         tenant_id, number, len(todo))  # fmt: skip
            if over:
                with _lock:
                    _warm_again.discard(tenant_id)
            if _end_warm(tenant_id):
                held = False
                return done
            application_id = None  # Anstoss waehrenddessen: der ganze Mandant
    finally:
        with _lock:
            _rounds.pop(tenant_id, None)
        if held:  # Fehler im Vorrechnen selbst: freigeben
            with _lock:
                _warm_again.discard(tenant_id)
                _warm_locks[tenant_id].release()


def _open_runs(factory: sessionmaker[Session], tenant_id: int) -> int:
    from app.store import runs  # spaet: runs gehoert zum Worker

    with factory() as session:
        return runs.open_count(session, tenant_id)


def _pause(tenant_id: int, count: int, done: int) -> int:
    """Vorrechnen anhalten, solange Laeufe offen sind (0.141.0, Anwender: „Die Neu berechnungen
    verhindern schnelles einlesen“): nach dem letzten Lauf geht es weiter (`app_refresh`)."""
    from app.store import app_refresh

    app_refresh.wait_for_runs(tenant_id, count)
    with _lock:
        _warm_again.discard(tenant_id)
        _warm_locks[tenant_id].release()
    return done


def refresh_app(factory: sessionmaker[Session], tenant_id: int, application_id: int) -> int:
    """Staende einer Applikation gleich neu (0.139.0) - nach ihrem Speichern bzw. nach dem
    Einlesen ihrer Regeln, ohne auf einen laufenden Durchgang des Mandanten zu warten und ohne
    einen neuen fuer alle anzustossen. Was gerade rechnet, rechnet nicht doppelt (`rebuild`).
    -> Anzahl neu gerechnet."""
    settings = get_settings()
    if not enabled() or settings.doc_warm_days <= 0:
        return 0
    from app import modules  # spaet: Module kennen die Store-Dateien

    _carry(factory, tenant_id, application_id)  # 0.141.0: nicht betroffene uebernehmen
    session = factory()
    try:
        if not modules.enabled(session, tenant_id, "doku"):
            return 0
        todo = []
        for item in _todo(session, tenant_id, application_id):
            params = {}
            if item.sid is not None:
                data = session.scalar(select(DocSnapshot.data).where(DocSnapshot.id == item.sid))
                params = _unpack(data)["params"] if data else {}
            todo.append((item.key, params))
    finally:
        session.close()
    done = 0
    for (app_id, kind, var), params in todo:
        if rebuild(factory, app_id, kind, var,
                   Params(params.get("rule_ids"), bool(params.get("names"))),
                   var.split("|", 1)[0], viewed=False):  # fmt: skip
            done += 1
    return done


def warm_all(factory: sessionmaker[Session]) -> None:
    """Alle Mandanten vorrechnen - nach dem Start (0.133.2: nach einem Neustart bzw. Update
    rechnete bis zum naechsten Lauf nichts vor)."""
    from app.models import Tenant

    with factory() as session:
        tenants = list(session.scalars(select(Tenant.id).order_by(Tenant.id)))
    for tid in tenants:
        try:
            warm(factory, tid)
        except Exception:  # z. B. Datenbank noch nicht migriert
            log.exception("Mandant %s: Vorrechnen nach dem Start fehlgeschlagen", tid)
