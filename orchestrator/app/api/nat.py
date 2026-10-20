"""NAT-/VIP-Endpunkte: eingehende DNAT/Virtual IPs (extip -> mappedip:port) und SNAT-Pools.

Liste und Detail sind angereichert wie die Firewall-Regeln: DNS-Namen der externen und der
internen IP aus DDI/NetBox, der Server hinter der internen IP, die Firewall-Regeln mit diesem
VIP bzw. SNAT-Pool. Suche (`q`) ueber Name, IPs, Ports, Kommentar, Interface und DNS-Namen;
eine IP trifft auch innerhalb eines Pool-Bereichs.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.api.rules import rule_outs
from app.audit import reads
from app.auth.deps import require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import NatRule, RuleDomain
from app.schemas.nat import NatDetailOut, NatOut
from app.store import nat as nat_store
from app.store import references, rule_search
from app.store import rules as rules_store

router = APIRouter(prefix="/nat", tags=["nat"])


class _Context:
    """Was Liste und Detail fuer die Anreicherung brauchen - je Mandant einmal geladen."""

    def __init__(self, session: Session, tenant_id: int | None, nats: list[NatRule]):
        self.session = session
        tenants = {n.tenant_id for n in nats}
        self.refs = {t: references.context(session, t) for t in tenants}
        self.servers = {
            t: nat_store.servers_by_ip(
                session, t, {n.mapped_ip for n in nats if n.tenant_id == t and n.mapped_ip}
            )
            for t in tenants
        }
        rules = (
            rules_store.list_rules(
                session, tenant_id=tenant_id, limit=None, domain=RuleDomain.FIREWALL
            )
            if nats
            else []
        )
        self.rules = rules
        self.search = rule_search.RuleSearch(session, rules)
        self.usage = self.search.nat_usage(rules)

    def out(self, n: NatRule) -> NatOut:
        ctx = self.refs[n.tenant_id]
        return NatOut(
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
            comment=nat_store.comment(n),
            interface=nat_store.interface(n),
            external_name=nat_store.external_name(n),
            ext_dns=references.dns_names(ctx, n.ext_ip),
            mapped_dns=references.dns_names(ctx, n.mapped_ip),
            server=self.servers[n.tenant_id].get(n.mapped_ip or ""),
            rule_count=len(self.usage.get((n.source_system_id, n.name.lower()), ())),
        )

    def rule_ids(self, n: NatRule) -> set[int]:
        return self.usage.get((n.source_system_id, n.name.lower()), set())


def _search_fields(o: NatOut) -> tuple[list[str | None], list[str | None]]:
    fields = [
        o.name,
        o.nat_type,
        o.ext_ip,
        o.ext_port,
        o.mapped_ip,
        o.mapped_port,
        o.protocol,
        o.comment,
        o.interface,
        o.external_name,
        o.server,
        *o.ext_dns,
        *o.mapped_dns,
    ]
    return fields, [o.ext_ip, o.mapped_ip]


@router.get("", response_model=list[NatOut])
def list_nat(
    response: Response,
    tenant_id: int | None = None,
    nat_type: str | None = None,
    q: str | None = None,
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session),
):
    """VIPs/SNAT-Pools seitenweise; Gesamtzahl (nach Filter/Suche) im Header `X-Total-Count`."""
    tenant_id = tenant_scope(principal, tenant_id)
    reads.maybe_audit_read(session, principal, tenant_id, "nat")
    q = (q or "").strip()
    if q:
        rows = nat_store.list_nat(session, tenant_id=tenant_id, limit=None, nat_type=nat_type)
        ctx = _Context(session, tenant_id, rows)
        outs = [o for o in map(ctx.out, rows) if nat_store.matches(*_search_fields(o), q)]
        response.headers["X-Total-Count"] = str(len(outs))
        return outs[offset : offset + limit]
    response.headers["X-Total-Count"] = str(nat_store.count_nat(session, tenant_id, nat_type))
    rows = nat_store.list_nat(
        session, tenant_id=tenant_id, limit=limit, offset=offset, nat_type=nat_type
    )
    ctx = _Context(session, tenant_id, rows)
    return [ctx.out(n) for n in rows]


@router.get("/{nat_id}", response_model=NatDetailOut)
def get_nat(
    nat_id: int,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session),
):
    """Ein VIP bzw. SNAT-Pool mit IPAM-Daten beider IPs und den Firewall-Regeln dazu."""
    n = session.get(NatRule, nat_id)
    if n is None:
        raise HTTPException(status_code=404, detail="nat rule not found")
    tenant_scope(principal, n.tenant_id)
    reads.maybe_audit_read(session, principal, n.tenant_id, "nat")
    ctx = _Context(session, n.tenant_id, [n])
    refs = ctx.refs[n.tenant_id]
    ids = ctx.rule_ids(n)
    rules = [r for r in ctx.rules if r.id in ids]
    obj = nat_store.device_object(session, n)
    return NatDetailOut(
        **ctx.out(n).model_dump(),
        ext_info=references.ip_info(refs, n.ext_ip),
        mapped_info=references.ip_info(refs, n.mapped_ip),
        color=obj.color if obj else None,
        settings=nat_store.settings(n),
        rules=rule_outs(session, rules, ctx.search),
    )
