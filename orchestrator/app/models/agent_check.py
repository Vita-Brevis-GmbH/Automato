"""Verbindung eines Agent-Geräts prüfen (1.7.0, ADR 0008/0009).

Anwender 2026-11-11: „Wenn ich den internen Worker verwende, kann ich die Verbindung prüfen, beim
Collector-Agent nicht.“ Ein Gerät hinter einem Agenten erreicht nur der Agent - der Orchestrator
legt deshalb einen Prüfauftrag an, den der Agent beim nächsten Abruf (`next-run`) mitbekommt,
read-only ausführt (Name, Verbindung, Anmeldung) und zurückmeldet. Nur der jüngste Stand je
Gerät zählt; alte Aufträge verfallen (`store/agent_checks.py`).
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AgentCheck(Base):
    __tablename__ = "agent_check"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id", ondelete="CASCADE"), index=True)
    source_system_id: Mapped[int] = mapped_column(
        ForeignKey("source_system.id", ondelete="CASCADE"), index=True
    )
    # queued -> running (vom Agenten abgeholt) -> done; expired = Agent hat nicht geantwortet
    status: Mapped[str] = mapped_column(String(16), default="queued")
    requested_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # {"steps": [{"step", "ok", "detail"}], "hint": str|None} - nie Zugangsdaten
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
