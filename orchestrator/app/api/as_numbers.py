"""AS-Nummern im Routing einzeln (1.19.0, ADR 0066) - nur lesend, mandantsgescoped (viewer).

- `GET /routing/as-numbers`  je AS die gelesenen Geraete mit diesem AS (Firewalls, Router) und
                             die BGP-Nachbarn mit diesem AS (an welchem Geraet, Zustand, Art,
                             Namen aus dem IPAM, gelesenes Geraet dahinter), eigen bzw. fremd,
                             privat und die Reservierungen, die es abdecken; dazu reservierte
                             AS-Nummern, die nicht in Gebrauch sind. Filter: Nummer bzw. Text
                             (`q`), nur eigene bzw. fremde (`scope`). Mit Kunde (0.76.0) nur
                             Geraete und Nachbarn in seinen Kontexten.

Die Liste baut `store/as_numbers.py` aus den schon gelesenen BGP-Daten (ADR 0041).
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.customer_filter import customer_view
from app.audit import reads
from app.auth.deps import require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.store import as_numbers, customer_infra
from app.store import reservations as reservation_store

router = APIRouter(prefix="/routing/as-numbers", tags=["routing"])


def _text(e: as_numbers.AsEntry, index: as_numbers.Index) -> str:
    parts: list = [e.asn, f"as{e.asn}"]
    for d in e.devices:
        parts += [d.label, d.source, d.node, d.router_id]
    for n in e.neighbors:
        parts += [n.peer_ip, n.at.label, n.at.l3out, n.description,
                  *index.graph.names(n.peer_ip), index.graph.owner_label(n.peer_ip)]  # fmt: skip
    return " ".join(str(p) for p in parts if p).lower()


@router.get("")
def list_as_numbers(
    tenant_id: int | None = None,
    q: str | None = Query(None, max_length=200, description="AS, Geraet, Nachbar …"),
    scope: Literal["own", "foreign"] | None = None,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    tid = tenant_scope(principal, tenant_id)
    if tid is None:
        raise HTTPException(status_code=422, detail="tenant_id required")
    reads.maybe_audit_read(session, principal, tid, "routing")
    infra = customer_infra.of(session, customer_view(session, principal, tid, customer_id))
    index = as_numbers.Index(session, tid)
    reserved = reservation_store.rows(session, tid, "asn")

    def visible(sid: int, context: str) -> bool:
        return infra is None or infra.context_mark(sid, context) is not None

    needle = (q or "").strip().lower()
    items, summary = [], {"total": 0, "own": 0, "foreign": 0, "private": 0, "reserved": 0}
    for number, e in sorted(index.entries.items()):
        devices = [d for d in e.devices if visible(d.source_system_id, d.context)]
        neighbors = [n for n in e.neighbors if visible(n.at.source_system_id, n.at.context)]
        if not devices and not neighbors:
            continue
        covering = [r for r in reserved if r.low <= number <= r.high]
        if scope == "own" and not e.own or scope == "foreign" and e.own:
            continue
        if needle and needle.lstrip("as").strip() != str(number) and needle not in _text(e, index):
            continue
        items.append({
            "asn": number,
            "own": e.own,
            "private": e.private,
            "devices": [d.out() for d in devices],
            "neighbors": [index.neighbor_out(n) for n in neighbors],
            "established": sum(1 for n in neighbors if n.established),
            "reservations": [{"id": r.id, "label": reservation_store.label(r.kind, r.low, r.high),
                              "purpose": r.purpose, "by": r.created_by} for r in covering],
        })  # fmt: skip
        summary["total"] += 1
        summary["own" if e.own else "foreign"] += 1
        summary["private"] += int(e.private)
        summary["reserved"] += int(bool(covering))
    used = set(index.entries)
    free = [{"id": r.id, "label": reservation_store.label(r.kind, r.low, r.high),
             "low": r.low, "high": r.high, "purpose": r.purpose, "by": r.created_by,
             "used": sorted(n for n in used if r.low <= n <= r.high)}
            for r in reserved]  # fmt: skip
    return {"items": items, "summary": summary, "reservations": free}
