"""Parallele Firewalls: abgelehnte Verbund-Vorschlaege (0.19.0, ADR 0020).

Ein Paar Firewalls, das der Admin als „nicht parallel“ markiert hat (Vorschlag abgelehnt bzw.
Verbund aufgeloest), wird nicht mehr vorgeschlagen. Ein spaeter von Hand gesetzter Verbund
hebt die Ablehnung fuer seine Firewalls auf.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ParallelRejection(Base):
    __tablename__ = "parallel_rejection"
    __table_args__ = (UniqueConstraint("system_a_id", "system_b_id", name="uq_parallel_rejection"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    # Paar mit system_a_id < system_b_id
    system_a_id: Mapped[int] = mapped_column(
        ForeignKey("source_system.id", ondelete="CASCADE"), index=True
    )
    system_b_id: Mapped[int] = mapped_column(
        ForeignKey("source_system.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
