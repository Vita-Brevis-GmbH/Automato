"""Statische Routen je Geraet und VRF (1.19.0) - nur lesend, mandantsgescoped (viewer).

- `GET /static-routes`  statische Routen mit Zustand (aktiv, Backup, ueberdeckt, nicht
                        installiert, deaktiviert, unbekannt), Next-Hop mit Namen aus dem IPAM und
                        Gegenstelle, Befunden je Route (wie die Pruefungen) und dem, was nicht
                        pruefbar war; Filter: Text (`q`), enthaelt IP (`ip`), Kontext
                        (`context` = `<Quellsystem>|<Kontext>`), VRF, Zustand, nur mit Befund;
                        seitenweise (`limit`, `offset`). Mit Kunde (0.76.0) nur in seinen
                        Kontexten, in geteilten nur die Routen zu seinen Netzen.

Die Pruefung selbst steht in `store/static_routes.py`; die Befunde rechnet der Abgleich
(`app/checks/analyse/statisch_*.py`).
"""

import ipaddress

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.customer_filter import customer_view
from app.audit import reads
from app.auth.deps import require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.i18n import L
from app.store import customer_infra, paths, static_routes, topology
from app.store.customer_infra import SHARED

router = APIRouter(prefix="/static-routes", tags=["routing"])


def _text(s: static_routes.Static) -> str:
    """Freitext einer Route: Praefix, Next-Hop, Interface, Geraet, Kommentar, Zustand."""
    parts = [s.prefix, s.next_hop, s.interface, s.label, s.source, s.node, s.comment, s.seq,
             s.l3out, s.state, static_routes.state_text(s.state), s.origin,
             (s.target or {}).get("name"), s.reason]  # fmt: skip
    return " ".join(str(p) for p in parts if p).lower()


def _contains(s: static_routes.Static, point) -> bool:
    if s.target and s.target.get("kind") == "object":
        nets = [ipaddress.ip_network(x, strict=False) for x in s.resolved]
        return any(n.version == point.version and point in n for n in nets)
    return s.family == point.version and s.lo <= int(point) <= s.hi


@router.get("")
def list_static_routes(
    tenant_id: int | None = None,
    q: str | None = Query(None, max_length=200, description="Praefix, Next-Hop, Geraet …"),
    ip: str | None = Query(None, max_length=64, description="Routen mit dieser IP"),
    context: str | None = Query(None, max_length=400, description="<Quellsystem>|<Kontext>"),
    vrf: str | None = Query(None, pattern=r"^(\d{1,3}|mgmt)$"),
    state: str | None = Query(None, pattern="^(" + "|".join(static_routes.STATES) + ")$"),
    issues: bool = False,
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    tid = tenant_scope(principal, tenant_id)
    if tid is None:
        raise HTTPException(status_code=422, detail="tenant_id required")
    reads.maybe_audit_read(session, principal, tid, "routing")
    infra = customer_infra.of(session, customer_view(session, principal, tid, customer_id))
    point = None
    if ip:
        try:
            point = ipaddress.ip_address(ip.strip().split("/")[0])
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=L("ip: keine gültige IP", "ip: not a valid IP")
            ) from exc
    graph = paths.Graph(session, tid)
    found = static_routes.evaluate(session, tid, graph)
    rows = found.items
    if infra is not None:  # Kunde: eigene Kontexte, in geteilten nur seine Netze
        mine = []
        for s in rows:
            mark = infra.context_mark(s.source_system_id, s.context)
            if mark is None or (mark == SHARED and not infra.overlaps(s.prefix)):
                continue
            mine.append(s)
        rows = mine
    # Auswahl je Knoten wie in der Weg-Suche (ACI: alle Border-Leafs einer VRF)
    labels: dict[str, str] = {}
    for s in rows:
        node = graph.nodes.get((s.source_system_id, s.context))
        labels.setdefault(s.node_key, node.label if node is not None else s.label)
    contexts = sorted(labels.items(), key=lambda x: x[1].lower())
    vrfs = sorted({str(s.vrf) for s in rows if s.vrf != 0})
    shown = rows
    if context:
        shown = [s for s in shown if s.node_key == context]
    if vrf is not None:
        shown = [s for s in shown if str(s.vrf) == vrf]
    if state:
        shown = [s for s in shown if s.state == state]
    if issues:
        shown = [s for s in shown if static_routes.issues_of(s)]
    if point is not None:  # laengstes Praefix zuerst; Adressobjekt ueber seine Netze in der RIB
        shown = sorted((s for s in shown if _contains(s, point)), key=lambda s: -s.prefix_len)
    for token in (q or "").lower().split():
        shown = [s for s in shown if token in _text(s)]
    page = shown[offset : offset + limit]
    ifaces = topology.Interfaces(session, {s.source_system_id for s in page})
    out = []
    for s in page:
        row = s.out()
        hop = ifaces.hops(s.source_system_id, [{"ip": s.next_hop, "interface": s.interface}])[0]
        row["zone"] = hop.get("zone")
        row["resolved_next_hop"] = graph.resolve(s.next_hop) if s.next_hop else {}
        if infra is not None:
            row["shared"] = infra.context_mark(s.source_system_id, s.context) == SHARED
        out.append(row)
    return {
        "rows": out,
        "total": len(shown),
        "summary": found.summary(rows),
        "contexts": [{"key": k, "label": label} for k, label in contexts],
        "vrfs": vrfs,
    }
