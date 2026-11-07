"""Prüfungen der Findings je Mandant ein- bzw. ausgeschaltet (0.110.0, ADR 0039).

Nur Abweichungen von der Vorgabe stehen hier (ohne Zeile: die Prüfung ist an). Die Prüfungen
selbst stehen im Code (`app/checks/<modul>/<prüfung>.py`), nicht in der Datenbank.
"""

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TenantCheck(Base):
    __tablename__ = "tenant_check"

    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), primary_key=True
    )
    check: Mapped[str] = mapped_column(String(48), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean)
    changed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
