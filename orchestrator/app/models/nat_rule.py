"""NAT-/VIP-Regeln (ADR 0004, Phase 1.5).

Eingehende DNAT/Virtual IPs der FortiGate: externe IP (+ Port) -> internes Ziel (+ Port);
Virtual Server (Lastverteilung) mit mehreren Real Servern; SNAT-Pools. Rein
lesend/dokumentierend; wird je Quellsystem bei jedem Collector-Lauf ersetzt (wie `rule`).
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
    # VIP-Typ wie auf der FortiGate: static-nat | server-load-balance (Virtual Server) | ...
    vip_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    family: Mapped[str | None] = mapped_column(String(4), nullable=True)  # v4 | v6
    vdom: Mapped[str | None] = mapped_column(String(64), nullable=True)  # FortiGate-VDOM
    # Virtual Server: Real Server (ip/address, port, status, weight, Health-Check ...);
    # mapped_ip/mapped_port = erster Real Server (fuer Sichten mit einem Ziel)
    targets: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Virtual Server: Server-Typ, Methode, Persistenz, Health-Checks, Zertifikat (Name)
    attrs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    collector_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("collector_run.id"), nullable=True
    )
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
