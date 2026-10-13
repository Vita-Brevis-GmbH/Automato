"""Applikations-Endpunkte (UC1): publizierte Dienste (VIPs) + zugeordnete Regeln.

Eine Applikation = ein VIP (externer Dienst) mit seinem internen Ziel (NAT) und den
Firewall-Regeln, die ihn exponieren. Der Markdown-Export wird clientseitig aus dem
Detail erzeugt.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import EndpointRole, NatRule
from app.schemas.applications import ApplicationDetailOut, ApplicationOut, AppRule
from app.store import applications as app_store
from app.store import rules as rules_store

router = APIRouter(prefix="/applications", tags=["applications"])


def _base(nat: NatRule, rule_count: int) -> dict:
    return {
        "name": nat.name,
        "ext_ip": nat.ext_ip,
        "ext_port": nat.ext_port,
        "mapped_ip": nat.mapped_ip,
        "mapped_port": nat.mapped_port,
        "protocol": nat.protocol,
        "portforward": nat.portforward,
        "rule_count": rule_count,
    }


@router.get("", response_model=list[ApplicationOut])
def list_applications(tenant_id: int | None = None, session: Session = Depends(get_session)):
    vmap = app_store.vip_rule_ids(session, tenant_id)
    return [
        ApplicationOut(**_base(nat, len(vmap.get(nat.name, set()))))
        for nat in app_store.list_vips(session, tenant_id)
    ]


@router.get("/{name}", response_model=ApplicationDetailOut)
def get_application(
    name: str, tenant_id: int | None = None, session: Session = Depends(get_session)
):
    nat = app_store.get_vip(session, name, tenant_id)
    if nat is None:
        raise HTTPException(status_code=404, detail="application not found")

    rule_ids = sorted(app_store.vip_rule_ids(session, tenant_id).get(name, set()))
    rules: list[AppRule] = []
    for rid in rule_ids:
        r = rules_store.get_rule(session, rid)
        if r is None:
            continue
        obj_ids = {e.network_object_id for e in r.endpoints if e.network_object_id}
        objs = rules_store.objects_by_ids(session, obj_ids)
        sources: list[str] = []
        for e in r.endpoints:
            if e.role is not EndpointRole.SRC:
                continue
            obj = objs.get(e.network_object_id) if e.network_object_id else None
            sources.append((obj.value or obj.name) if obj else (e.inline_value or "any"))
        rules.append(
            AppRule(
                id=r.id,
                name=r.name or f"#{r.external_id}",
                action=r.action.value,
                src_zone=None,
                sources=sources,
                services=[s.canonical_name for s in r.services],
            )
        )

    return ApplicationDetailOut(**_base(nat, len(rule_ids)), rules=rules)
