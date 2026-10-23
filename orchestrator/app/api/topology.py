"""Topologie-Modell (0.50.0, ADR 0019): `GET /topology` - Knoten und Kanten der Umgebung mit
Herkunft (abgeleitet, von Hand, bestaetigt) und den Plausibilitaets-Befunden
(`store/topo_model.py`). Nur lesend; nicht fuer Kunden-Benutzer (zeigt die ganze Umgebung)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.audit import reads
from app.auth.deps import require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.store import topo_model

router = APIRouter(prefix="/topology", tags=["topology"])


@router.get("")
def topology(
    tenant_id: int | None = None,
    node: str | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Modell des Mandanten; `node` = nur dieser Knoten mit seinen Nachbarn und Kanten."""
    tid = tenant_scope(principal, tenant_id)
    if tid is None:
        raise HTTPException(status_code=422, detail="tenant_id required")
    reads.maybe_audit_read(session, principal, tid, "routing")
    model = topo_model.build(session, tid)
    if node is None:
        return model
    if node not in {n["id"] for n in model["nodes"]}:
        raise HTTPException(status_code=404, detail="node not found")
    edges = [e for e in model["edges"] if node in (e["a"], e["b"])]
    near = {node} | {e["a"] for e in edges} | {e["b"] for e in edges}
    issues = [i for i in model["issues"] if node in topo_model._issue_nodes(i)]
    return {**model, "nodes": [n for n in model["nodes"] if n["id"] in near], "edges": edges,
            "issues": issues}  # fmt: skip
