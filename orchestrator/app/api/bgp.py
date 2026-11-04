"""BGP-Filter und Route Control (0.113.0, ADR 0041) - nur lesend, mandantsgescoped (viewer).

- `GET /routing/bgp/config`            BGP-Prozess je FortiGate-Kontext (AS, Router-ID, network,
                                       aggregate, redistribute, Anzahl Filter) und konfigurierte
                                       Nachbarn ohne Sitzung im Monitor
- `GET /routing/bgp/peers/{id}`        Soll/Ist eines BGP-Nachbarn: Filter ein/aus, erwartet,
                                       erhalten und (berechnet) angekuendigt
- `GET /routing/bgp/peers/{id}/zones`  Soll aus den Zonen dahinter (0.114.0): Zone am Nachbarn,
                                       Zonen dahinter, Prefix-Liste, Abweichungen, CLI-Vorschlag
- `GET /routing/bgp/l3outs/{id}`       Route Control eines ACI-L3Outs: Import (erwartet), erhalten,
                                       Export
- `GET /routing/bgp/filtered`          Netze der Eingangsfilter fuer die Router einer Anbindung -
                                       Vorschlag fuer „Netze dahinter“

Seit 0.131.0 kommen Nachbar, Soll aus den Zonen und Soll je L3Out aus dem vorab berechneten
Stand (`bgp_cache`), wenn er passt.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.customer_filter import customer_view
from app.audit import reads
from app.auth.deps import require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session, get_session_factory
from app.models import SourceSystem, TopoL3Out, TopoPeer
from app.store import bgp_cache, bgp_filters, bgp_zones, customer_infra, paths

router = APIRouter(prefix="/routing/bgp", tags=["routing"])


def _tenant(session: Session, principal: Principal, tenant_id: int | None) -> int:
    tid = tenant_scope(principal, tenant_id)
    if tid is None:
        raise HTTPException(status_code=422, detail="tenant_id required")
    reads.maybe_audit_read(session, principal, tid, "routing")
    return tid


def _visible(session: Session, principal: Principal, tid: int, customer_id: int | None,
             sid: int, context: str | None) -> bool:  # fmt: skip
    """Kunde oben gewaehlt (0.76.0): nur Kontexte seines Ausschnitts."""
    infra = customer_infra.of(session, customer_view(session, principal, tid, customer_id))
    return infra is None or infra.context_mark(sid, context) is not None


def _names(session: Session, tid: int) -> dict[int, str]:
    stmt = select(SourceSystem.id, SourceSystem.name).where(SourceSystem.tenant_id == tid)
    return dict(session.execute(stmt).all())


@router.get("/config")
def config(
    tenant_id: int | None = None,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    tid = _tenant(session, principal, tenant_id)
    infra = customer_infra.of(session, customer_view(session, principal, tid, customer_id))
    f = bgp_filters.Filters(session, tid)
    peers = f.peers()
    alone = bgp_filters.configured_only(f, peers)
    graph = paths.Graph(session, tid)
    names = _names(session, tid)
    out = []
    for (sid, node, ctx), cfg in sorted(f.configs.items()):
        if infra is not None and infra.context_mark(sid, ctx) is None:
            continue
        count: dict[str, int] = {}
        for kind, _family, _name in cfg.objects:
            count[kind] = count.get(kind, 0) + 1
        process = {k: v for k, v in cfg.process.items() if k not in ("neighbors", "groups")}
        out.append({
            "source_system_id": sid, "source": names.get(sid), "node": node, "context": ctx,
            "label": graph.label(sid, node, ctx)[1], **process,
            "groups": sorted(cfg.process.get("groups") or {}),
            "neighbors": len(cfg.process.get("neighbors") or {}),
            "objects": count,
            "configured_only": [{**a, **graph.resolve(a["peer_ip"])} for a in alone
                                if (a["source_system_id"], a["node"], a["context"])
                                == (sid, node, ctx)],
        })  # fmt: skip
    return out


@router.get("/peers/{peer_id}")
def peer(
    peer_id: int,
    background: BackgroundTasks,
    tenant_id: int | None = None,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
    factory: sessionmaker = Depends(get_session_factory),
) -> dict:
    tid = _tenant(session, principal, tenant_id)
    p = session.get(TopoPeer, peer_id)
    if (
        p is None
        or p.tenant_id != tid
        or not _visible(session, principal, tid, customer_id, p.source_system_id, p.context)
    ):
        raise HTTPException(status_code=404, detail="peer not found")  # fmt: skip

    def compute() -> dict:
        f = bgp_filters.Filters(session, tid)
        return bgp_cache.peer_report(session, tid, p, f, paths.Graph(session, tid))

    # 0.131.0: vorab berechnet (wie die Doku)
    return bgp_cache.serve(session, background, factory, tid, bgp_cache.PEER, p.id, compute)


@router.get("/peers/{peer_id}/zones")
def peer_zones(
    peer_id: int,
    background: BackgroundTasks,
    tenant_id: int | None = None,
    customer_id: int | None = None,
    way: Literal["in", "out"] = "in",
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
    factory: sessionmaker = Depends(get_session_factory),
) -> dict:
    """Soll des Nachbarn aus den Zonen (ADR 0041): `in` = Zonen dahinter (Stufe 2), `out` = Zonen
    diesseits fuer den Ausgangsfilter (Stufe 3) - Vorschlag, kein Schreibweg."""
    tid = _tenant(session, principal, tenant_id)
    p = session.get(TopoPeer, peer_id)
    if (
        p is None
        or p.tenant_id != tid
        or not _visible(session, principal, tid, customer_id, p.source_system_id, p.context)
    ):
        raise HTTPException(status_code=404, detail="peer not found")  # fmt: skip
    kind = bgp_cache.ZONES_OUT if way == "out" else bgp_cache.ZONES_IN
    return bgp_cache.serve(
        session,
        background,
        factory,
        tid,
        kind,
        p.id,
        lambda: bgp_cache.peer_zones(bgp_zones.ZoneSoll(session, tid), p, way),
    )


@router.get("/l3outs/{l3out_id}")
def l3out(
    l3out_id: int,
    tenant_id: int | None = None,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    tid = _tenant(session, principal, tenant_id)
    o = session.get(TopoL3Out, l3out_id)
    if (
        o is None
        or o.tenant_id != tid
        or not _visible(session, principal, tid, customer_id, o.source_system_id, o.vrf)
    ):
        raise HTTPException(status_code=404, detail="l3out not found")  # fmt: skip
    return {**bgp_filters.l3out_report(session, o), "source": _names(session, tid).get(
        o.source_system_id)}  # fmt: skip


@router.get("/l3outs/{l3out_id}/zones")
def l3out_zones(
    l3out_id: int,
    background: BackgroundTasks,
    tenant_id: int | None = None,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
    factory: sessionmaker = Depends(get_session_factory),
) -> dict:
    """Import Route Control eines L3Outs gegen die Zonen dahinter (ADR 0041 Stufe 3, 0.116.0) -
    Vorschlag, kein Schreibweg."""
    tid = _tenant(session, principal, tenant_id)
    o = session.get(TopoL3Out, l3out_id)
    if (
        o is None
        or o.tenant_id != tid
        or not _visible(session, principal, tid, customer_id, o.source_system_id, o.vrf)
    ):
        raise HTTPException(status_code=404, detail="l3out not found")  # fmt: skip
    return bgp_cache.serve(
        session,
        background,
        factory,
        tid,
        bgp_cache.L3OUT_ZONES,
        o.id,
        lambda: bgp_cache.l3out_zones(bgp_zones.ZoneSoll(session, tid), o),
    )


@router.get("/filtered")
def filtered(
    tenant_id: int | None = None,
    source_system_ids: Annotated[list[int] | None, Query()] = None,
    next_hops: Annotated[list[str] | None, Query()] = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Netze, die die Eingangsfilter fuer die Router der Anbindung erlauben (Vorschlag)."""
    tid = _tenant(session, principal, tenant_id)
    return bgp_filters.filtered(session, tid, source_system_ids or [], next_hops or [])
