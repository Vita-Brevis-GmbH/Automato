"""Wo liegt ein Consumer/Provider eines ACI-Contracts? (EPG, externe EPG, vzAny)

Beschriftung fuer Listen (`AP/EPG`, `L3Out/externe EPG`, `vzAny VRF`) und die Lage fuer das
Regel-Detail: ACI-Tenant, Application Profile, Bridge Domain, VRF, L3Out, Subnetze (mit dem
passenden IPAM-Netz der fuehrenden Referenzquelle) und die gelernten Endpunkte mit DNS-Namen
aus DDI/NetBox und dem verknuepften Server. Grundlage, um Contracts Applikationen zuzuordnen.
Physisch: Leaf-Ports (statische Pfade) und VLANs der EPG aus dem Netzinventar des APICs.
"""

import ipaddress

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Epg, EpgEndpoint, EpgSubnet, InvInterface, InvVlan, NetworkObject
from app.store import references, sites

MAX_ENDPOINTS = 50  # je EPG im Detail; die Gesamtzahl steht daneben
MAX_PORTS = 50


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


def inventory_label(epg: Epg) -> str:
    """Bezeichnung der EPG im Netzinventar (`Tenant/AP/EPG`, wie der Collector sie setzt)."""
    return "/".join(x for x in (epg.aci_tenant, epg.ap, epg.name) if x)


def _physical(session: Session, tenant_id: int, epgs: list[Epg]) -> dict[str, dict]:
    """Leaf-Ports und VLANs je Inventar-Bezeichnung, nur aus den APICs der EPGs."""
    sources = {e.source_system_id for e in epgs if e.kind == "epg" and e.source_system_id}
    if not sources:
        return {}
    wanted = {inventory_label(e) for e in epgs if e.kind == "epg"}
    out: dict[str, dict] = {lbl: {"ports": [], "vlans": set()} for lbl in wanted}
    scope = (InvInterface.tenant_id == tenant_id, InvInterface.source_system_id.in_(sources))
    for i in session.scalars(
        select(InvInterface).where(*scope).order_by(InvInterface.device, InvInterface.name)
    ):
        for lbl in set(i.epgs or []) & wanted:
            out[lbl]["ports"].append(
                {
                    "device": i.device,
                    "name": i.name,
                    "kind": i.kind,
                    "lag": i.lag,
                    "mode": i.mode,
                    "neighbor": i.neighbor,
                    "neighbor_port": i.neighbor_port,
                }
            )
    for v in session.scalars(
        select(InvVlan).where(InvVlan.tenant_id == tenant_id, InvVlan.source_system_id.in_(sources))
    ):
        for lbl in set(v.epgs or []) & wanted:
            out[lbl]["vlans"].add(v.vid)
    return out


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
    physical = _physical(session, tenant_id, epgs)
    site_index = sites.SiteIndex(session, tenant_id)
    vms = vms_of(session, ids)
    out: dict[int, dict] = {}
    for epg in epgs:
        phys = physical.get(inventory_label(epg)) if epg.kind == "epg" else None
        host = session.get(NetworkObject, epg.network_object_id) if epg.network_object_id else None
        site = site_index.ref(host.site_id) if host and host.site_id else None
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
                {
                    "ip": s.ip,
                    "origin": s.origin,
                    "scope": s.scope,
                    "ipam": _ipam_net(ctx, _network(s.ip)),
                }
                for s in sorted(subnets[epg.id], key=lambda x: (x.origin, x.ip))
            ],
            "endpoint_count": len(eps),
            "endpoints": [
                {"ip": ep.ip, "mac": ep.mac, "dns_name": _ip_name(ctx, ep.ip)}
                for ep in eps[:MAX_ENDPOINTS]
            ],
            "server": (
                {"name": host.name, "ip": host.value, "site": _site_label(site)} if host else None
            ),
            "selectors": epg.selectors or [],
            "pref_group": epg.pref_group,
            "vlans": sorted(phys["vlans"]) if phys else [],
            "port_count": len(phys["ports"]) if phys else 0,
            "ports": phys["ports"][:MAX_PORTS] if phys else [],
            "vms": vms.get(epg.id, []),
        }
    return out


def vms_of(session: Session, epg_ids: list[int]) -> dict[int, list[dict]]:
    """VMs an den Portgruppen der EPGs (vCenter, ADR 0017): Name, IPs, Status, Portgruppe."""
    from app.models import Portgroup, Vm, VmNic  # spaet: Modelle des VMware-Inventars
    from app.store.virtual import nic_join  # spaet: virtual importiert dieses Modul

    if not epg_ids:
        return {}
    out: dict[int, list[dict]] = {}
    rows = session.execute(
        select(Portgroup.epg_id, Portgroup.epg_match, Vm, VmNic)
        .join(Vm, Vm.source_system_id == Portgroup.source_system_id)
        .join(VmNic, (VmNic.vm_id == Vm.id) & nic_join())
        .where(Portgroup.epg_id.in_(epg_ids), Vm.template.is_(False))
        .order_by(Vm.name)
    )
    for epg_id, match, vm, nic in rows:
        out.setdefault(epg_id, []).append(
            {
                "name": vm.name,
                "ips": nic.ips or [],
                "power_state": vm.power_state,
                "portgroup": nic.portgroup,
                "match": match,
            }
        )
    return out


def _site_label(site: dict | None) -> str | None:
    """Standort eines Servers: Code, beim virtuellen mit den physischen (000 (chzrh1, chbsl1))."""
    if not site:
        return None
    return f"{site['code']} ({', '.join(site['physical'])})" if site["physical"] else site["code"]
