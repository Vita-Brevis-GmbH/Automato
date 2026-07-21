"""Quellsysteme, von denen Collectors Regeln lesen (FortiGate, Cisco ACI, ...)."""

import enum
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Platform(enum.StrEnum):
    FORTIGATE = "fortigate"
    CISCO_ACI = "cisco_aci"


class SourceSystem(Base):
    __tablename__ = "source_system"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    platform: Mapped[Platform] = mapped_column(Enum(Platform, native_enum=False, length=32))
    base_url: Mapped[str] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
