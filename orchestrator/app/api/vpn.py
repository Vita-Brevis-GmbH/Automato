"""VPN-Tunnel (IPsec) der FortiGates (1.19.0, ADR 0063) - nur lesend, mandantsgescoped (viewer).

- `GET /vpn/tunnels`       Tunnel mit Zustand, Gegenstelle (gelesene FortiGate bzw. nicht),
                           Phase 2 (up/gesamt), Routen ueber den Tunnel und Selektoren; Filter
                           Suche, Zustand, Quellsystem; dazu, wo IPsec nicht lesbar war
- `GET /vpn/tunnels/{id}`  ein Tunnel: Phase 2 mit Selektoren, Dial-up-Gegenstellen, Routen ueber
                           den Tunnel (deckt ein Selektor sie?), Netze dahinter, Gegenstelle

- `PUT /vpn/pairs`          zwei Tunnel gelesener FortiGates von Hand koppeln (`coupled`) bzw.
                           eine automatische Kopplung loesen (`separated`) - Operator, Audit
                           `vpn_pair.set` (1.23.0, ADR 0063 Nachtrag 1.23.0)
- `DELETE /vpn/pairs/{id}` Kopplung von Hand bzw. Loesung entfernen (dann gilt wieder die
                           automatische Erkennung) - Operator, Audit `vpn_pair.deleted`

Mit Kunde (oben gewaehlt bzw. Kunden-Benutzer, ADR 0018) nur Tunnel in seinen Kontexten
(VDOMs). Nichts geht an ein Geraet: die Kopplung sind Laufzeitdaten in Automato; sie aendert
Wege, Verbindungen und Doku (Audit -> Fingerabdruck und Wege-Speicher, ADR 0037/0048).
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import audit
from app.api.customer_filter import customer_view
from app.audit import reads
from app.auth.deps import require_operator, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.i18n import L
from app.models import VpnPair
from app.store import customer_infra, vpn_kopplung
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
    found = store.detail(session, tid, tunnel_id, keep)
    if found is None or (keep is not None and not keep(found["source_system_id"],
                                                        found["context"])):  # fmt: skip
        raise HTTPException(status_code=404, detail="tunnel not found")
    return found


# -- Kopplung von Hand (1.23.0) ---------------------------------------------------------------


class PairIn(BaseModel):
    tenant_id: int
    a: int  # Kennung des Tunnels (aktueller Lauf)
    b: int
    mode: Literal["coupled", "separated"] = "coupled"


_INVALID_EN = {
    "beide Tunnel sind auf derselben Firewall": "both tunnels are on the same firewall",
    "zwei Dial-up-Tunnel verbinden sich nicht miteinander": "two dial-up tunnels never connect",
    "keine automatische Kopplung zwischen diesen Tunneln": (
        "no automatic coupling between these tunnels"),
}  # fmt: skip


def _invalid(text: str) -> str:
    if text.startswith("Tunnel ") and text.endswith("zuerst lösen"):
        name = text.removeprefix("Tunnel ").split(" ist schon", 1)[0]
        return L(text, f"tunnel {name} is already coupled manually – separate it first")
    return L(text, _INVALID_EN.get(text, text))


def _audit(session: Session, principal: Principal, action: str, row: VpnPair, **extra) -> None:
    audit.record(session, actor=f"user:{principal.subject}", action=f"vpn_pair.{action}",
                 subject=str(row.id),
                 detail={"tenant_id": row.tenant_id, "mode": row.mode,
                         "a": "|".join(str(x) for x in row.ends[0]),
                         "b": "|".join(str(x) for x in row.ends[1]), **extra})  # fmt: skip


@router.put("/pairs")
def set_pair(
    body: PairIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Zwei Tunnel von Hand koppeln bzw. eine automatische Kopplung loesen (Operator). Die
    Enden stehen mit ihrem festen Schluessel da (Quellsystem, VDOM, Tunnel) - sie gelten auch
    nach dem naechsten Lauf. Mit Kunde nur Tunnel in seinen Kontexten."""
    tid = _tenant(session, principal, body.tenant_id)
    keep = _keep(session, principal, tid, None)
    index = store.Index(session, tid)
    by_id = {t.id: t for t in index.tunnels}
    a, b = by_id.get(body.a), by_id.get(body.b)
    visible = keep is None or (a is not None and b is not None and keep(
        a.source_system_id, a.context) and keep(b.source_system_id, b.context))  # fmt: skip
    if a is None or b is None or not visible:
        raise HTTPException(status_code=404, detail="tunnel not found")
    try:
        row, old = vpn_kopplung.set_pair(session, index, a, b, body.mode,
                                         f"user:{principal.subject}")  # fmt: skip
    except vpn_kopplung.Invalid as exc:
        raise HTTPException(status_code=409, detail=_invalid(str(exc))) from None
    _audit(session, principal, "set", row, previous=old)
    return {"pair_id": row.id, "mode": row.mode, "previous": old,
            "a": a.ref, "b": b.ref}  # fmt: skip


@router.delete("/pairs/{pair_id}")
def delete_pair(
    pair_id: int,
    tenant_id: int | None = None,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Kopplung von Hand bzw. Loesung entfernen (Operator): danach gilt wieder, was die
    automatische Erkennung findet. Auch verwaiste Kopplungen (Tunnel nicht mehr gelesen)."""
    tid = _tenant(session, principal, tenant_id)
    row = session.get(VpnPair, pair_id)
    keep = _keep(session, principal, tid, None)
    visible = row is not None and (keep is None or any(keep(e[0], e[1]) for e in row.ends))
    if row is None or row.tenant_id != tid or not visible:
        raise HTTPException(status_code=404, detail="vpn pair not found")
    _audit(session, principal, "deleted", row)
    session.delete(row)
    session.flush()
    return {"deleted": pair_id}
