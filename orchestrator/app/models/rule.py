"""Regel-Kernmodell (ADR 0004).

Eine Tabelle fuer beide Domaenen (FortiGate-Policy | ACI-Contract), unterschieden
per `domain`. Endpunkte sind polymorph (rule_endpoint): fuer Firewalls
Host/Netz/Zone-Objekte, fuer ACI EPGs. Die epg-Referenz am Endpunkt kommt mit
Phase 2 (ACI) hinzu - in Phase 1 werden EPG-Endpunkte ggf. als inline_value gefuehrt.

`rule_object_ref` haelt die Verweise der Regel so, wie sie auf der Firewall stehen
(Objektnamen in Reihenfolge, ADR 0010); die Endpunkte bleiben die aufgeloesten Werte.
"""

import enum
from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Enum, ForeignKey, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.service_object import ServiceObject

# Zuordnung Regel <-> kanonische Services (eine Regel kann mehrere Dienste haben)
rule_service = Table(
    "rule_service",
    Base.metadata,
    Column("rule_id", ForeignKey("rule.id", ondelete="CASCADE"), primary_key=True),
    Column("service_object_id", ForeignKey("service_object.id"), primary_key=True),
)


class RefRole(enum.StrEnum):
    SRC = "src"
    DST = "dst"
    SERVICE = "service"


class RuleDomain(enum.StrEnum):
    FIREWALL = "firewall"
    ACI_CONTRACT = "aci_contract"


class RuleAction(enum.StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class EndpointRole(enum.StrEnum):
    SRC = "src"
    DST = "dst"


class EndpointKind(enum.StrEnum):
    HOST = "host"
    NETWORK = "network"
    EPG = "epg"
    ZONE = "zone"
    ANY = "any"
    GROUP = "group"


class Rule(Base):
    __tablename__ = "rule"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    source_system_id: Mapped[int] = mapped_column(ForeignKey("source_system.id"), index=True)
    domain: Mapped[RuleDomain] = mapped_column(Enum(RuleDomain, native_enum=False, length=16))
    external_id: Mapped[str] = mapped_column(String(200))  # ID im Quellsystem
    name: Mapped[str] = mapped_column(String(300), default="")
    action: Mapped[RuleAction] = mapped_column(Enum(RuleAction, native_enum=False, length=8))
    sequence: Mapped[int | None] = mapped_column(nullable=True)  # Reihenfolge auf dem Geraet
    section_label: Mapped[str | None] = mapped_column(String(100), nullable=True)  # Band (Zone 1-5)
    src_zone_id: Mapped[int | None] = mapped_column(ForeignKey("zone.id"), nullable=True)
    dst_zone_id: Mapped[int | None] = mapped_column(ForeignKey("zone.id"), nullable=True)
    # ACI-Spezifika (nur domain=aci_contract):
    contract_dn: Mapped[str | None] = mapped_column(String(400), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_inter_tenant: Mapped[bool] = mapped_column(Boolean, default=False)
    # Contract-Scope (application-profile|context|tenant|global) -> Applikations-Doku-Kategorie
    aci_scope: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collector_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("collector_run.id"), nullable=True
    )
    # Schutzmechanismen (FortiGate-UTM-Profile an der Policy) fuer die Applikations-Doku
    protections: Mapped[list | None] = mapped_column(JSON, nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    endpoints: Mapped[list["RuleEndpoint"]] = relationship(
        back_populates="rule", cascade="all, delete-orphan"
    )
    services: Mapped[list[ServiceObject]] = relationship(secondary=rule_service)
    refs: Mapped[list["RuleObjectRef"]] = relationship(
        back_populates="rule", cascade="all, delete-orphan", order_by="RuleObjectRef.position"
    )


class RuleEndpoint(Base):
    __tablename__ = "rule_endpoint"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("rule.id", ondelete="CASCADE"), index=True)
    role: Mapped[EndpointRole] = mapped_column(Enum(EndpointRole, native_enum=False, length=4))
    kind: Mapped[EndpointKind] = mapped_column(Enum(EndpointKind, native_enum=False, length=8))
    network_object_id: Mapped[int | None] = mapped_column(
        ForeignKey("network_object.id"), nullable=True
    )
    # ACI (Phase 2): EPG-Endpunkt-Referenz
    epg_id: Mapped[int | None] = mapped_column(ForeignKey("epg.id"), nullable=True)
    inline_value: Mapped[str | None] = mapped_column(String(300), nullable=True)
    matched: Mapped[bool] = mapped_column(Boolean, default=False)

    rule: Mapped[Rule] = relationship(back_populates="endpoints")


class RuleObjectRef(Base):
    """Verweis einer Regel auf ein Firewall-Objekt (per Name, wie auf dem Geraet)."""

    __tablename__ = "rule_object_ref"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("rule.id", ondelete="CASCADE"), index=True)
    role: Mapped[RefRole] = mapped_column(Enum(RefRole, native_enum=False, length=8))
    position: Mapped[int] = mapped_column(default=0)
    name: Mapped[str] = mapped_column(String(300))
    device_object_id: Mapped[int | None] = mapped_column(
        ForeignKey("device_object.id", ondelete="SET NULL"), nullable=True, index=True
    )

    rule: Mapped[Rule] = relationship(back_populates="refs")
