"""Prüfung: Plausibilität der Topologie (0.50.0, `store/topo_model.checks`).

Steckbare Prüfung (0.110.0, ADR 0039) - aus `store/reconcile.py` hierher verschoben."""

from sqlalchemy.orm import Session

from app.checks import Check
from app.models import (
    FindingType,
    Severity,
    SubjectKind,
)
from app.store.reconcile import Issue

_TOPO = {  # Befund-Typ -> (Gegenstand, Schwere)
    "topo_duplicate_ip": (SubjectKind.SUBNET, Severity.WARNING),
    "topo_mask_mismatch": (SubjectKind.SUBNET, Severity.WARNING),
    "topo_route_loop": (SubjectKind.NODE, Severity.ERROR),
    "topo_static_unused": (SubjectKind.NODE, Severity.INFO),
    "topo_bgp_down": (SubjectKind.NODE, Severity.WARNING),
}


def _topology_issues(session: Session, tenant_id: int) -> list[Issue]:
    """Plausibilitaet der Topologie (0.50.0, `store/topo_model.checks`): doppelte IP und
    abweichende Maske im selben Netz, Routing-Schleifen, statische Routen ohne Wirkung,
    BGP-Sitzungen nicht aufgebaut."""
    from app.store import topo_model

    out: list[Issue] = []
    for item in topo_model.checks(session, tenant_id):
        kind, severity = _TOPO[item["type"]]
        node = item.get("node") or next(iter(item.get("nodes") or []), None)
        sid = int(node.split("|")[0]) if isinstance(node, str) else None
        detail = {k: v for k, v in item.items() if k not in ("type", "subject")}
        if kind is not SubjectKind.NODE:
            sid = None  # mehrere Geraete im selben Netz
        out.append(Issue(FindingType(item["type"]), kind, item["subject"], sid, severity,
                         detail=detail))  # fmt: skip
    return out


CHECK = Check(
    "topologie",
    "analyse",
    ("Topologie", "Topology"),
    (
        "Doppelte IP und abweichende Maske im selben Netz, Routing-Schleifen, statische Routen "
        "ohne Wirkung, BGP nicht aufgebaut",
        "Duplicate IP and differing mask in the same network, routing loops, static routes without "
        "effect, BGP not established",
    ),
    frozenset(
        {
            FindingType.TOPO_DUPLICATE_IP,
            FindingType.TOPO_MASK_MISMATCH,
            FindingType.TOPO_ROUTE_LOOP,
            FindingType.TOPO_STATIC_UNUSED,
            FindingType.TOPO_BGP_DOWN,
        }
    ),
    lambda session, tenant_id, cx: _topology_issues(session, tenant_id),
    order=60,
)
