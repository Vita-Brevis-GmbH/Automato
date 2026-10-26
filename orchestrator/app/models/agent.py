"""Externer Collector-Agent (ADR 0008).

Ein registrierter Agent (z. B. Windows-Station im Kundennetz) holt externe Laeufe per
Pull am Orchestrator ab und liefert Ergebnisse ueber `/internal` zurueck. Authentisiert
sich mit einem Agent-Token (nur dessen SHA-256-Hash wird gespeichert). `tenant_id`
schraenkt ein, welche Laeufe der Agent sieht; None = keine Einschraenkung (nur bewusst).
`version` meldet der Agent bei jedem Aufruf (Paket-Version), `auto_update` erlaubt ihm,
sich ueber `/internal/agent/update` selbst zu aktualisieren (ADR 0011).
"""

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, true
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Agent(Base):
    __tablename__ = "agent"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), index=True)  # sha256(token), hex
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenant.id"), nullable=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true(), default=True
    )
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Selbst-Update (ADR 0011): gemeldete Paket-Version des Agenten, Update erlaubt?
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    auto_update: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true(), default=True
    )
    # Ergebnis der letzten Update-Pruefung des Agenten (Header X-Agent-Update), None = ok
    update_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
