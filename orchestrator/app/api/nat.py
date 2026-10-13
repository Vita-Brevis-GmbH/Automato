"""NAT-/VIP-Endpunkt: eingehende DNAT/Virtual IPs (extip -> mappedip:port)."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.deps import require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.schemas.nat import NatOut
from app.store import nat as nat_store

router = APIRouter(prefix="/nat", tags=["nat"])


@router.get("", response_model=list[NatOut])
def list_nat(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session),
):
    tenant_id = tenant_scope(principal, tenant_id)
    return [
        NatOut(
            id=n.id,
            tenant_id=n.tenant_id,
            source_system_id=n.source_system_id,
            name=n.name,
            external_id=n.external_id,
            nat_type=n.nat_type,
            ext_ip=n.ext_ip,
            ext_port=n.ext_port,
            mapped_ip=n.mapped_ip,
            mapped_port=n.mapped_port,
            protocol=n.protocol,
            portforward=n.portforward,
        )
        for n in nat_store.list_nat(session, tenant_id=tenant_id)
    ]
