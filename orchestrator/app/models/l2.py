"""Endpunkte der Switches (0.63.0, ADR 0025 Nr. 8): MAC-Adress-Tabelle und ARP.

Anders als das Inventar werden die Eintraege nicht je Lauf ersetzt, sondern **gesammelt**: ein
Server, der gerade still ist, faellt aus der MAC-Tabelle des Switches (Standard 5 min), steht
hier aber weiter mit `last_seen`. Aktuell ist je Switch, was der letzte Lauf gesehen hat
(`last_seen` = neuster Zeitpunkt des Switches); aeltere Eintraege gelten als „frueher gesehen“
und fallen nach der Frist `retention_l2_days` weg (`store/retention`).
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MacEntry(Base):
    """Eine MAC in einem VLAN an einem Port eines Switches."""

    __tablename__ = "mac_entry"
    __table_args__ = (Index("ix_mac_entry_tenant_mac", "tenant_id", "mac"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    source_system_id: Mapped[int] = mapped_column(ForeignKey("source_system.id"), index=True)
    device: Mapped[str] = mapped_column(String(200))
    mac: Mapped[str] = mapped_column(String(17))  # aa:bb:cc:dd:ee:ff
    vlan: Mapped[int | None] = mapped_column(Integer, nullable=True)
    port: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str | None] = mapped_column(String(16), nullable=True)  # dynamic | static
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ArpEntry(Base):
    """IP -> MAC aus der ARP-Tabelle eines Switches mit L3-Interface."""

    __tablename__ = "arp_entry"
    __table_args__ = (
        Index("ix_arp_entry_tenant_ip", "tenant_id", "ip"),
        Index("ix_arp_entry_tenant_mac", "tenant_id", "mac"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    source_system_id: Mapped[int] = mapped_column(ForeignKey("source_system.id"), index=True)
    device: Mapped[str] = mapped_column(String(200))
    ip: Mapped[str] = mapped_column(String(64))
    mac: Mapped[str] = mapped_column(String(17))
    interface: Mapped[str | None] = mapped_column(String(200), nullable=True)
    vrf: Mapped[str | None] = mapped_column(String(100), nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))


L2_MODELS = (MacEntry, ArpEntry)
