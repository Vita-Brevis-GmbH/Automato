"""NAT-/VIP-Regeln (ADR 0004, Phase 1.5).

Fuer UC1 vorerst eingehende DNAT/Virtual IPs der FortiGate: externe IP (+ Port)
-> internes Ziel (+ Port). Rein lesend/dokumentierend; wird je Quellsystem bei
jedem Collector-Lauf ersetzt (wie `rule`).
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class NatRule(Base):
    __tablename__ = "nat_rule"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    source_system_id: Mapped[int] = mapped_column(ForeignKey("source_system.id"), index=True)
    name: Mapped[str] = mapped_column(String(300))  # VIP-/Pool-Name
    external_id: Mapped[str] = mapped_column(String(200))  # Ref im Quellsystem
    # dnat_vip (eingehend, extip->mappedip) | snat_pool (ausgehend, externe Quell-IP)
    nat_type: Mapped[str] = mapped_column(String(16), default="dnat_vip")
    ext_ip: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ext_port: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mapped_ip: Mapped[str | None] = mapped_column(String(200), nullable=True)
    mapped_port: Mapped[str | None] = mapped_column(String(64), nullable=True)
    protocol: Mapped[str | None] = mapped_column(String(16), nullable=True)
    portforward: Mapped[bool] = mapped_column(Boolean, default=False)
    collector_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("collector_run.id"), nullable=True
    )
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
