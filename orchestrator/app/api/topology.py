"""Topologie-Modell (0.50.0, ADR 0019): `GET /topology` - Knoten und Kanten der Umgebung mit
Herkunft (abgeleitet, von Hand, bestaetigt) und den Plausibilitaets-Befunden
(`store/topo_model.py`). Nur lesend; nicht fuer Kunden-Benutzer (zeigt die ganze Umgebung)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.customer_filter import customer_view
from app.audit import reads
from app.auth.deps import require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.store import customer_infra, topo_model

router = APIRouter(prefix="/topology", tags=["topology"])


def _customer_cut(model: dict, infra: customer_infra.Infra) -> dict:
    """Ausschnitt eines Kunden (0.76.0): seine Knoten (VDOM/VRF bzw. Firewall seiner Kontexte),
    die Netze, Router und Anbindungen direkt daran; geteilte markiert."""
    keep: dict[str, bool] = {}
    for n in model["nodes"]:
        if n.get("source_system_id") is not None and n.get("context") is not None:
            mark = infra.context_mark(n["source_system_id"], n["context"])
            if mark:
                keep[n["id"]] = mark == customer_infra.SHARED
    for e in model["edges"]:
        for a, b in ((e["a"], e["b"]), (e["b"], e["a"])):
            if a in keep and b not in keep and not b.split("|")[0].isdigit():
                mark = infra.net_mark(e.get("network")) if e.get("network") else None
                keep[b] = mark != customer_infra.OWN  # Netz, Router, Anbindung daneben
    nodes = [{**n, "shared": keep[n["id"]]} for n in model["nodes"] if n["id"] in keep]
    edges = [e for e in model["edges"] if e["a"] in keep and e["b"] in keep]
    issues = [i for i in model["issues"] if set(topo_model._issue_nodes(i)) <= set(keep)]
    return {**model, "nodes": nodes, "edges": edges, "issues": issues}


@router.get("")
def topology(
    tenant_id: int | None = None,
    node: str | None = None,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Modell des Mandanten; `node` = nur dieser Knoten mit seinen Nachbarn und Kanten; mit
    Kunde (0.76.0) nur sein Ausschnitt."""
    tid = tenant_scope(principal, tenant_id)
    if tid is None:
        raise HTTPException(status_code=422, detail="tenant_id required")
    reads.maybe_audit_read(session, principal, tid, "routing")
    model = topo_model.build(session, tid)
    infra = customer_infra.of(session, customer_view(session, principal, tid, customer_id))
    if infra is not None:
        model = _customer_cut(model, infra)
    if node is None:
        return model
    if node not in {n["id"] for n in model["nodes"]}:
        raise HTTPException(status_code=404, detail="node not found")
    edges = [e for e in model["edges"] if node in (e["a"], e["b"])]
    near = {node} | {e["a"] for e in edges} | {e["b"] for e in edges}
    issues = [i for i in model["issues"] if node in topo_model._issue_nodes(i)]
    return {**model, "nodes": [n for n in model["nodes"] if n["id"] in near], "edges": edges,
            "issues": issues}  # fmt: skip
