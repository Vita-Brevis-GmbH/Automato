"""Netzinventar je Quellsystem (Inventar-Ebene, ADR 0010).

Geraete (Firewall, ACI-Leaf/-Spine/-Controller), Ports/Interfaces mit VLAN-Konfiguration
(Access/Trunk, LAG/vPC, Gegenstelle), VLANs (inkl. der EPG-VLANs), reale Subnetze (FortiGate-
Interfaces, ACI-BD-/EPG-Subnetze) und VRFs. Wird je Lauf des Quellsystems ersetzt und ist die
Quelle fuer die Ablage in NetBox (NetBox-Sync).
"""

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class _InventoryRow:
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    source_system_id: Mapped[int] = mapped_column(ForeignKey("source_system.id"), index=True)


class InvDevice(_InventoryRow, Base):
    __tablename__ = "inv_device"

    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(32))  # firewall | leaf | spine | controller
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    serial: Mapped[str | None] = mapped_column(String(100), nullable=True)
    node_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mgmt_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)


class InvInterface(_InventoryRow, Base):
    __tablename__ = "inv_interface"

    device: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))  # physical | vlan | lag | loopback | ...
    enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    mode: Mapped[str | None] = mapped_column(String(16), nullable=True)  # access | tagged
    untagged_vlan: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tagged_vlans: Mapped[list | None] = mapped_column(JSON, nullable=True)
    lag: Mapped[str | None] = mapped_column(String(200), nullable=True)
    parent: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ips: Mapped[list | None] = mapped_column(JSON, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    zone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    vdom: Mapped[str | None] = mapped_column(String(64), nullable=True)  # FortiGate-VDOM
    neighbor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    neighbor_port: Mapped[str | None] = mapped_column(String(100), nullable=True)
    policy_group: Mapped[str | None] = mapped_column(String(200), nullable=True)
    epgs: Mapped[list | None] = mapped_column(JSON, nullable=True)


class InvVlan(_InventoryRow, Base):
    __tablename__ = "inv_vlan"

    vid: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    group: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    epgs: Mapped[list | None] = mapped_column(JSON, nullable=True)


class InvSubnet(_InventoryRow, Base):
    __tablename__ = "inv_subnet"

    prefix: Mapped[str] = mapped_column(String(64), index=True)
    gateway: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vrf: Mapped[str | None] = mapped_column(String(200), nullable=True)
    vlan: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context: Mapped[str] = mapped_column(String(16))  # interface | bd | epg
    context_name: Mapped[str] = mapped_column(String(200), default="")
    aci_tenant: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ap: Mapped[str | None] = mapped_column(String(100), nullable=True)
    epg: Mapped[str | None] = mapped_column(String(200), nullable=True)
    bd: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    vdom: Mapped[str | None] = mapped_column(String(64), nullable=True)  # FortiGate-VDOM


class InvVrf(_InventoryRow, Base):
    __tablename__ = "inv_vrf"

    name: Mapped[str] = mapped_column(String(200))


INVENTORY_MODELS = (InvDevice, InvInterface, InvVlan, InvSubnet, InvVrf)
