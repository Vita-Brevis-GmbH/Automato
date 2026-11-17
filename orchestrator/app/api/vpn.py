"""VPN-Tunnel (IPsec) der FortiGates (1.19.0, ADR 0063) - nur lesend, mandantsgescoped (viewer).

- `GET /vpn/tunnels`       Tunnel mit Zustand, Gegenstelle (gelesene FortiGate bzw. nicht),
                           Phase 2 (up/gesamt), Routen ueber den Tunnel und Selektoren; Filter
                           Suche, Zustand, Quellsystem; dazu, wo IPsec nicht lesbar war
- `GET /vpn/tunnels/{id}`  ein Tunnel: Phase 2 mit Selektoren, Dial-up-Gegenstellen, Routen ueber
                           den Tunnel (deckt ein Selektor sie?), Netze dahinter, Gegenstelle

Mit Kunde (oben gewaehlt bzw. Kunden-Benutzer, ADR 0018) nur Tunnel in seinen Kontexten
(VDOMs). Rein lesend - nichts geht an ein Geraet.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.customer_filter import customer_view
from app.audit import reads
from app.auth.deps import require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.store import customer_infra
from app.store import vpn as store

router = APIRouter(prefix="/vpn", tags=["vpn"])


def _tenant(session: Session, principal: Principal, tenant_id: int | None) -> int:
    tid = tenant_scope(principal, tenant_id)
    if tid is None:
        raise HTTPException(status_code=422, detail="tenant_id required")
    reads.maybe_audit_read(session, principal, tid, "routing")  # wie das Routing
    return tid


def _keep(session: Session, principal: Principal, tid: int, customer_id: int | None):
    """Ausschnitt eines Kunden: Tunnel in seinen (eigenen bzw. geteilten) Kontexten."""
    infra = customer_infra.of(session, customer_view(session, principal, tid, customer_id))
    if infra is None:
        return None
    return lambda sid, ctx: bool(infra.context_mark(sid, ctx))


@router.get("/tunnels")
def tunnels(
    tenant_id: int | None = None,
    customer_id: int | None = None,
    q: str | None = None,
    status: str | None = None,
    system: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    tid = _tenant(session, principal, tenant_id)
    keep = _keep(session, principal, tid, customer_id)
    return store.listing(session, tid, q=q, status=status, system=system, keep=keep)


@router.get("/tunnels/{tunnel_id}")
def tunnel(
    tunnel_id: int,
    tenant_id: int | None = None,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    tid = _tenant(session, principal, tenant_id)
    keep = _keep(session, principal, tid, customer_id)
    found = store.detail(session, tid, tunnel_id)
    if found is None or (keep is not None and not keep(found["source_system_id"],
                                                        found["context"])):  # fmt: skip
        raise HTTPException(status_code=404, detail="tunnel not found")
    return found
