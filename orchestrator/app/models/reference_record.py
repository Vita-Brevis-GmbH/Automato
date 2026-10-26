"""Referenz-Ebene: Stand der Referenzquellen (Infoblox DDI, NetBox), ADR 0010.

Je Lauf einer Referenzquelle komplett gelesen (Teil-Upload) und dem Lauf zugeordnet; der
aktuelle Stand einer Quelle ist der Lauf in `source_system.reference_run_id`. Aeltere Laeufe
werden beim Abschluss geloescht, ein fehlgeschlagener Lauf verwirft seine Teil-Uploads.
"""

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ReferenceRecordRow(Base):
    __tablename__ = "reference_record"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    source_system_id: Mapped[int] = mapped_column(ForeignKey("source_system.id"), index=True)
    collector_run_id: Mapped[str] = mapped_column(
        ForeignKey("collector_run.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))  # sot_gateway.models.ReferenceKind
    value: Mapped[str] = mapped_column(String(200), index=True)
    prefix_len: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vrf: Mapped[str | None] = mapped_column(String(200), nullable=True)
    name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    dns_name: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    vlan_vid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vlan_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    site: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tenant_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    attrs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ref_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
