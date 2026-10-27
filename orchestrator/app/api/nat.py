"""NAT-/VIP-Endpunkte: eingehende DNAT/Virtual IPs (extip -> mappedip:port) und SNAT-Pools.

Liste und Detail sind angereichert wie die Firewall-Regeln: DNS-Namen der externen und der
internen IP aus DDI/NetBox, der Server hinter der internen IP, die Firewall-Regeln mit diesem
VIP bzw. SNAT-Pool. Suche (`q`) ueber Name, IPs, Ports, Kommentar, Interface und DNS-Namen;
eine IP trifft auch innerhalb eines Pool-Bereichs.

Korrektheitspruefung (0.14.0, `issues`, Filter `issues_only`): die Befunde des VIP- bzw.
Pool-Objekts wie in der Objekt-Ansicht (Namenskonvention, unbenutzt, Wert ohne IPAM-Treffer)
und ein internes Ziel (Mapped-IP, Real Server), das im fuehrenden IPAM fehlt.

Kette ueber den Load Balancer (0.96.0, ADR 0027): je VIP der Firewall `chain` = externe
IP:Port → VIP des Load Balancers → Virtual Service → Pool → Server, am Virtual Service
umgekehrt `fronted_by` (die VIPs davor); `dest_servers` = Ziel-Server jeder VIP (direkt bzw.
ueber den Load Balancer). Die Suche findet die Kette ueber externe IP, Server, Virtual Service.
"""

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.customer_filter import customer_view, own_view, visible
from app.api.customer_filter import ids as view_ids
from app.audit import reads
from app.auth.deps import require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import DeviceObject, NatRule, ObjectCategory, Rule, RuleIndex, SourceSystem
from app.schemas.nat import (
    NatChainOut,
    NatDetailOut,
    NatFrontOut,
    NatOut,
    NatServerOut,
    NatTargetOut,
)
from app.store import nat as nat_store
from app.store import objects as objects_store
from app.store import references, rule_index, virtual
from app.store import rules as rules_store

MAPPED_NOT_IN_IPAM = "mapped_not_in_ipam"  # internes Ziel fehlt im fuehrenden IPAM

router = APIRouter(prefix="/nat", tags=["nat"])


class _Context:
    """Was Liste und Detail fuer die Anreicherung brauchen - je Mandant einmal geladen."""

    def __init__(
        self,
        session: Session,
        tenant_id: int | None,
        nats: list[NatRule],
        only=None,
        nat_only: set[int] | None = None,
    ):
        self.session = session
        self.only: set[int] | None = only  # Kunden-Benutzer: nur ihre Regeln (ADR 0018)
        self.nat_only = nat_only  # Ausschnitt eines Kunden: nur seine NAT-Eintraege davor
        tenants = {n.tenant_id for n in nats}
        self.refs = {t: references.context(session, t) for t in tenants}
        # 0.96.0: Kette VIP der Firewall -> Virtual Service des NSX ALB -> Server, je Mandant
        self.lbs = {t: nat_store.Balancers.load(session, t) for t in tenants}
        stmt = select(SourceSystem.id, SourceSystem.name).where(SourceSystem.tenant_id.in_(tenants))
        self.sources: dict[int, str] = dict(session.execute(stmt).tuples().all()) if tenants else {}
        self.servers = {
            t: nat_store.servers_by_ip(
                session, t, {ip for n in nats if n.tenant_id == t for ip in self._ips(n)}
            )
            for t in tenants
        }
        # Regeln je VIP/SNAT-Pool aus dem Regelindex (VIPs auch ueber Gruppen), je VDOM; die
        # Virtual Services des Load Balancers nach Namen (Regeln der VIPs davor bzw. direkt)
        self.usage: dict[tuple[int, str | None, str], set[int]] = defaultdict(set)
        self.by_name: dict[tuple[int, str], set[int]] = defaultdict(set)
        for t in tenants:
            rule_index.ensure(session, t)
        if tenants:
            for rule_id, tid, sid, vdom, names in session.execute(
                select(
                    RuleIndex.rule_id,
                    RuleIndex.tenant_id,
                    RuleIndex.source_system_id,
                    Rule.vdom,
                    RuleIndex.nat,
                )
                .join(Rule, Rule.id == RuleIndex.rule_id)
                .where(RuleIndex.tenant_id.in_(tenants), RuleIndex.nat != "")
            ):
                for name in names.strip("\n").split("\n"):
                    self.usage[(sid, vdom, name)].add(rule_id)
                    self.by_name[(tid, name)].add(rule_id)
        # VIP-Gruppen der Firewall (FortiGate vipgrp, 0.96.0): Gruppe je VIP
        self.groups: dict[tuple[int, str | None, str], list[str]] = defaultdict(list)
        if tenants:
            for g in session.scalars(
                select(DeviceObject).where(
                    DeviceObject.tenant_id.in_(tenants),
                    DeviceObject.category == ObjectCategory.ADDRESS_GROUP,
                    DeviceObject.obj_type == nat_store.VIP_GROUP,
                )
            ):
                for m in g.members or []:
                    self.groups[(g.source_system_id, g.vdom, str(m).lower())].append(g.name)
        # VIP- bzw. Pool-Objekte der Eintraege und ihre Befunde (wie in der Objekt-Ansicht)
        self.objects: dict[tuple[int, str | None, str, str], list[DeviceObject]] = defaultdict(list)
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
                self.objects[(o.source_system_id, o.vdom, o.category.value, o.name)].append(o)
            self.issues.update(objects_store.object_issues(session, t, objs))

    def device_object(self, n: NatRule) -> DeviceObject | None:
        """VIP- bzw. Pool-Objekt des Eintrags - IPv4 und IPv6 duerfen gleich heissen (0.96.0:
        das Objekt der Familie des Eintrags)."""
        category = ObjectCategory.IPPOOL if n.nat_type == "snat_pool" else ObjectCategory.VIP
        found = self.objects.get((n.source_system_id, n.vdom, category.value, n.name), [])
        same = [o for o in found if o.family == n.family]
        return (same or [o for o in found if o.family is None] or found or [None])[0]

    def check(self, n: NatRule) -> list[str]:
        """Befunde des Eintrags: die des VIP-/Pool-Objekts und ein internes Ziel ohne IPAM."""
        obj = self.device_object(n)
        found = list(self.issues.get(obj.id, [])) if obj is not None else []
        primary = self.refs[n.tenant_id].primary
        if n.nat_type == "dnat_vip" and primary is not None:
            if any(not primary.ip_records(ip) for ip in nat_store.internal_ips(n)):
                found.append(MAPPED_NOT_IN_IPAM)
        return sorted(set(found))

    def _ips(self, n: NatRule) -> set[str]:
        """Interne IPs eines Eintrags inkl. der Server hinter dem Load Balancer."""
        ips = set(nat_store.internal_ips(n))
        for link in self.lbs[n.tenant_id].links(n):
            ips |= set(nat_store.internal_ips(link.lb))
        return ips

    def _server(self, n: NatRule, ip: str | None, port, status=None, via=None) -> NatServerOut:
        # Firewall-Objekte mit der IP: bevorzugt die der Firewall des Eintrags (auch hinter dem
        # Load Balancer - die VIP davor), sonst die anderer Firewalls
        candidates = self.servers[n.tenant_id].get(ip or "", []) if ip else []
        return NatServerOut(
            ip=ip,
            port=str(port) if port not in (None, "") else None,
            status=status,
            via=via,
            names=nat_store.server_names(candidates, n.source_system_id, n.vdom),
            dns=references.dns_names(self.refs[n.tenant_id], ip) if ip else [],
        )

    def _targets(self, n: NatRule, lb: NatRule | None = None) -> list[NatServerOut]:
        """Server eines Virtual Server bzw. Virtual Service (Real Server, Pool)."""
        src = lb or n
        out = []
        for t in src.targets or []:
            if isinstance(t, dict) and (t.get("ip") or t.get("address")):
                ip = t.get("ip") or t.get("address")
                out.append(self._server(n, ip, t.get("port"), t.get("status"), lb and lb.name))
        return out

    def chain(self, n: NatRule) -> list[NatChainOut]:
        out = []
        for link in self.lbs[n.tenant_id].links(n):
            lb, attrs = link.lb, link.lb.attrs or {}
            out.append(
                NatChainOut(
                    ext_ip=link.ext_ip,
                    ext_port=link.ext_port,
                    vip_ip=lb.ext_ip,
                    vip_port=lb.ext_port,
                    via=link.via,
                    balancer_id=lb.id,
                    balancer=lb.name,
                    source=self.sources.get(lb.source_system_id),
                    source_system_id=lb.source_system_id,
                    pool=attrs.get("pool") or attrs.get("pool_group"),
                    tenant=attrs.get("tenant"),
                    vrf=attrs.get("vrf"),
                    floating=bool(attrs.get("floating_ip")),
                    down=attrs.get("oper_status") == "OPER_DOWN" or attrs.get("enabled") is False,
                    servers=self._targets(n, lb),
                )
            )
        return out

    def fronted_by(self, n: NatRule) -> list[NatFrontOut]:
        if not nat_store.is_balancer(n):
            return []
        return [
            NatFrontOut(
                id=link.vip.id,
                name=link.vip.name,
                source=self.sources.get(link.vip.source_system_id),
                source_system_id=link.vip.source_system_id,
                vdom=link.vip.vdom,
                ext_ip=link.ext_ip,
                ext_port=link.ext_port,
                via=link.via,
            )
            for link in self.lbs[n.tenant_id].fronts(n)
            if self.nat_only is None or link.vip.id in self.nat_only
        ]

    def dest_servers(self, n: NatRule, chain: list[NatChainOut]) -> list[NatServerOut]:
        """Ziel-Server (0.96.0): ueber den Load Balancer dessen Server, sonst die Real Server
        bzw. die zugeordnete IP mit Port; SNAT-Pools haben keine."""
        if n.nat_type != "dnat_vip":
            return []
        if chain:
            seen, out = set(), []
            for c in chain:
                for s in c.servers:
                    if (s.ip, s.port, s.via) not in seen:
                        seen.add((s.ip, s.port, s.via))
                        out.append(s)
            return out
        if n.targets:
            return self._targets(n)
        values = nat_store.mapped_values(n)
        port = n.mapped_port if n.portforward else None
        return [self._server(n, v, port) for v in values]

    def rule_ids(self, n: NatRule) -> set[int]:
        if nat_store.is_balancer(n):  # Regeln der VIPs davor bzw. direkt zur VIP (0.96.0)
            found = self.by_name.get((n.tenant_id, n.name.lower()), set())
        else:
            found = self.usage.get((n.source_system_id, n.vdom, n.name.lower()), set())
        return found if self.only is None else found & self.only

    def out(self, n: NatRule) -> NatOut:
        ctx = self.refs[n.tenant_id]
        candidates = self.servers[n.tenant_id].get(n.mapped_ip or "", [])
        servers = nat_store.server_names(candidates, n.source_system_id, n.vdom)
        chain = self.chain(n)
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
            rule_count=len(self.rule_ids(n)),
            vip_type=n.vip_type,
            family=n.family,
            vdom=n.vdom,
            targets=[self._target(n, t, ctx) for t in n.targets or []],
            attrs=n.attrs or {},
            issues=self.check(n),
            source=self.sources.get(n.source_system_id),
            chain=chain,
            fronted_by=self.fronted_by(n),
            dest_servers=self.dest_servers(n, chain),
            groups=list(
                dict.fromkeys(self.groups.get((n.source_system_id, n.vdom, n.name.lower()), []))
            ),
        )

    def _target(self, n: NatRule, t: dict, ctx) -> NatTargetOut:
        """Real Server eines Virtual Servers mit Firewall-Objekt und DNS-Namen der IP."""
        ip = t.get("ip")
        candidates = self.servers[n.tenant_id].get(ip or "", [])
        return NatTargetOut(
            **{k: v for k, v in t.items() if k in NatTargetOut.model_fields},
            servers=nat_store.server_names(candidates, n.source_system_id, n.vdom),
            dns=references.dns_names(ctx, ip),
        )


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
    # 0.96.0: die Kette - Virtual Service, Pool, VIP des Load Balancers, Ziel-Server bzw. die
    # VIPs davor (externe IP) und die VIP-Gruppen
    ranges = [o.ext_ip, o.mapped_ip, *(t.ip for t in o.targets)]
    for c in o.chain:
        fields += [c.balancer, c.pool, c.vip_ip, c.vip_port, c.ext_ip, c.ext_port, c.source]
        fields += [c.tenant, c.vrf]
        ranges += [c.vip_ip, c.ext_ip]
    for f in o.fronted_by:
        fields += [f.name, f.ext_ip, f.ext_port, f.source]
        ranges.append(f.ext_ip)
    for s in o.dest_servers:
        fields += [s.ip, f"{s.ip}:{s.port}" if s.ip and s.port else None, *s.names, *s.dns]
        ranges.append(s.ip)
    fields += o.groups
    return fields, ranges


@router.get("", response_model=list[NatOut])
def list_nat(
    response: Response,
    tenant_id: int | None = None,
    nat_type: str | None = None,
    q: str | None = None,
    issues_only: bool = Query(False, description="nur Eintraege mit Befund"),
    customer_id: int | None = Query(None, description="nur NAT dieses Kunden (ADR 0018)"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    """VIPs/SNAT-Pools seitenweise; Gesamtzahl (nach Filter/Suche) im Header `X-Total-Count`."""
    tenant_id = tenant_scope(principal, tenant_id)
    reads.maybe_audit_read(session, principal, tenant_id, "nat")
    view = customer_view(session, principal, tenant_id, customer_id)
    only = view.ids("nat") if view else None
    own_rules = view_ids(session, view, "rule") if principal.is_customer else None
    q = (q or "").strip()
    if q or issues_only:
        rows = nat_store.list_nat(
            session, tenant_id=tenant_id, limit=None, nat_type=nat_type, only=only
        )
        ctx = _Context(session, tenant_id, rows, own_rules, view_ids(session, view, "nat"))
        outs = [
            o
            for o in map(ctx.out, rows)
            if (not q or nat_store.matches(*_search_fields(o), q)) and (not issues_only or o.issues)
        ]
        response.headers["X-Total-Count"] = str(len(outs))
        return outs[offset : offset + limit]
    response.headers["X-Total-Count"] = str(nat_store.count_nat(session, tenant_id, nat_type, only))
    rows = nat_store.list_nat(
        session, tenant_id=tenant_id, limit=limit, offset=offset, nat_type=nat_type, only=only
    )
    ctx = _Context(session, tenant_id, rows, own_rules, view_ids(session, view, "nat"))
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
    view = own_view(session, principal, n.tenant_id)
    visible(session, view, "nat", n.id)  # ADR 0018
    reads.maybe_audit_read(session, principal, n.tenant_id, "nat")
    nat_only = view_ids(session, view, "nat")
    ctx = _Context(session, n.tenant_id, [n], view_ids(session, view, "rule"), nat_only)
    refs = ctx.refs[n.tenant_id]
    ids = ctx.rule_ids(n)
    order = select(Rule.id).where(Rule.id.in_(ids)).order_by(*rules_store.ORDER)
    rules = rule_index.load_rules(session, list(session.scalars(order)) if ids else [])
    obj = ctx.device_object(n)
    internal = nat_store.internal_ips(n) if n.nat_type == "dnat_vip" else []
    for link in ctx.lbs[n.tenant_id].links(n):  # 0.96.0: auch die Server hinter dem LB
        internal += [ip for ip in nat_store.internal_ips(link.lb) if ip not in internal]
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
