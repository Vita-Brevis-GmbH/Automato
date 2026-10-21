"""NetBox-Sync: Vorschau (Plan) und Uebernahme (ADR 0010).

Ein Plan haelt fest, was der Sync in der NetBox eines Mandanten anlegen, aendern oder
loeschen wuerde (`items`), dazu Kennzahlen (`summary`) und einen Fingerabdruck der Aktionen.
Uebernommen wird nur ein fertiger Plan per Klick (admin); dabei wird der Plan neu berechnet
und muss denselben Fingerabdruck haben - sonst hat sich NetBox oder Automato seit der
Vorschau geaendert und es braucht eine neue Vorschau.
"""

import enum
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SyncStatus(enum.StrEnum):
    PLANNING = "planning"  # Vorschau wird berechnet
    READY = "ready"  # Vorschau fertig, kann uebernommen werden
    APPLYING = "applying"
    APPLIED = "applied"
    FAILED = "failed"
    SUPERSEDED = "superseded"  # neuere Vorschau vorhanden


class SyncPlan(Base):
    __tablename__ = "sync_plan"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    source_system_id: Mapped[int] = mapped_column(
        ForeignKey("source_system.id", ondelete="CASCADE"), index=True
    )  # Ziel-NetBox
    status: Mapped[str] = mapped_column(String(16), default=SyncStatus.PLANNING.value)
    options: Mapped[dict] = mapped_column(JSON, default=dict)  # sections, allow_delete
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # je Art: Aktion -> Anzahl
    items: Mapped[list | None] = mapped_column(JSON, nullable=True)
    warnings: Mapped[list | None] = mapped_column(JSON, nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # Uebernahme: Anzahlen
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(200))
    applied_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Uebernahme begonnen / letztes Lebenszeichen (je Objektart) - haengende Plaene erkennen
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
