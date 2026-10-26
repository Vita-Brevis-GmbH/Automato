"""Lueckenloses Audit-Log: wer hat wann was ausgeloest, mit welchem Ergebnis (SECURITY.md)."""

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    actor: Mapped[str] = mapped_column(String(100))  # Benutzer oder Service-Identitaet
    action: Mapped[str] = mapped_column(String(100), index=True)  # z. B. collector_run.queued
    subject: Mapped[str] = mapped_column(Text)  # betroffenes Objekt, z. B. run-id
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
