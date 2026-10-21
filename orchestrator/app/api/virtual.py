"""VMware-Inventar (ADR 0017): VMs mit NICs, IPs, Portgruppe und EPG; Portgruppen mit EPG.

- `GET /vms`         seitenweise mit Gesamtzahl; Suche ueber Name, Hostname, IP, Portgruppe,
                     EPG, Cluster, Host, Gast-OS, Notiz; Filter Quellsystem, EPG.
- `GET /portgroups`  Portgruppen mit VLAN, Anzahl VMs und zugeordneter EPG (Name | VLAN).
Lesen = `viewer`, mandantsgescoped. Rein lesend.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.deps import require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import Epg, Portgroup, SourceSystem, Vm
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
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    tid = _tenant(principal, tenant_id)
    stmt = select(Vm).options(selectinload(Vm.nics)).where(Vm.tenant_id == tid)
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
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    tid = _tenant(principal, tenant_id)
    names, groups, epgs = _context(session, tid)
    rows = []
    for pg in sorted(groups.values(), key=lambda p: (p.name, p.source_system_id)):
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
