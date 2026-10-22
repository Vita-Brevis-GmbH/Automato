"""VMware-Inventar (ADR 0017): VMs mit NICs, IPs, Portgruppe und EPG; Portgruppen mit EPG.

- `GET /vms`         seitenweise mit Gesamtzahl; Suche ueber Name, Hostname, IP, Portgruppe,
                     EPG, Cluster, Host, Gast-OS, Notiz; Filter Quellsystem, EPG.
- `GET /portgroups`  Portgruppen mit VLAN, Anzahl VMs und zugeordneter EPG (Name | VLAN).
- `GET /portgroups/{id}` Detail (0.13.0): EPG mit BD/VRF, Subnetze (ACI-EPG/BD, Inventar mit
                     gleichem VLAN, IPAM-Netze der VM-IPs) und die VMs an der Portgruppe.
Lesen = `viewer`, mandantsgescoped. Rein lesend.
"""

import ipaddress

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.customer_filter import customer_view, own_view
from app.api.customer_filter import ids as view_ids
from app.audit import reads
from app.auth.deps import require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import Epg, EpgSubnet, InvSubnet, Portgroup, SourceSystem, Vm, VmNic
from app.store import references
from app.store.epgs import inventory_label

router = APIRouter(tags=["virtual"])


def _tenant(principal: Principal, tenant_id: int | None) -> int:
    tid = tenant_scope(principal, tenant_id)
    if tid is None:
        raise HTTPException(status_code=422, detail="tenant_id required")
    return tid


def _context(session: Session, tid: int) -> tuple[dict, dict, dict]:
    names = dict(
        session.execute(
            select(SourceSystem.id, SourceSystem.name).where(SourceSystem.tenant_id == tid)
        ).all()
    )
    groups = {
        (pg.source_system_id, pg.name): pg
        for pg in session.scalars(select(Portgroup).where(Portgroup.tenant_id == tid))
    }
    epg_ids = {pg.epg_id for pg in groups.values() if pg.epg_id}
    epgs = (
        {e.id: e for e in session.scalars(select(Epg).where(Epg.id.in_(epg_ids)))}
        if epg_ids
        else {}
    )
    return names, groups, epgs


@router.get("/vms")
def list_vms(
    tenant_id: int | None = None,
    source_system_id: int | None = None,
    q: str | None = None,
    epg: str | None = Query(None, description="nur VMs dieser EPG (Tenant/AP/EPG)"),
    customer_id: int | None = Query(None, description="nur VMs dieses Kunden (ADR 0018)"),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    tid = _tenant(principal, tenant_id)
    reads.maybe_audit_read(session, principal, tid, "virtual")
    stmt = select(Vm).options(selectinload(Vm.nics)).where(Vm.tenant_id == tid)
    view = customer_view(session, principal, tid, customer_id)
    if view is not None:
        stmt = stmt.where(Vm.id.in_(view.ids("vm")))
    if source_system_id is not None:
        stmt = stmt.where(Vm.source_system_id == source_system_id)
    vms = list(session.scalars(stmt.order_by(Vm.name, Vm.id)))
    names, groups, epgs = _context(session, tid)
    ctx = references.context(session, tid)
    primary = ctx.primary

    def row(vm: Vm) -> dict:
        nics = []
        for n in vm.nics:
            pg = groups.get((vm.source_system_id, n.portgroup or ""))
            e = epgs.get(pg.epg_id) if pg and pg.epg_id else None
            nics.append(
                {
                    "name": n.name,
                    "mac": n.mac,
                    "portgroup": n.portgroup,
                    "switch": n.switch,
                    "vlan": n.vlan,
                    "connected": n.connected,
                    "ips": [
                        {
                            "ip": ip,
                            "dns": references.dns_names(ctx, ip),
                            "in_ipam": bool(primary.ip_records(ip)) if primary else None,
                        }
                        for ip in n.ips or []
                    ],
                    "epg": inventory_label(e) if e else None,
                    "epg_match": pg.epg_match if pg else None,
                }
            )
        return {
            "id": vm.id,
            "source_system_id": vm.source_system_id,
            "source": names.get(vm.source_system_id),
            "name": vm.name,
            "power_state": vm.power_state,
            "template": vm.template,
            "guest_os": vm.guest_os,
            "hostname": vm.hostname,
            "datacenter": vm.datacenter,
            "cluster": vm.cluster,
            "host": vm.host,
            "vcpus": vm.vcpus,
            "memory_mb": vm.memory_mb,
            "disk_gb": vm.disk_gb,
            "annotation": vm.annotation,
            "nics": nics,
        }

    rows = [row(vm) for vm in vms]
    if epg:
        rows = [r for r in rows if any(n["epg"] == epg for n in r["nics"])]
    if q:
        ql = q.strip().lower()

        def hit(r: dict) -> bool:
            texts = [r[k] for k in ("name", "hostname", "cluster", "host", "guest_os")]
            texts += [r["annotation"], r["datacenter"], r["source"]]
            for n in r["nics"]:
                texts += [n["portgroup"], n["epg"], n["mac"], str(n["vlan"] or "")]
                texts += [i["ip"] for i in n["ips"]] + [d for i in n["ips"] for d in i["dns"]]
            return any(ql in str(t).lower() for t in texts if t)

        rows = [r for r in rows if hit(r)]
    return {"total": len(rows), "rows": rows[offset : offset + limit]}


@router.get("/portgroups")
def list_portgroups(
    tenant_id: int | None = None,
    q: str | None = None,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    tid = _tenant(principal, tenant_id)
    reads.maybe_audit_read(session, principal, tid, "virtual")
    names, groups, epgs = _context(session, tid)
    view = customer_view(session, principal, tid, customer_id)
    mine = set(session.scalars(view.ids("epg"))) if view else None  # Portgruppen seiner EPGs
    rows = []
    for pg in sorted(groups.values(), key=lambda p: (p.name, p.source_system_id)):
        if mine is not None and pg.epg_id not in mine:
            continue
        e = epgs.get(pg.epg_id) if pg.epg_id else None
        rows.append(
            {
                "id": pg.id,
                "source_system_id": pg.source_system_id,
                "source": names.get(pg.source_system_id),
                "name": pg.name,
                "kind": pg.kind,
                "switch": pg.switch,
                "vlan": pg.vlan,
                "vlan_ranges": pg.vlan_ranges,
                "vm_count": pg.vm_count,
                "epg": inventory_label(e) if e else None,
                "epg_match": pg.epg_match,
            }
        )
    if q:
        ql = q.strip().lower()
        rows = [
            r
            for r in rows
            if any(ql in str(r[k] or "").lower() for k in ("name", "switch", "vlan", "epg"))
        ]
    return rows


def _network(value: str) -> str | None:
    try:
        return str(ipaddress.ip_interface(value.strip()).network)
    except ValueError:
        return None


@router.get("/portgroups/{pg_id}")
def portgroup_detail(
    pg_id: int,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Portgruppe im Detail (Dev-Befund 0.13.0): EPG, Subnetze aus ACI (EPG/BD), dem
    Netzinventar (gleiches VLAN) und dem IPAM (Netze der VM-IPs) sowie die VMs."""
    pg = session.get(Portgroup, pg_id)
    if pg is None:
        raise HTTPException(status_code=404, detail="portgroup not found")
    tenant_scope(principal, pg.tenant_id)
    view = own_view(session, principal, pg.tenant_id)
    reads.maybe_audit_read(session, principal, pg.tenant_id, "virtual")
    if view is not None and (pg.epg_id is None or not view.contains(session, "epg", pg.epg_id)):
        raise HTTPException(status_code=404, detail="portgroup not found")  # ADR 0018
    own_vms = view_ids(session, view, "vm")
    names = dict(
        session.execute(
            select(SourceSystem.id, SourceSystem.name).where(SourceSystem.tenant_id == pg.tenant_id)
        ).all()
    )
    ctx = references.context(session, pg.tenant_id)
    primary = ctx.primary
    epg = session.get(Epg, pg.epg_id) if pg.epg_id else None
    subnets: dict[str, dict] = {}

    def add(prefix: str | None, source: str, **extra) -> None:
        if not prefix:
            return
        entry = subnets.setdefault(prefix, {"prefix": prefix, "sources": []})
        if source not in entry["sources"]:
            entry["sources"].append(source)
        for k, v in extra.items():  # erste bekannte Angabe gilt (Gateway, VRF, Beschreibung)
            if entry.get(k) in (None, "") and v not in (None, ""):
                entry[k] = v

    if epg is not None:
        for sub in session.scalars(select(EpgSubnet).where(EpgSubnet.epg_id == epg.id)):
            origin = {"bd": "ACI-BD", "ext": "ACI-L3Out"}.get(sub.origin, "ACI-EPG")
            add(_network(sub.ip), origin, gateway=sub.ip)
    if pg.vlan is not None:
        for sub in session.scalars(
            select(InvSubnet).where(InvSubnet.tenant_id == pg.tenant_id, InvSubnet.vlan == pg.vlan)
        ):
            # nur Hinweis: VLAN-Nummern gelten je Geraet/Domaene, die Quelle steht dabei
            where = f"{sub.context_name} ({names.get(sub.source_system_id, '?')})"
            add(sub.prefix, f"VLAN {pg.vlan} in {where}", gateway=sub.gateway, vrf=sub.vrf)
    vms = []
    rows = session.execute(
        select(Vm, VmNic)
        .join(VmNic, VmNic.vm_id == Vm.id)
        .where(Vm.source_system_id == pg.source_system_id, VmNic.portgroup == pg.name)
        .order_by(Vm.name, VmNic.name)
    ).all()
    for vm, nic in rows:
        ips = []
        for ip in nic.ips or []:
            found = primary.containing_prefixes(ip)[:1] if primary else []
            if found:
                add(_network(found[0].value) or found[0].value, f"IPAM {primary.source.name}",
                    description=found[0].description)  # fmt: skip
            ips.append(
                {
                    "ip": ip,
                    "dns": references.dns_names(ctx, ip),
                    "in_ipam": bool(primary.ip_records(ip)) if primary else None,
                }
            )
        vms.append(
            {
                "id": vm.id,
                "name": vm.name,
                "power_state": vm.power_state,
                "template": vm.template,
                "nic": nic.name,
                "mac": nic.mac,
                "connected": nic.connected,
                "ips": ips,
                "cluster": vm.cluster,
                "host": vm.host,
            }
        )
    return {
        "id": pg.id,
        "name": pg.name,
        "kind": pg.kind,
        "switch": pg.switch,
        "vlan": pg.vlan,
        "vlan_ranges": pg.vlan_ranges,
        "vm_count": pg.vm_count,
        "source": names.get(pg.source_system_id),
        "epg_match": pg.epg_match,
        "epg": {
            "label": inventory_label(epg),
            "dn": epg.dn,
            "tenant": epg.aci_tenant,
            "ap": epg.ap,
            "name": epg.name,
            "bd": epg.bd,
            "vrf": epg.vrf,
        }
        if epg is not None
        else None,
        "subnets": sorted(subnets.values(), key=lambda x: x["prefix"]),
        "vms": vms if own_vms is None else [v for v in vms if v["id"] in own_vms],
    }
