"""Prüfung: VIP der Firewall ohne Ziel (0.134.0).

Anwender 2026-11-05: „es scheint noch Externe VIPs zu geben, welche keine Loadbalancer haben
und die IP nicht mehr zugewiesen ist. Diese müssen man als Finding markieren“.

Eine VIP (DNAT) einer Firewall, deren interne Adressen nirgends mehr vorkommen: kein Virtual
Service eines Load Balancers dahinter (`nat.Balancers`), im führenden IPAM nicht vergeben,
keine VM (VMware Tools) und kein Endpunkt im ACI mit der Adresse. Ohne führendes IPAM läuft die
Prüfung nicht - sonst wäre jede VIP ein Befund. Bereiche (`mappedip` von-bis) zählen nicht.
Steckbare Prüfung (ADR 0039), nur lesend."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.checks import Check, Context
from app.models import Epg, EpgEndpoint, FindingType, NatRule, Severity, SubjectKind, VmIp
from app.store.reconcile import Issue, _source_names


def _vip_issues(session: Session, tenant_id: int, cx: Context) -> list[Issue]:
    from app.store import nat as nat_store  # spaet: nat -> references -> reconcile

    primary = getattr(cx.refs, "primary", None)
    if primary is None:
        return []
    lbs = nat_store.Balancers.load(session, tenant_id)
    vips: list[tuple[NatRule, list[str]]] = []
    for n in lbs.rows:
        if nat_store.is_balancer(n) or lbs.links(n):
            continue
        ips = [ip for v in nat_store.internal_ips(n) if (ip := nat_store.host_ip(v))]
        if ips and not any(primary.ip_records(ip) for ip in ips):
            vips.append((n, ips))
    if not vips:
        return []
    wanted = {ip for _n, ips in vips for ip in ips}
    used = set(session.scalars(select(VmIp.ip).where(VmIp.tenant_id == tenant_id,
                                                     VmIp.ip.in_(wanted))))  # fmt: skip
    used |= set(session.scalars(
        select(EpgEndpoint.ip).join(Epg, Epg.id == EpgEndpoint.epg_id)
        .where(Epg.tenant_id == tenant_id, EpgEndpoint.ip.in_(wanted))
    ))  # fmt: skip
    names = _source_names(session, tenant_id)
    out: list[Issue] = []
    for n, ips in vips:
        if any(ip in used for ip in ips):
            continue
        out.append(
            Issue(
                FindingType.VIP_TARGET_MISSING,
                SubjectKind.NAT,
                f"{n.source_system_id}|{n.vdom or ''}|{n.nat_type}|{n.family or ''}|{n.name}",
                n.source_system_id,
                Severity.WARNING,
                detail={
                    "nat_id": n.id,
                    "name": n.name,
                    "ext_ip": n.ext_ip,
                    "mapped": ips,
                    "source": names.get(n.source_system_id),
                    **({"vdom": n.vdom} if n.vdom else {}),
                },
            )
        )
    return out


CHECK = Check(
    "vip_ziel",
    "kern",
    ("VIP ohne Ziel", "VIP without target"),
    (
        "VIP der Firewall ohne Load Balancer dahinter, deren interne Adresse im IPAM nicht "
        "vergeben ist und zu keiner VM bzw. keinem ACI-Endpunkt gehört",
        "Firewall VIP without a load balancer behind it whose internal address is not assigned "
        "in the IPAM and belongs to no VM or ACI endpoint",
    ),
    frozenset({FindingType.VIP_TARGET_MISSING}),
    _vip_issues,
    order=31,
)
