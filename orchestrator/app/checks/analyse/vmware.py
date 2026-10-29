"""Prüfung: VMs und Portgruppen (vCenter) gegen IPAM und ACI.

Steckbare Prüfung (0.110.0, ADR 0039) - aus `store/reconcile.py` hierher verschoben."""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.checks import Check
from app.models import (
    Epg,
    FindingType,
    Portgroup,
    Severity,
    SubjectKind,
    Vm,
    VmIp,
)
from app.store import virtual
from app.store.reconcile import Issue, _source_names


def _virtual_issues(session: Session, tenant_id: int, ctx) -> list[Issue]:
    """VM-IPs ohne Eintrag im fuehrenden IPAM; Portgruppen nach ACI-Schema ohne EPG."""
    out: list[Issue] = []
    names = _source_names(session, tenant_id)
    primary = ctx.primary if ctx is not None else None
    if primary is not None:
        missing: dict[int, list[str]] = defaultdict(list)
        vms: dict[int, Vm] = {}
        for ip, vm in session.execute(
            select(VmIp.ip, Vm)
            .join(Vm, Vm.id == VmIp.vm_id)
            .where(VmIp.tenant_id == tenant_id, Vm.template.is_(False))
            .order_by(Vm.name, VmIp.ip)
        ):
            if not primary.ip_records(ip):
                missing[vm.id].append(ip)
                vms[vm.id] = vm
        for vm_id, ips in missing.items():
            vm = vms[vm_id]
            out.append(
                Issue(
                    FindingType.VM_NOT_IN_IPAM,
                    SubjectKind.VM,
                    vm.name,
                    vm.source_system_id,
                    detail={
                        "source": names.get(vm.source_system_id),
                        "ips": ips,
                        "reference": primary.source.name,
                        "cluster": vm.cluster,
                        "hostname": vm.hostname,
                    },
                )
            )
    has_aci = session.scalar(select(Epg.id).where(Epg.tenant_id == tenant_id).limit(1))
    if has_aci:
        # nur ueber das VLAN zugeordnet, obwohl der Name eine andere EPG nennt (Dev-Befund 0.33.0)
        by_vlan = list(
            session.execute(
                select(Portgroup, Epg)
                .join(Epg, Epg.id == Portgroup.epg_id)
                .where(Portgroup.tenant_id == tenant_id, Portgroup.epg_match == "vlan")
            )
        )
        for pg, epg in by_vlan:
            parts = virtual.epg_from_name(pg.name)
            if parts is None:
                continue
            out.append(
                Issue(
                    FindingType.PORTGROUP_NAME_MISMATCH,
                    SubjectKind.PORTGROUP,
                    pg.name,
                    pg.source_system_id,
                    detail={
                        "source": names.get(pg.source_system_id),
                        "tenant": parts[0],
                        "ap": parts[1],
                        "epg": parts[2],
                        "assigned": "/".join(x for x in (epg.aci_tenant, epg.ap, epg.name) if x),
                        "vlan": pg.vlan,
                        "vms": pg.vm_count,
                    },
                )
            )
        for pg in session.scalars(
            select(Portgroup).where(Portgroup.tenant_id == tenant_id, Portgroup.epg_id.is_(None))
        ):
            parts = virtual.epg_from_name(pg.name)
            if parts is None:
                continue
            out.append(
                Issue(
                    FindingType.PORTGROUP_NO_EPG,
                    SubjectKind.PORTGROUP,
                    pg.name,
                    pg.source_system_id,
                    Severity.INFO,
                    detail={
                        "source": names.get(pg.source_system_id),
                        "tenant": parts[0],
                        "ap": parts[1],
                        "epg": parts[2],
                        "vlan": pg.vlan,
                        "vms": pg.vm_count,
                    },
                )
            )
    return out


CHECK = Check(
    "vmware",
    "analyse",
    ("VMware: VMs und Portgruppen", "VMware: VMs and port groups"),
    (
        "IP einer VM fehlt im IPAM, Portgruppe nach ACI-Schema ohne EPG, Name passt nicht zur EPG",
        "IP of a VM missing in the IPAM, port group by ACI schema without EPG, name does not match "
        "the EPG",
    ),
    frozenset(
        {
            FindingType.VM_NOT_IN_IPAM,
            FindingType.PORTGROUP_NO_EPG,
            FindingType.PORTGROUP_NAME_MISMATCH,
        }
    ),
    lambda session, tenant_id, cx: _virtual_issues(session, tenant_id, cx.refs),
    order=55,
)
