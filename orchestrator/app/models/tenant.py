"""Mandant (Automato-Tenant) - oberster Scope, traegt die SoT-Bindung (ADR 0004).

Ein Mandant kann mehrere ACI-Security-Tenants umfassen (aci_tenant_names);
"Tenant" in ACI ist nicht dasselbe wie "Mandant" in Automato.
"""

import enum
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class IpamType(enum.StrEnum):
    NETBOX = "netbox"
    INFOBLOX = "infoblox"


class DnsType(enum.StrEnum):
    INFOBLOX = "infoblox"
    ACTIVE_DIRECTORY = "active_directory"


class Tenant(Base):
    __tablename__ = "tenant"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    # Kuerzel/Code der GG (optional) - identifiziert den Mandanten bei VMs/Objekten.
    # Stammdaten, keine Funktionsrelevanz; kundenspezifisch vergeben (ADR 0004-Ergaenzung).
    code: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    ipam_type: Mapped[IpamType] = mapped_column(Enum(IpamType, native_enum=False, length=16))
    dns_type: Mapped[DnsType] = mapped_column(Enum(DnsType, native_enum=False, length=32))
    # ACI-Security-Tenant-Namen, die dieser Mandant umfasst (Model 1)
    aci_tenant_names: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Per-Mandant-Parserregeln fuer die Namenskonvention (siehe app/naming.py)
    naming_convention: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Namensprofil (in-App pflegbare Woerterbuecher) fuer diesen Mandanten; None = SOP-
    # Default-Konvention (keine Funktionsabhaengigkeit von Daten).
    naming_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("naming_profile.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
