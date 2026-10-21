"""NAT-/VIP-Endpunkte: eingehende DNAT/Virtual IPs (extip -> mappedip:port) und SNAT-Pools.

Liste und Detail sind angereichert wie die Firewall-Regeln: DNS-Namen der externen und der
internen IP aus DDI/NetBox, der Server hinter der internen IP, die Firewall-Regeln mit diesem
VIP bzw. SNAT-Pool. Suche (`q`) ueber Name, IPs, Ports, Kommentar, Interface und DNS-Namen;
eine IP trifft auch innerhalb eines Pool-Bereichs.

Korrektheitspruefung (0.14.0, `issues`, Filter `issues_only`): die Befunde des VIP- bzw.
Pool-Objekts wie in der Objekt-Ansicht (Namenskonvention, unbenutzt, Wert ohne IPAM-Treffer)
und ein internes Ziel (Mapped-IP, Real Server), das im fuehrenden IPAM fehlt.
"""

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import reads
from app.auth.deps import require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import DeviceObject, NatRule, ObjectCategory, Rule, RuleIndex
from app.schemas.nat import NatDetailOut, NatOut, NatTargetOut
from app.store import nat as nat_store
from app.store import objects as objects_store
from app.store import references, rule_index, virtual
from app.store import rules as rules_store

MAPPED_NOT_IN_IPAM = "mapped_not_in_ipam"  # internes Ziel fehlt im fuehrenden IPAM

router = APIRouter(prefix="/nat", tags=["nat"])


class _Context:
    """Was Liste und Detail fuer die Anreicherung brauchen - je Mandant einmal geladen."""

    def __init__(self, session: Session, tenant_id: int | None, nats: list[NatRule]):
        self.session = session
        tenants = {n.tenant_id for n in nats}
        self.refs = {t: references.context(session, t) for t in tenants}
        self.servers = {
            t: nat_store.servers_by_ip(
                session,
                t,
                {ip for n in nats if n.tenant_id == t for ip in nat_store.internal_ips(n)},
            )
            for t in tenants
        }
        # Regeln je VIP/SNAT-Pool aus dem Regelindex (VIPs auch ueber Gruppen)
        self.usage: dict[tuple[int, str], set[int]] = defaultdict(set)
        for t in tenants:
            rule_index.ensure(session, t)
        if tenants:
            for rule_id, sid, names in session.execute(
                select(RuleIndex.rule_id, RuleIndex.source_system_id, RuleIndex.nat).where(
                    RuleIndex.tenant_id.in_(tenants), RuleIndex.nat != ""
                )
            ):
                for name in names.strip("\n").split("\n"):
                    self.usage[(sid, name)].add(rule_id)
        # VIP- bzw. Pool-Objekte der Eintraege und ihre Befunde (wie in der Objekt-Ansicht)
        self.objects: dict[tuple[int, str, str], DeviceObject] = {}
        self.issues: dict[int, list[str]] = {}
        for t in tenants:
            objs = list(
                session.scalars(
                    select(DeviceObject).where(
                        DeviceObject.tenant_id == t,
                        DeviceObject.category.in_((ObjectCategory.VIP, ObjectCategory.IPPOOL)),
                        DeviceObject.name.in_({n.name for n in nats if n.tenant_id == t}),
                    )
                )
            )
            for o in objs:
                self.objects[(o.source_system_id, o.category.value, o.name)] = o
            self.issues.update(objects_store.object_issues(session, t, objs))

    def device_object(self, n: NatRule) -> DeviceObject | None:
        category = ObjectCategory.IPPOOL if n.nat_type == "snat_pool" else ObjectCategory.VIP
        return self.objects.get((n.source_system_id, category.value, n.name))

    def check(self, n: NatRule) -> list[str]:
        """Befunde des Eintrags: die des VIP-/Pool-Objekts und ein internes Ziel ohne IPAM."""
        obj = self.device_object(n)
        found = list(self.issues.get(obj.id, [])) if obj is not None else []
        primary = self.refs[n.tenant_id].primary
        if n.nat_type == "dnat_vip" and primary is not None:
            if any(not primary.ip_records(ip) for ip in nat_store.internal_ips(n)):
                found.append(MAPPED_NOT_IN_IPAM)
        return sorted(set(found))

    def out(self, n: NatRule) -> NatOut:
        ctx = self.refs[n.tenant_id]
        candidates = self.servers[n.tenant_id].get(n.mapped_ip or "", [])
        servers = nat_store.server_names(candidates, n.source_system_id)
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
            server=next(iter(servers), None),
            servers=servers,
            rule_count=len(self.usage.get((n.source_system_id, n.name.lower()), ())),
            vip_type=n.vip_type,
            family=n.family,
            targets=[self._target(n, t, ctx) for t in n.targets or []],
            attrs=n.attrs or {},
            issues=self.check(n),
        )

    def _target(self, n: NatRule, t: dict, ctx) -> NatTargetOut:
        """Real Server eines Virtual Servers mit Firewall-Objekt und DNS-Namen der IP."""
        ip = t.get("ip")
        candidates = self.servers[n.tenant_id].get(ip or "", [])
        return NatTargetOut(
            **{k: v for k, v in t.items() if k in NatTargetOut.model_fields},
            servers=nat_store.server_names(candidates, n.source_system_id),
            dns=references.dns_names(ctx, ip),
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
        *o.servers,
        *o.ext_dns,
        *o.mapped_dns,
        o.vip_type,
        "virtual server" if o.vip_type == nat_store.SLB else None,
        *(str(v) for k, v in o.attrs.items() if k != "monitor"),
        *(m.get("name") for m in o.attrs.get("monitor", []) if isinstance(m, dict)),
    ]
    for t in o.targets:
        fields += [t.ip, t.address, *t.servers, *t.dns]
    return fields, [o.ext_ip, o.mapped_ip, *(t.ip for t in o.targets)]


@router.get("", response_model=list[NatOut])
def list_nat(
    response: Response,
    tenant_id: int | None = None,
    nat_type: str | None = None,
    q: str | None = None,
    issues_only: bool = Query(False, description="nur Eintraege mit Befund"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    """VIPs/SNAT-Pools seitenweise; Gesamtzahl (nach Filter/Suche) im Header `X-Total-Count`."""
    tenant_id = tenant_scope(principal, tenant_id)
    reads.maybe_audit_read(session, principal, tenant_id, "nat")
    q = (q or "").strip()
    if q or issues_only:
        rows = nat_store.list_nat(session, tenant_id=tenant_id, limit=None, nat_type=nat_type)
        ctx = _Context(session, tenant_id, rows)
        outs = [
            o
            for o in map(ctx.out, rows)
            if (not q or nat_store.matches(*_search_fields(o), q)) and (not issues_only or o.issues)
        ]
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
    session: Session = Depends(get_session, scope="function"),
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
    order = select(Rule.id).where(Rule.id.in_(ids)).order_by(*rules_store.ORDER)
    rules = rule_index.load_rules(session, list(session.scalars(order)) if ids else [])
    obj = ctx.device_object(n)
    internal = nat_store.internal_ips(n) if n.nat_type == "dnat_vip" else []
    vms = virtual.Lookup(session, n.tenant_id, set(internal))
    return NatDetailOut(
        **ctx.out(n).model_dump(),
        ext_info=references.ip_info(refs, n.ext_ip),
        mapped_info=references.ip_info(refs, n.mapped_ip),
        color=obj.color if obj else None,
        object_id=obj.id if obj else None,
        settings=nat_store.settings(n),
        rules=rule_index.outs(session, rules),
        vms=[{**info, "for_ip": ip} for ip in internal if (info := vms.info(ip))],
    )
