"""Zuordnung grosser Netze aus Regeln von Hand (0.111.0, ADR 0040).

Ein Netz-Objekt einer Regel (z. B. 10.0.0.0/8), das das Routing nicht als Ganzes kennt, landet
sonst bei der naechst kuerzeren Route (oft der Default-Route). Hier steht je Mandant und Netz,
wohin es gehoert: zu einer Zone, ueber eine Anbindung, der Weg nach dem Routing ist richtig,
oder nur die genauer gerouteten Teile zaehlen (`ignore`). Ohne Zeile entscheiden Zonen,
Anbindungen und das Routing (`store/net_assign.py`).
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

NET_ASSIGN_KINDS = ("zone", "exit", "route", "ignore")


class NetAssignment(Base):
    __tablename__ = "net_assignment"
    __table_args__ = (UniqueConstraint("tenant_id", "prefix", name="uq_net_assignment_prefix"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"),
                                           index=True)  # fmt: skip
    prefix: Mapped[str] = mapped_column(String(64))  # CIDR, normalisiert
    kind: Mapped[str] = mapped_column(String(16))  # siehe NET_ASSIGN_KINDS
    zone_id: Mapped[int | None] = mapped_column(
        ForeignKey("logical_zone.id", ondelete="SET NULL"), nullable=True
    )
    exit_id: Mapped[int | None] = mapped_column(
        ForeignKey("topo_exit.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    changed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
