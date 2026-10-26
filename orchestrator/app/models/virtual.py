"""VMware-Inventar (ADR 0017): VMs mit NICs und IPs, Portgruppen - je vCenter und Lauf ersetzt.

Eine Portgruppe ist einer ACI-EPG zugeordnet, wenn ihr Name dem VMM-Schema
`<Tenant>|<AP>|<EPG>` entspricht (`epg_match=name`) bzw. ihr VLAN genau einer EPG gehoert
(`vlan`). Die Zuordnung wird im Abgleich neu berechnet (`store.virtual.map_epgs`), weil sich
EPGs mit jedem APIC-Lauf aendern koennen. `vm_ip` dient dem Nachschlagen IP -> VM (Regeln,
Objekte, Suche, IPAM-Abgleich).
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Vm(Base):
    __tablename__ = "vm"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    source_system_id: Mapped[int] = mapped_column(ForeignKey("source_system.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    uuid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    power_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    template: Mapped[bool] = mapped_column(Boolean, default=False)
    guest_os: Mapped[str | None] = mapped_column(String(200), nullable=True)
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    datacenter: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cluster: Mapped[str | None] = mapped_column(String(100), nullable=True)
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vcpus: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_gb: Mapped[float | None] = mapped_column(Float, nullable=True)
    annotation: Mapped[str | None] = mapped_column(Text, nullable=True)
    collector_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("collector_run.id"), nullable=True
    )
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    nics: Mapped[list["VmNic"]] = relationship(
        back_populates="vm", cascade="all, delete-orphan", order_by="VmNic.id"
    )


class VmNic(Base):
    __tablename__ = "vm_nic"

    id: Mapped[int] = mapped_column(primary_key=True)
    vm_id: Mapped[int] = mapped_column(ForeignKey("vm.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    mac: Mapped[str | None] = mapped_column(String(32), nullable=True)
    portgroup: Mapped[str | None] = mapped_column(String(255), nullable=True)
    switch: Mapped[str | None] = mapped_column(String(100), nullable=True)
    vlan: Mapped[int | None] = mapped_column(Integer, nullable=True)
    connected: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ips: Mapped[list | None] = mapped_column(JSON, nullable=True)

    vm: Mapped[Vm] = relationship(back_populates="nics")


class VmIp(Base):
    """IP einer VM (aus den VMware Tools) - Nachschlagen IP -> VM."""

    __tablename__ = "vm_ip"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    vm_id: Mapped[int] = mapped_column(ForeignKey("vm.id", ondelete="CASCADE"), index=True)
    nic_id: Mapped[int | None] = mapped_column(
        ForeignKey("vm_nic.id", ondelete="CASCADE"), nullable=True
    )
    ip: Mapped[str] = mapped_column(String(64), index=True)


class Portgroup(Base):
    __tablename__ = "portgroup"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    source_system_id: Mapped[int] = mapped_column(ForeignKey("source_system.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(16), default="dvs")  # dvs | standard
    switch: Mapped[str | None] = mapped_column(String(100), nullable=True)
    key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vlan: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vlan_ranges: Mapped[str | None] = mapped_column(String(200), nullable=True)
    vm_count: Mapped[int] = mapped_column(Integer, default=0)
    # ACI-EPG der Portgruppe (Name Tenant|AP|EPG bzw. eindeutiges VLAN) - im Abgleich gesetzt
    epg_id: Mapped[int | None] = mapped_column(
        ForeignKey("epg.id", ondelete="SET NULL"), nullable=True, index=True
    )
    epg_match: Mapped[str | None] = mapped_column(String(8), nullable=True)  # name | vlan
