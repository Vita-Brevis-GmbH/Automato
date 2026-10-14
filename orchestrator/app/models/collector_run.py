"""Collector-Laeufe: Betriebs- und Audit-Sicht auf jede Regel-Erfassung."""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RunStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class CollectorRun(Base):
    __tablename__ = "collector_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    platform: Mapped[str] = mapped_column(String(32), index=True)
    # Ziel der Erfassung; noetig, um Regeln dem Mandanten/Quellsystem zuzuordnen (M7).
    # Nullable: ohne diese Angaben wird nur gezaehlt/auditiert, nicht persistiert.
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenant.id"), nullable=True)
    source_system_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_system.id"), nullable=True
    )
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, native_enum=False, length=16), default=RunStatus.QUEUED
    )
    # inprocess (zentraler Worker) | external (externer Agent holt per Pull, ADR 0008)
    execution: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="inprocess", default="inprocess"
    )
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rule_count: Mapped[int | None] = mapped_column(nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
