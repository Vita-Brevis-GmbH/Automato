"""Kabelnummer je Kabel bzw. Port-Paar, in Automato erfasst (1.19.0, ADR 0031 Nachtrag 1.19.0).

Anwender 2026-11-17: „Kabelnummern können auch in Automato erfasst werden, beides möglich“ - neben
dem Label des Kabels in NetBox (gelesen ueber die Referenzquelle, `reference_record` Art `cable`).
Laufzeitdaten je Mandant, in der Oberflaeche gepflegt (kein CSV): eine Erfassung je Port-Paar.

- `pair` = beide Enden normalisiert und sortiert (`<geraet>|<port>~<geraet>|<port>`, Schluessel wie
  die Verbindungen der Verkabelung, `store/cabling.port_key`/`device_key`) - eindeutig je Mandant;
- `a_*`/`b_*` = die Enden so, wie sie erfasst wurden (Anzeige, NetBox-Sync);
- der NetBox-Sync schreibt `label` als Label des Kabels (Vorschau → Übernehmen, je Kabel
  bestaetigt).
Nicht je Lauf ersetzt; geloescht mit dem Mandanten.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CableLabel(Base):
    __tablename__ = "cable_label"
    __table_args__ = (UniqueConstraint("tenant_id", "pair", name="uq_cable_label_tenant_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    pair: Mapped[str] = mapped_column(String(500))
    a_device: Mapped[str] = mapped_column(String(200))
    a_port: Mapped[str] = mapped_column(String(200))
    b_device: Mapped[str] = mapped_column(String(200))
    b_port: Mapped[str] = mapped_column(String(200))
    label: Mapped[str] = mapped_column(String(100))  # NetBox: Label eines Kabels max. 100
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    changed_by: Mapped[str] = mapped_column(String(200))
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
