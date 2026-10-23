"""VMware-Inventar (ADR 0017): VMs, NICs, IPs, Portgruppen - Ersetzen je Lauf, Zuordnung
Portgruppe -> ACI-EPG und Nachschlagen IP -> VM fuer Regeln, Objekte, Suche und Doku.

Zuordnung Portgruppe -> EPG (in dieser Reihenfolge):
1. Name nach ACI-VMM-Schema `<Tenant>|<AP>|<EPG>` (auch `~`) -> EPG-DN; passen die Namen nicht
   exakt, der Vergleich ohne Praefixe (`TE_`, `AP_`, `EG_` ...),
2. das VLAN der Portgruppe, wenn es im Netzinventar der APICs genau einer EPG gehoert.
"""

import re
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Epg, InvVlan, Portgroup, Vm, VmIp, VmNic
from app.schemas.collector import VirtualInventory
from app.store.epgs import inventory_label

_SPLIT = re.compile(r"[|~]")
_PREFIX = re.compile(r"^[a-z]{2,3}_", re.IGNORECASE)


def replace(session: Session, run, inv: VirtualInventory) -> int:
    """VMs und Portgruppen des vCenters ersetzen (aktueller Stand); Rueckgabe: Anzahl VMs."""
    sid = run.source_system_id
    vm_ids = select(Vm.id).where(Vm.source_system_id == sid)
    session.execute(delete(VmIp).where(VmIp.vm_id.in_(vm_ids)))
    session.execute(delete(VmNic).where(VmNic.vm_id.in_(vm_ids)))
    session.execute(
        delete(Vm).where(Vm.source_system_id == sid).execution_options(synchronize_session="fetch")
    )
    session.execute(delete(Portgroup).where(Portgroup.source_system_id == sid))
    session.flush()
    now = datetime.now(UTC)
    base = {"tenant_id": run.tenant_id, "source_system_id": sid}
    vms: list[Vm] = []
    for v in inv.vms:
        vm = Vm(
            **base,
            **v.model_dump(exclude={"nics"}),
            collector_run_id=run.id,
            last_seen=now,
        )
        vm.nics = [VmNic(**n.model_dump(exclude={"ips"}), ips=n.ips or None) for n in v.nics]
        vms.append(vm)
    session.add_all(vms)
    session.add_all(Portgroup(**base, **p.model_dump()) for p in inv.portgroups)
    session.flush()
    session.add_all(
        VmIp(tenant_id=run.tenant_id, vm_id=vm.id, nic_id=nic.id, ip=ip)
        for vm in vms
        for nic in vm.nics
        for ip in nic.ips or []
    )
    session.flush()
    return len(vms)


def nic_join():
    """SQL-Bedingung NIC <-> Portgruppe: gleicher Name und - wo beide es kennen - gleiches
    VLAN, bei Portgruppen eines Distributed Switch auch derselbe Switch (Standard-vSwitches
    heissen je Host gleich, `vSwitch0`). Gleichnamige Portgruppen auf verschiedenen Switches
    bzw. mit anderem VLAN sind verschiedene Netze (Dev-Befund 0.43.0); der Name allein ordnete
    eine VM der EPG einer fremden Portgruppe zu."""
    return (
        (VmNic.portgroup == Portgroup.name)
        & (
            (Portgroup.kind != "dvs")
            | VmNic.switch.is_(None)
            | Portgroup.switch.is_(None)
            | (VmNic.switch == Portgroup.switch)
        )
        & (VmNic.vlan.is_(None) | Portgroup.vlan.is_(None) | (VmNic.vlan == Portgroup.vlan))
    )


class PortgroupIndex:
    """Portgruppen je (vCenter, Name) - auch mehrere gleichnamige (`nic_join`)."""

    def __init__(self, groups):
        self.groups = list(groups)
        self.by_name: dict[tuple[int, str], list[Portgroup]] = defaultdict(list)
        for pg in self.groups:
            self.by_name[(pg.source_system_id, pg.name)].append(pg)

    @classmethod
    def load(cls, session: Session, tenant_id: int) -> "PortgroupIndex":
        return cls(session.scalars(select(Portgroup).where(Portgroup.tenant_id == tenant_id)))

    def of(self, source_system_id: int, nic) -> Portgroup | None:
        """Portgruppe einer NIC; mehrdeutig (verschiedene EPGs) = None statt geraten."""
        found = [
            pg
            for pg in self.by_name.get((source_system_id, nic.portgroup or ""), [])
            if (
                pg.kind != "dvs"
                or nic.switch is None
                or pg.switch is None
                or nic.switch == pg.switch
            )
            and (nic.vlan is None or pg.vlan is None or nic.vlan == pg.vlan)
        ]
        if len({pg.epg_id for pg in found}) > 1:
            return None
        return found[0] if found else None


def _bare(name: str) -> str:
    return _PREFIX.sub("", name.strip()).lower()


def epg_from_name(name: str) -> tuple[str, str, str] | None:
    """(Tenant, AP, EPG) aus einem Portgruppen-Namen nach ACI-VMM-Schema, sonst None."""
    parts = _SPLIT.split(name)
    return (parts[0], parts[1], parts[2]) if len(parts) == 3 and all(parts) else None


def map_epgs(session: Session, tenant_id: int) -> None:
    """Portgruppen des Mandanten ihrer EPG zuordnen (Name, sonst eindeutiges VLAN)."""
    groups = list(session.scalars(select(Portgroup).where(Portgroup.tenant_id == tenant_id)))
    if not groups:
        return
    epgs = list(session.scalars(select(Epg).where(Epg.tenant_id == tenant_id, Epg.kind == "epg")))
    by_dn = {e.dn: e for e in epgs if e.dn}
    by_bare: dict[tuple, list[Epg]] = defaultdict(list)
    by_label = {inventory_label(e): e for e in epgs}
    for e in epgs:
        by_bare[(_bare(e.aci_tenant or ""), _bare(e.ap or ""), _bare(e.name))].append(e)
    by_vlan: dict[int, set[str]] = defaultdict(set)
    for vid, labels in session.execute(
        select(InvVlan.vid, InvVlan.epgs).where(InvVlan.tenant_id == tenant_id)
    ):
        by_vlan[vid] |= set(labels or [])
    for pg in groups:
        epg, how = None, None
        parts = epg_from_name(pg.name)
        if parts is not None:
            t, a, e = parts
            epg = by_dn.get(f"uni/tn-{t}/ap-{a}/epg-{e}")
            if epg is None:
                found = by_bare.get((_bare(t), _bare(a), _bare(e)), [])
                epg = found[0] if len(found) == 1 else None
            how = "name" if epg is not None else None
        if epg is None and pg.vlan is not None and len(by_vlan.get(pg.vlan, ())) == 1:
            (label,) = by_vlan[pg.vlan]
            epg = by_label.get(label)
            how = "vlan" if epg is not None else None
        pg.epg_id = epg.id if epg is not None else None
        pg.epg_match = how
    session.flush()


class Lookup:
    """IP -> VMs eines Mandanten und Portgruppe -> EPG, einmal geladen."""

    def __init__(self, session: Session, tenant_id: int | None, ips: set[str] | None = None):
        self.by_ip: dict[str, list[Vm]] = defaultdict(list)
        self.nic_of: dict[tuple[int, str], VmNic] = {}
        self.portgroups = PortgroupIndex([])
        self.epgs: dict[int, Epg] = {}
        self.session = session
        self._ips_of: dict[int, list[str]] | None = None
        if tenant_id is None:
            return
        stmt = select(VmIp, Vm).join(Vm, Vm.id == VmIp.vm_id).where(VmIp.tenant_id == tenant_id)
        if ips is not None:
            if not ips:
                return
            stmt = stmt.where(VmIp.ip.in_(ips))
        rows = session.execute(stmt).all()
        if not rows:
            return
        nic_ids = {ip.nic_id for ip, _ in rows if ip.nic_id}
        nics = (
            {n.id: n for n in session.scalars(select(VmNic).where(VmNic.id.in_(nic_ids)))}
            if nic_ids
            else {}
        )
        for ip, vm in rows:
            if vm not in self.by_ip[ip.ip]:
                self.by_ip[ip.ip].append(vm)
            if ip.nic_id in nics:
                self.nic_of[(vm.id, ip.ip)] = nics[ip.nic_id]
        self.portgroups = PortgroupIndex.load(session, tenant_id)
        epg_ids = {pg.epg_id for pg in self.portgroups.groups if pg.epg_id}
        if epg_ids:
            self.epgs = {e.id: e for e in session.scalars(select(Epg).where(Epg.id.in_(epg_ids)))}

    def vm_ips(self, vm_id: int) -> list[str]:
        """Alle IPs einer VM - beim ersten Aufruf fuer alle gefundenen VMs geladen."""
        if self._ips_of is None:
            self._ips_of = defaultdict(list)
            ids = {vm.id for vms in self.by_ip.values() for vm in vms}
            if ids:
                for vid, addr in self.session.execute(
                    select(VmIp.vm_id, VmIp.ip).where(VmIp.vm_id.in_(ids)).order_by(VmIp.id)
                ):
                    if addr not in self._ips_of[vid]:
                        self._ips_of[vid].append(addr)
        return self._ips_of.get(vm_id, [])

    def info(self, value: str | None) -> dict | None:
        """Kurzinfo der VM hinter einer IP (auch a.b.c.d/32) fuer Regeln und Objekte."""
        ip = (value or "").split("/")[0]
        vms = self.by_ip.get(ip)
        if not vms:
            return None
        vm = vms[0]
        nic = self.nic_of.get((vm.id, ip))
        pg = self.portgroups.of(vm.source_system_id, nic) if nic and nic.portgroup else None
        epg = self.epgs.get(pg.epg_id) if pg and pg.epg_id else None
        return {
            "name": vm.name,
            "id": vm.id,
            "power_state": vm.power_state,
            "cluster": vm.cluster,
            "portgroup": nic.portgroup if nic else None,
            "vlan": nic.vlan if nic else None,
            "epg": inventory_label(epg) if epg is not None else None,
            "others": [v.name for v in vms[1:]],  # gleiche IP an mehreren VMs
            # Detailansichten (0.14.0): was das vCenter sonst ueber die VM weiss
            "template": vm.template,
            "guest_os": vm.guest_os,
            "hostname": vm.hostname,
            "datacenter": vm.datacenter,
            "host": vm.host,
            "vcpus": vm.vcpus,
            "memory_mb": vm.memory_mb,
            "disk_gb": vm.disk_gb,
            "notes": vm.annotation,
            "nic": nic.name if nic else None,
            "mac": nic.mac if nic else None,
            "connected": nic.connected if nic else None,
            "ips": self.vm_ips(vm.id) or [ip],
            "epg_match": pg.epg_match if pg else None,
        }

    def epg_ids(self, value: str | None) -> set[int]:
        """EPGs der Portgruppen, an denen die VMs mit dieser IP haengen."""
        ip = (value or "").split("/")[0]
        out: set[int] = set()
        for vm in self.by_ip.get(ip, []):
            nic = self.nic_of.get((vm.id, ip))
            pg = self.portgroups.of(vm.source_system_id, nic) if nic else None
            if pg is not None and pg.epg_id:
                out.add(pg.epg_id)
        return out

    def names(self, value: str | None) -> list[str]:
        return [vm.name for vm in self.by_ip.get((value or "").split("/")[0], [])]
