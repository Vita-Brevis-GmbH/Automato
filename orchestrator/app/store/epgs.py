"""Wo liegt ein Consumer/Provider eines ACI-Contracts? (EPG, externe EPG, vzAny)

Beschriftung fuer Listen (`AP/EPG`, `L3Out/externe EPG`, `vzAny VRF`) und die Lage fuer das
Regel-Detail: ACI-Tenant, Application Profile, Bridge Domain, VRF, L3Out, Subnetze (mit dem
passenden IPAM-Netz der fuehrenden Referenzquelle) und die gelernten Endpunkte mit DNS-Namen
aus DDI/NetBox und dem verknuepften Server. Grundlage, um Contracts Applikationen zuzuordnen.
"""

import ipaddress

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Epg, EpgEndpoint, EpgSubnet, NetworkObject, Site
from app.store import references

MAX_ENDPOINTS = 50  # je EPG im Detail; die Gesamtzahl steht daneben


def by_ids(session: Session, ids: set[int]) -> dict[int, Epg]:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return {e.id: e for e in session.scalars(select(Epg).where(Epg.id.in_(ids)))}


def label(epg: Epg, home_tenant: str | None = None) -> str:
    """Kurzname wie in der APIC-Hierarchie; ACI-Tenant davor, wenn er vom Contract abweicht."""
    if epg.kind == "external":
        short = f"{epg.l3out}/{epg.name}" if epg.l3out else epg.name
    elif epg.kind == "vzany":
        short = f"vzAny {epg.vrf}" if epg.vrf else "vzAny"
    else:
        short = f"{epg.ap}/{epg.name}" if epg.ap else epg.name
    if epg.aci_tenant and home_tenant and epg.aci_tenant != home_tenant:
        return f"{epg.aci_tenant}/{short}"
    return short


def _network(ip: str) -> str | None:
    try:
        return str(ipaddress.ip_interface(ip).network)
    except ValueError:
        return None


def _ip_name(ctx: references.Context, ip: str) -> str | None:
    for idx in [ctx.primary, *ctx.enrichments]:
        if idx is None:
            continue
        for rec in idx.ip_records(ip):
            if rec.dns_name or rec.name:
                return rec.dns_name or rec.name
    return None


def _ipam_net(ctx: references.Context, prefix: str | None) -> dict | None:
    idx = ctx.primary
    if idx is None or prefix is None:
        return None
    rec = idx.prefix(prefix) or next(iter(idx.containing_prefixes(prefix)), None)
    if rec is None:
        return None
    return {
        "prefix": rec.value,
        "description": rec.description or rec.name,
        "vlan": rec.vlan_vid,
        "site": rec.site,
        "source": idx.platform,
    }


def details(session: Session, tenant_id: int, epgs: list[Epg]) -> dict[int, dict]:
    """Lage je EPG-ID (fuer `EndpointOut.epg`)."""
    ids = [e.id for e in epgs]
    if not ids:
        return {}
    subnets: dict[int, list[EpgSubnet]] = {i: [] for i in ids}
    for s in session.scalars(select(EpgSubnet).where(EpgSubnet.epg_id.in_(ids))):
        subnets[s.epg_id].append(s)
    endpoints: dict[int, list[EpgEndpoint]] = {i: [] for i in ids}
    for ep in session.scalars(
        select(EpgEndpoint).where(EpgEndpoint.epg_id.in_(ids)).order_by(EpgEndpoint.ip)
    ):
        endpoints[ep.epg_id].append(ep)
    ctx = references.context(session, tenant_id)
    out: dict[int, dict] = {}
    for epg in epgs:
        host = session.get(NetworkObject, epg.network_object_id) if epg.network_object_id else None
        site = session.get(Site, host.site_id) if host and host.site_id else None
        eps = endpoints[epg.id]
        out[epg.id] = {
            "kind": epg.kind,
            "name": epg.name,
            "label": label(epg),
            "dn": epg.dn,
            "aci_tenant": epg.aci_tenant,
            "ap": epg.ap,
            "bd": epg.bd,
            "vrf": epg.vrf,
            "l3out": epg.l3out,
            "subnets": [
                {"ip": s.ip, "origin": s.origin, "ipam": _ipam_net(ctx, _network(s.ip))}
                for s in sorted(subnets[epg.id], key=lambda x: (x.origin, x.ip))
            ],
            "endpoint_count": len(eps),
            "endpoints": [
                {"ip": ep.ip, "mac": ep.mac, "dns_name": _ip_name(ctx, ep.ip)}
                for ep in eps[:MAX_ENDPOINTS]
            ],
            "server": (
                {"name": host.name, "ip": host.value, "site": site.code if site else None}
                if host
                else None
            ),
        }
    return out
