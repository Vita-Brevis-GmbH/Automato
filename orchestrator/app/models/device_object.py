"""Firewall-Objekte, wie sie auf dem Geraet stehen (Inventar-Ebene, ADR 0010).

Je Quellsystem und Lauf ersetzt: Adressen, Adressgruppen (mit Mitgliedern), VIPs, IP-Pools,
Services und Service-Gruppen - mit Namen, Typ, aufgeloesten Werten, Kommentar und Farbe.
Regeln verweisen per Name darauf (`rule_object_ref`). `network_object_id` verknuepft ein
einzelnes Adressobjekt mit der kanonischen Werte-Ebene (Matching, Referenzdaten).
"""

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ObjectCategory(enum.StrEnum):
    ADDRESS = "address"
    ADDRESS_GROUP = "address_group"
    VIP = "vip"
    IPPOOL = "ippool"
    SERVICE = "service"
    SERVICE_GROUP = "service_group"
    # internes Ziel einer VIP (Mapped-IP bzw. Real Server) ohne eigenes Adressobjekt auf der
    # FortiGate - von Automato abgeleitet (0.12.0), damit Suche und Objektliste es finden
    VIP_TARGET = "vip_target"


# Kategorien, die als Adress-Endpunkt in Regeln vorkommen bzw. als Service
ADDRESS_CATEGORIES = (ObjectCategory.ADDRESS, ObjectCategory.ADDRESS_GROUP, ObjectCategory.VIP)
SERVICE_CATEGORIES = (ObjectCategory.SERVICE, ObjectCategory.SERVICE_GROUP)
GROUP_CATEGORIES = (ObjectCategory.ADDRESS_GROUP, ObjectCategory.SERVICE_GROUP)


class DeviceObject(Base):
    __tablename__ = "device_object"
    # IPv4- und IPv6-Objekte (FortiGate address/address6) duerfen gleich heissen - es sind
    # verschiedene Objekte; ebenso gleiche Namen in zwei VDOMs (ADR 0019). Eindeutig je
    # Quellsystem/VDOM/Kategorie/Familie/Name
    __table_args__ = (
        UniqueConstraint(
            "source_system_id",
            "vdom",
            "category",
            "family",
            "name",
            name="uq_device_object_source_vdom_cat_fam_name",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    source_system_id: Mapped[int] = mapped_column(ForeignKey("source_system.id"), index=True)
    category: Mapped[ObjectCategory] = mapped_column(
        Enum(ObjectCategory, native_enum=False, length=16)
    )
    name: Mapped[str] = mapped_column(String(300))
    obj_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    family: Mapped[str | None] = mapped_column(String(4), nullable=True)  # v4 | v6
    vdom: Mapped[str | None] = mapped_column(String(64), nullable=True)  # FortiGate-VDOM
    values: Mapped[list | None] = mapped_column(JSON, nullable=True)  # Werte / Service-Tokens
    members: Mapped[list | None] = mapped_column(JSON, nullable=True)  # Gruppen: Mitglieder
    fqdn: Mapped[str | None] = mapped_column(String(300), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    color: Mapped[int | None] = mapped_column(Integer, nullable=True)
    interface: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # weitere Attribute wie auf dem Geraet (Services: Protokoll, Ports, ICMP, Timer; ACI-Filter)
    attrs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    network_object_id: Mapped[int | None] = mapped_column(
        ForeignKey("network_object.id", ondelete="SET NULL"), nullable=True, index=True
    )
    collector_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("collector_run.id"), nullable=True
    )
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
