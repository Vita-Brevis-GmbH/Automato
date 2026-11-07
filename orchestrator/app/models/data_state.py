"""Datenstand nach Inhalt (0.141.0, ADR 0049).

Anwender 2026-11-06: „Die Berechnungen nur anstossen, wenn was geändert hat, also nur die Wege
berechnen, welche von der geänderten Firewall Regel betroffen sind.“

- `DataState` je Quellsystem: Abdruck des zuletzt eingespielten Inhalts je Teil (Regeln, Objekte,
  NAT, Routing, Inventar …), je Regel und je Eintrag (Objekt, VIP, EPG, VM) - zum Vergleich mit dem
  naechsten Lauf (`store/data_state.py`).
- `DataChange` je Mandant: was ein Lauf wirklich geaendert hat (Teile, Regeln, Adressen, Geraete,
  EPGs), fortlaufend gezaehlt (`seq`). Ein Lauf ohne Aenderung legt keine Zeile an - der Stand der
  Doku bleibt gueltig. Abgeleitete Daten, nur lesend gegenueber den Systemen.
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DataState(Base):
    __tablename__ = "data_state"

    source_system_id: Mapped[int] = mapped_column(
        ForeignKey("source_system.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    parts: Mapped[dict] = mapped_column(JSON, default=dict)  # Teil -> Abdruck
    rules: Mapped[dict] = mapped_column(JSON, default=dict)  # Regel-ID -> [Abdruck, Vorgaenger]
    items: Mapped[dict] = mapped_column(JSON, default=dict)  # Teil -> Schluessel -> [Abdruck, IPs]
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class DataChange(Base):
    __tablename__ = "data_change"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer, index=True)  # je Mandant fortlaufend
    source_system_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    parts: Mapped[list] = mapped_column(JSON, default=list)  # geaenderte Teile
    all: Mapped[bool] = mapped_column(Boolean, default=False)  # betrifft jede Applikation
    net: Mapped[bool] = mapped_column(Boolean, default=False)  # Routing bzw. Inventar geaendert
    rules: Mapped[list | None] = mapped_column(JSON, nullable=True)  # Regel-IDs
    devices: Mapped[list | None] = mapped_column(JSON, nullable=True)  # Quellsysteme
    ips: Mapped[list | None] = mapped_column(JSON, nullable=True)  # ["4:lo-hi", …] hex
    epgs: Mapped[list | None] = mapped_column(JSON, nullable=True)  # DN
