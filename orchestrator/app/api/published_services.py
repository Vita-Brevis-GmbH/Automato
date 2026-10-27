"""Publizierte Dienste (UC1): VIPs mit internem Ziel (NAT) und den Regeln, die sie exponieren.

Ein publizierter Dienst = ein VIP (externer Dienst). Die fachliche Applikation (mehrere
APs/EPGs/Server) liegt unter /applications (ADR 0012).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.customer_filter import customer_view
from app.api.customer_filter import ids as view_ids
from app.audit import reads
from app.auth.deps import require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import EndpointRole, NatRule
from app.schemas.published_services import AppRule, PublishedServiceDetailOut, PublishedServiceOut
from app.store import nat as nat_store
from app.store import published_services as app_store
from app.store import rules as rules_store

router = APIRouter(prefix="/published-services", tags=["published-services"])


def _chain(
    session: Session, tenant_id: int | None, nats: list[NatRule]
) -> tuple[nat_store.Balancers, dict]:
    """0.79.0: Virtual Services hinter den VIPs der FortiGate und umgekehrt die VIPs davor -
    seit 0.96.0 je Glied mit der externen Adresse und dem Port dorthin (`nat.Link`), auch
    FQDN-VIPs."""
    fqdns = nat_store.address_fqdns(session, tenant_id, nats) if tenant_id is not None else {}
    lbs = nat_store.Balancers(nats, fqdns)
    front: dict[int, list[nat_store.Link]] = {}
    for n in nats:
        for link in lbs.links(n):
            front.setdefault(link.lb.id, []).append(link)
    return lbs, front


def _links(nat: NatRule, lbs, front: dict, sources: dict) -> dict:
    def name(n: NatRule) -> str | None:
        return (sources.get(n.source_system_id) or ("", ""))[0] or None

    behind = lbs.behind(nat)
    return {
        "balancers": [nat_store.balancer_brief(lb, name(lb)) for lb in behind],
        "fronted_by": [
            {"name": f.vip.name, "ext_ip": f.ext_ip, "ext_port": f.ext_port, "source": name(f.vip)}
            for f in front.get(nat.id, [])
        ],
    }


def _base(nat: NatRule, rule_count: int, source: tuple[str, str] | None = None) -> dict:
    attrs = nat.attrs or {}
    return {
        "source": source[0] if source else None,
        "platform": source[1] if source else None,
        "servers": len([t for t in nat.targets or [] if t.get("ip") or t.get("address")]),
        "pool": attrs.get("pool") or attrs.get("pool_group"),
        "name": nat.name,
        "ext_ip": nat.ext_ip,
        "ext_port": nat.ext_port,
        "mapped_ip": nat.mapped_ip,
        "mapped_port": nat.mapped_port,
        "protocol": nat.protocol,
        "portforward": nat.portforward,
        "rule_count": rule_count,
    }


@router.get("", response_model=list[PublishedServiceOut])
def list_published_services(
    tenant_id: int | None = None,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    tenant_id = tenant_scope(principal, tenant_id)
    reads.maybe_audit_read(session, principal, tenant_id, "published_services")
    view = customer_view(session, principal, tenant_id, customer_id)  # 0.76.0: VIPs des Kunden
    mine = view_ids(session, view, "nat")
    vmap = app_store.vip_rule_ids(session, tenant_id)
    sources = app_store.platforms(session, tenant_id)
    nats = [n for n in app_store.list_vips(session, tenant_id) if mine is None or n.id in mine]
    lb_ips = {
        n.ext_ip
        for n in nats
        if n.ext_ip and app_store.is_balancer(sources.get(n.source_system_id, ("", ""))[1])
    }
    imap = app_store.ip_rule_ids(session, lb_ips, tenant_id)
    lbs, front = _chain(session, tenant_id, app_store.list_vips(session, tenant_id))
    out = []
    for nat in nats:
        source = sources.get(nat.source_system_id)
        rules = vmap.get(nat.name, set())
        if source and app_store.is_balancer(source[1]):
            rules = imap.get(nat.ext_ip or "", set())
            for f in front.get(nat.id, []):  # Regeln der VIPs davor (0.79.0)
                rules = rules | vmap.get(f.vip.name, set())
        base = _base(nat, len(rules), source) | _links(nat, lbs, front, sources)
        out.append(PublishedServiceOut(**base))
    return out


@router.get("/{name}", response_model=PublishedServiceDetailOut)
def get_published_service(
    name: str,
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    tenant_id = tenant_scope(principal, tenant_id)
    reads.maybe_audit_read(session, principal, tenant_id, "published_services")
    nat = app_store.get_vip(session, name, tenant_id)
    if nat is None:
        raise HTTPException(status_code=404, detail="published service not found")

    systems = app_store.platforms(session, tenant_id)
    source = systems.get(nat.source_system_id)
    vmap = app_store.vip_rule_ids(session, tenant_id)
    found = vmap.get(name, set())
    lbs, front = _chain(session, tenant_id, app_store.list_vips(session, tenant_id))
    if source and app_store.is_balancer(source[1]) and nat.ext_ip:
        found = app_store.ip_rule_ids(session, {nat.ext_ip}, tenant_id).get(nat.ext_ip, set())
        for f in front.get(nat.id, []):  # Regeln der VIPs der FortiGate davor (0.79.0)
            found = found | vmap.get(f.vip.name, set())
    rule_ids = sorted(found)
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

    base = _base(nat, len(rule_ids), source) | _links(nat, lbs, front, systems)
    return PublishedServiceDetailOut(**base, rules=rules)
