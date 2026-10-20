"""Quellsysteme, von denen Collectors Regeln lesen (FortiGate, Cisco ACI, ...).

Ein Quellsystem gehoert zu genau einem Mandanten (Name je Mandant eindeutig). `tenant_id`
ist nur fuer Altbestaende vor Migration 0015 leer ("nicht zugeordnet").

Geraete-Zugangsdaten (ADR 0009): `agent_id` = Collector-Agent, der das Geraet verwaltet;
`credentials` = verschluesselte Zugangsdaten (app.credentials, nie im Klartext gespeichert
oder ausgegeben) - der Agent bekommt sie beim Abholen eines Laufs. Inhalt je Plattform:
FortiGate `{"token"}`, Cisco ACI `{"username", "password"}` (APIC-Login).
`read_scope` = Lesebereich: bei Cisco ACI die zu lesenden ACI-Tenants (kommagetrennt, leer =
alle ausser infra/mgmt).
"""

import enum
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Platform(enum.StrEnum):
    FORTIGATE = "fortigate"
    CISCO_ACI = "cisco_aci"


class SourceSystem(Base):
    __tablename__ = "source_system"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_source_system_tenant_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenant.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    platform: Mapped[Platform] = mapped_column(Enum(Platform, native_enum=False, length=32))
    base_url: Mapped[str] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(default=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agent.id"), nullable=True, index=True)
    credentials: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet-Token
    verify_tls: Mapped[bool] = mapped_column(default=True, server_default=true())
    read_scope: Mapped[str] = mapped_column(String(500), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
