"""Adressobjekt - kanonische Sicht auf Host/Netz/Range (ADR 0004).

object_class stammt aus der Namenskonvention (Praefix), type aus dem SoT;
weichen sie voneinander ab, entsteht ein name_type_mismatch-Finding.
match_status haelt fest, ob das Objekt im IPAM aufgeloest werden konnte.
"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ObjectClass(enum.StrEnum):
    """Aus der Namenskonvention geparst (H_/N_/VIP_/R_/H-EXT_)."""

    HOST = "host"
    NETWORK = "network"
    VIP = "vip"
    RANGE = "range"
    EXTERNAL = "external"
    UNKNOWN = "unknown"


class ObjectType(enum.StrEnum):
    """Tatsaechlicher Typ laut SoT."""

    HOST = "host"
    NETWORK = "network"
    RANGE = "range"


class Family(enum.StrEnum):
    V4 = "v4"
    V6 = "v6"


class MatchStatus(enum.StrEnum):
    MATCHED = "matched"
    UNMATCHED = "unmatched"
    AMBIGUOUS = "ambiguous"
    EXPECTED_EXTERNAL = "expected_external"  # H-EXT_: nicht im IPAM erwartet
    NOT_APPLICABLE = "not_applicable"  # kein IPAM-Typ (geography/mac/dynamic)


class SourceOfRecord(enum.StrEnum):
    NETBOX = "netbox"
    INFOBLOX = "infoblox"
    DEVICE = "device"
    AUTOMATO = "automato"


class NetworkObject(Base):
    __tablename__ = "network_object"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    object_class: Mapped[ObjectClass] = mapped_column(
        Enum(ObjectClass, native_enum=False, length=16), default=ObjectClass.UNKNOWN
    )
    type: Mapped[ObjectType | None] = mapped_column(
        Enum(ObjectType, native_enum=False, length=16), nullable=True
    )
    value: Mapped[str | None] = mapped_column(String(200), nullable=True)  # IP/CIDR/Range
    family: Mapped[Family | None] = mapped_column(
        Enum(Family, native_enum=False, length=4), nullable=True
    )
    fqdn: Mapped[str | None] = mapped_column(String(300), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("zone.id"), nullable=True, index=True)
    # Standort-Verortung (ADR 0004-Ergaenzung): ordnet ein Objekt/System einem Standort zu.
    site_id: Mapped[int | None] = mapped_column(ForeignKey("site.id"), nullable=True, index=True)
    vlan: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # FortiGate-Farbcode als Herkunfts-/Typ-Signal (optional, numerisch; ADR 0006)
    color: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_of_record: Mapped[SourceOfRecord | None] = mapped_column(
        Enum(SourceOfRecord, native_enum=False, length=16), nullable=True
    )
    match_status: Mapped[MatchStatus] = mapped_column(
        Enum(MatchStatus, native_enum=False, length=20), default=MatchStatus.UNMATCHED
    )
    # Abgleich (ADR 0010): bester Treffer im fuehrenden IPAM (genauer Eintrag, sonst Netz),
    # das umschliessende Netz und der Treffer in der Anreicherung (z. B. NetBox neben DDI)
    reference_id: Mapped[int | None] = mapped_column(
        ForeignKey("reference_record.id", ondelete="SET NULL"), nullable=True
    )
    reference_prefix_id: Mapped[int | None] = mapped_column(
        ForeignKey("reference_record.id", ondelete="SET NULL"), nullable=True
    )
    enrichment_id: Mapped[int | None] = mapped_column(
        ForeignKey("reference_record.id", ondelete="SET NULL"), nullable=True
    )
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
