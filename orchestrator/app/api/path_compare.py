"""Wege vergleichen (1.19.0, ADR 0067): ein L3-Weg gegen L1/L2 (`store/path_compare`).

- `GET /path-compare/trace`     Weg wie in der Weg-Suche (`/routing/trace`), mit `connection=true`
                                wie die Verbindungen einer Applikation (ab der Quelle, sonst ab
                                dem Eingang nach dem Rueckweg bzw. dem Geraet der Regel `starts`);
                                je Weg die Abschnitte mit den drei Pruefungen und die Summe.
- `GET /path-compare/sections`  alle L3-Abschnitte des Mandanten (Grundlage der Befunde), Filter
                                nach Zustand und Text.

Lesen = `viewer`, nicht fuer Kunden-Benutzer (zeigt die Infrastruktur). Rein lesend; nichts davon
wird gespeichert.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api import routing as routing_api
from app.auth.deps import require_viewer
from app.auth.principal import Principal
from app.db import get_session
from app.i18n import L
from app.store import app_paths, l2_topo, path_compare, paths, policy_check

router = APIRouter(prefix="/path-compare", tags=["path-compare"])
EXIT_REF = routing_api.EXIT_REF
STATES = ("ok", "deviation", "partial", "unchecked", "vpn", "internal")


def _tenant(session: Session, principal: Principal, tenant_id: int | None) -> int:
    if principal.is_customer:  # Kabel, Switches und Ports der Umgebung
        raise HTTPException(status_code=403, detail=L("nicht für Kunden-Benutzer",
                                                      "not for customer users"))  # fmt: skip
    return routing_api._tenant(session, principal, tenant_id)


@router.get("/trace")
def compare_trace(
    dst: str | None = None,
    src: str | None = None,
    start: str | None = Query(None, description="Startpunkt: <Quellsystem>|<Kontext>"),
    slack: int = Query(1, ge=0, le=3),
    service: str | None = Query(None, max_length=200),
    src_exit: str | None = Query(None, max_length=100, pattern=EXIT_REF),
    dst_exit: str | None = Query(None, max_length=100, pattern=EXIT_REF),
    start_port: str | None = Query(None, max_length=100),
    service_at: str = Query("client", pattern="^(client|lb|server)$"),
    connection: bool = Query(False, description="Weg wie bei Verbindungen Ende zu Ende"),
    starts: Annotated[list[str] | None, Query(max_length=20)] = None,
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Weg der Weg-Suche bzw. einer Verbindung gegen L1/L2: je Abschnitt VLAN erlaubt, Kabel und
    Nachbarn, Spanning Tree - mit Ports, Geraeten und dem, was nicht pruefbar ist (gezaehlt)."""
    tid = _tenant(session, principal, tenant_id)
    if connection:
        if not dst or not src:
            raise HTTPException(status_code=422, detail=L("src und dst angeben",
                                                          "specify src and dst"))  # fmt: skip
        routing_api._endpoint(dst, "dst")
        routing_api._endpoint(src, "src")
        wanted, _note = routing_api._service(session, tid, service)
        graph = paths.Graph(session, tid)
        checks = policy_check.Checks.load(session, tid)
        result, _start = app_paths.connection_trace(session, tid, graph, checks, src, dst,
                                                    wanted, starts or [])  # fmt: skip
    else:
        result, _graph, src, dst, _wanted = routing_api._traced(
            session, tid, dst, src, start, slack, service, src_exit, dst_exit, start_port,
            service_at)  # fmt: skip
    l2_topo.annotate(session, tid, result, src, dst)  # Anschluss von Quelle und Ziel
    out = path_compare.Comparer(session, tid).trace(result)
    out["notes"] = result.get("notes") or []
    out["query"] = {"src": src, "dst": dst, "service": service}
    return out


@router.get("/sections")
def compare_sections(
    state: str | None = Query(None, description="nur Abschnitte in diesem Zustand"),
    q: str | None = Query(None, max_length=200, description="Geraet, Interface, VLAN"),
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Alle L3-Abschnitte des Mandanten mit den drei Pruefungen - wie der Abgleich sie prueft."""
    if state is not None and state not in STATES:
        raise HTTPException(status_code=422, detail="state")
    tid = _tenant(session, principal, tenant_id)
    data = path_compare.sections(session, tid).out()
    items = data["items"]
    if state:
        items = [s for s in items if s["state"] == state]
    if q and q.strip():
        needle = q.strip().lower()
        items = [s for s in items
                 if needle in s["label"].lower()
                 or needle == str(s.get("vlan") or "")
                 or needle in ((s.get("segment") or {}).get("text") or "").lower()]  # fmt: skip
    data["items"] = items
    data["total"] = len(items)
    return data
