"""Quellsysteme, von denen Collectors Regeln lesen (FortiGate, Cisco ACI, ...).

Ein Quellsystem gehoert zu genau einem Mandanten (Name je Mandant eindeutig). `tenant_id`
ist nur fuer Altbestaende vor Migration 0015 leer ("nicht zugeordnet").

Geraete-Zugangsdaten (ADR 0009): `agent_id` = Collector-Agent, der das Geraet verwaltet;
`credentials` = verschluesselte Zugangsdaten (app.credentials, nie im Klartext gespeichert
oder ausgegeben) - der Agent bekommt sie beim Abholen eines Laufs. Inhalt je Plattform:
FortiGate `{"token"}`, Cisco ACI `{"username", "password"}` (APIC-Login).
`read_scope` = Lesebereich: bei Cisco ACI die zu lesenden ACI-Tenants (kommagetrennt, leer =
alle ausser infra/mgmt).

Referenzquellen (Infoblox, NetBox; ADR 0010): `ref_role` = primary | enrichment,
`reference_run_id` = Lauf, dessen Eintraege (`reference_record`) der aktuelle Stand sind.
NetBox kann zusaetzlich ein Schreib-Token tragen (`write_token` in `credentials`) - nur fuer
den NetBox-Sync des Orchestrators, nie an einen Agenten.
"""

import enum
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Platform(enum.StrEnum):
    FORTIGATE = "fortigate"
    CISCO_ACI = "cisco_aci"
    INFOBLOX = "infoblox"  # Referenzquelle (DDI), ADR 0010
    NETBOX = "netbox"  # Referenzquelle / Anreicherung, ADR 0010


# Referenzquellen (IPAM) - liefern keinen Regeln, sondern den Referenz-Stand
REFERENCE_PLATFORMS = frozenset({Platform.INFOBLOX, Platform.NETBOX})


class RefRole(enum.StrEnum):
    """Rolle einer Referenzquelle je Mandant (Feld-Hoheit, ADR 0010)."""

    PRIMARY = "primary"  # IPAM fuehrend (IP, Prefix, Range, DNS)
    ENRICHMENT = "enrichment"  # Anreicherung (z. B. NetBox neben der DDI)


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
    ref_role: Mapped[str | None] = mapped_column(String(16), nullable=True)  # RefRole
    reference_run_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "collector_run.id",
            ondelete="SET NULL",
            use_alter=True,  # Zyklus source_system <-> collector_run
            name="fk_source_system_reference_run",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
