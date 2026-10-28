"""Module je Mandant ein- bzw. ausgeschaltet (0.107.0, ADR 0036).

Nur Abweichungen von der Vorgabe stehen hier (ohne Zeile: das Modul ist an). Die Module
selbst stehen im Code (`app/modules.py`), nicht in der Datenbank.
"""

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TenantModule(Base):
    __tablename__ = "tenant_module"

    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), primary_key=True
    )
    module: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean)
    changed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
