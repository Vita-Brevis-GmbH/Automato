"""Routing-Filter (BGP) vorab berechnet (0.131.0, wie die Doku, ADR 0037).

Anwender 2026-11-04: „Das Laden der Routingfilter dauert lange, kann man dies nicht wie bei der
Doku machen und vorab berechnen?“ - je Nachbar baute jede Anfrage Filter, Zonenplan und Wege neu
(`bgp_filters.Filters`, `bgp_zones.ZoneSoll`, `paths.Graph`).

- **Stand** je Mandant und Sprache im Prozess: der Bericht je BGP-Nachbar (Soll/Ist), das Soll
  aus den Zonen je Nachbar und Richtung und das Soll je L3Out - in einem Durchgang mit einem
  gemeinsamen Zonenplan.
- **Fingerabdruck** wie die Doku (`doc_snapshots._tenant_parts`: letzter Lauf, Quellsysteme,
  letzte Aenderung an der Konfiguration) und Version der Plattform.
- **Sofort zeigen** (`serve`): passt der Stand, kommt er sofort; sonst wird die Anfrage wie
  bisher einzeln gerechnet und der Stand des Mandanten hinter der Antwort neu.
- **Vorrechnen** (`warm`): nach Laeufen und Aenderungen (Zonen, Verbund, L3Outs) im Hintergrund,
  je Mandant hoechstens ein Durchgang zugleich, in den zuletzt verlangten Sprachen.

Abgeleitete Daten, nur lesend; `doc_snapshots=false` schaltet auch dies ab.
"""

import hashlib
import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import BackgroundTasks
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import __version__
from app.i18n import DEFAULT, lang, use
from app.models import TopoL3Out, TopoPeer

log = logging.getLogger(__name__)

PEER, ZONES_IN, ZONES_OUT, L3OUT_ZONES = "peer", "zones_in", "zones_out", "l3out_zones"
MAX_BUNDLES = 20  # Mandant x Sprache im Speicher

_lock = threading.Lock()
_bundles: dict[tuple[int, str], "Bundle"] = {}
_langs: dict[int, set[str]] = defaultdict(lambda: {DEFAULT})
_building: set[int] = set()
_tenant_locks: dict[int, threading.Lock] = defaultdict(threading.Lock)


@dataclass
class Bundle:
    fingerprint: str
    seconds: float = 0.0
    built: float = field(default_factory=time.time)
    items: dict[str, dict[int, dict]] = field(default_factory=lambda: defaultdict(dict))


def enabled() -> bool:
    from app.store import doc_snapshots

    return doc_snapshots.enabled()


def fingerprint(session: Session, tenant_id: int) -> str:
    from app.store import doc_snapshots  # gleiche Eingaben wie die Doku

    parts = [__version__, tenant_id, lang(), doc_snapshots._tenant_parts(session, tenant_id)]
    return hashlib.sha256(repr(parts).encode()).hexdigest()


# -- Ergebnisse (gleich fuer Anfrage und Vorrechnen) ------------------------------------------


def peer_report(session: Session, tid: int, p: TopoPeer, f, graph) -> dict:
    """Antwort von GET /routing/bgp/peers/{id}."""
    base = {"id": p.id, "source_system_id": p.source_system_id, "node": p.node,
            "context": p.context, "label": graph.label(p.source_system_id, p.node, p.context)[1],
            **graph.resolve(p.peer_ip)}  # fmt: skip
    key = (p.source_system_id, p.node, p.context)
    if key not in f.configs or (p.attrs or {}).get("config") is None:
        return {**base, "peer_ip": p.peer_ip, "state": p.state, "configured": False}
    return {**base, **f.report(p), "configured": True}


def peer_zones(zs, p: TopoPeer, way: str) -> dict:
    """Antwort von GET /routing/bgp/peers/{id}/zones."""
    return {"id": p.id, "peer_ip": p.peer_ip, **zs.for_peer(p, way=way)}


def l3out_zones(zs, o: TopoL3Out) -> dict:
    """Antwort von GET /routing/bgp/l3outs/{id}/zones."""
    return {"id": o.id, "l3out": o.name, **zs.for_l3out(o)}


def build(session: Session, tenant_id: int) -> Bundle:
    """Alle Nachbarn und L3Outs des Mandanten in einem Durchgang."""
    from app.store import bgp_filters, bgp_zones, paths  # spaet: paths -> ...

    start = time.monotonic()
    b = Bundle(fingerprint(session, tenant_id))
    if session.scalar(select(TopoPeer.id).where(TopoPeer.tenant_id == tenant_id).limit(1)) is None \
            and session.scalar(select(TopoL3Out.id).where(TopoL3Out.tenant_id == tenant_id)
                               .limit(1)) is None:  # fmt: skip
        return b  # ohne Nachbarn und L3Outs nichts zu rechnen
    f = bgp_filters.Filters(session, tenant_id)
    graph = paths.Graph(session, tenant_id)
    zs = bgp_zones.ZoneSoll(session, tenant_id, f)
    peers = session.scalars(select(TopoPeer).where(TopoPeer.tenant_id == tenant_id)
                            .order_by(TopoPeer.id)).all()  # fmt: skip
    for p in peers:
        b.items[PEER][p.id] = jsonable_encoder(peer_report(session, tenant_id, p, f, graph))
        b.items[ZONES_IN][p.id] = jsonable_encoder(peer_zones(zs, p, "in"))
        b.items[ZONES_OUT][p.id] = jsonable_encoder(peer_zones(zs, p, "out"))
    for o in session.scalars(select(TopoL3Out).where(TopoL3Out.tenant_id == tenant_id)
                             .order_by(TopoL3Out.id)):  # fmt: skip
        b.items[L3OUT_ZONES][o.id] = jsonable_encoder(l3out_zones(zs, o))
    b.seconds = time.monotonic() - start
    return b


def _store(tenant_id: int, code: str, b: Bundle) -> None:
    with _lock:
        _bundles[(tenant_id, code)] = b
        while len(_bundles) > MAX_BUNDLES:  # aeltester Stand faellt weg
            del _bundles[min(_bundles, key=lambda k: _bundles[k].built)]


# -- Anfrage und Vorrechnen --------------------------------------------------------------------


def serve(session: Session, background: BackgroundTasks | None,
          factory: sessionmaker[Session] | None, tenant_id: int, kind: str, item_id: int,
          compute: Callable[[], dict]) -> dict:  # fmt: skip
    """Stand, wenn er passt; sonst einzeln rechnen und den Stand hinter der Antwort neu."""
    if not enabled():
        return compute()
    code = lang()
    with _lock:
        _langs[tenant_id].add(code)
        b = _bundles.get((tenant_id, code))
    if b is not None and item_id in b.items.get(kind, {}):
        if b.fingerprint == fingerprint(session, tenant_id):
            return b.items[kind][item_id]
    if background is not None and factory is not None:
        schedule(background, factory, tenant_id)
    return compute()


def schedule(background: BackgroundTasks, factory: sessionmaker[Session], tenant_id: int) -> bool:
    """Vorrechnen vormerken; False, wenn fuer den Mandanten schon eines laeuft."""
    with _lock:
        if tenant_id in _building:
            return False
        _building.add(tenant_id)
    background.add_task(_warm_claimed, factory, tenant_id)
    return True


def warm(factory: sessionmaker[Session], tenant_id: int) -> int:
    """Stand des Mandanten in den zuletzt verlangten Sprachen neu, wo er nicht mehr passt (nach
    Laeufen und Aenderungen, im Hintergrund). -> Anzahl neu gerechnet."""
    if not enabled():
        return 0
    with _lock:
        if tenant_id in _building:
            return 0
        _building.add(tenant_id)
    return _warm_claimed(factory, tenant_id)


def _warm_claimed(factory: sessionmaker[Session], tenant_id: int) -> int:
    done = 0
    try:
        with _tenant_locks[tenant_id]:
            with _lock:
                codes = sorted(_langs[tenant_id])
            for code in codes:
                with use(code):
                    session = factory()
                    try:
                        old = _bundles.get((tenant_id, code))
                        if old is not None and old.fingerprint == fingerprint(session, tenant_id):
                            continue
                        _store(tenant_id, code, build(session, tenant_id))
                        done += 1
                    except Exception:
                        log.exception("Mandant %s: Routing-Filter vorrechnen fehlgeschlagen",
                                      tenant_id)  # fmt: skip
                    finally:
                        session.rollback()
                        session.close()
    finally:
        with _lock:
            _building.discard(tenant_id)
    return done


def state(tenant_id: int) -> dict:
    """Stand des Mandanten je Sprache (Tests, Uebersicht)."""
    with _lock:
        return {code: {"seconds": round(b.seconds, 2), "peers": len(b.items.get(PEER, {})),
                       "l3outs": len(b.items.get(L3OUT_ZONES, {}))}
                for (tid, code), b in _bundles.items() if tid == tenant_id}  # fmt: skip


def clear() -> None:
    """Alles verwerfen (Tests)."""
    with _lock:
        _bundles.clear()
        _langs.clear()
        _building.clear()
