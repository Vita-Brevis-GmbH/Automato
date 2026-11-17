"""Spanning Tree der Switches (Automato 1.19.0, Anwender 2026-11-17 Prioritaet 5).

- `StpDevice` je Switch eines Quellsystems: Lesestatus (gelesen, aus, Plattform nicht
  unterstuetzt, nicht lesbar), Modus (PVST/Rapid-PVST/MST), MST-Region und die err-disabled
  Ports mit Grund - je Lauf ersetzt.
- `StpInstance` je Switch und VLAN bzw. MST-Instanz: Root (Prioritaet, MAC, Kosten, Root-Port),
  eigene Bridge, Topologieaenderungen (Zaehler, Zeitpunkt, Port) mit dem Verlauf der Zaehler der
  letzten Laeufe (`tc_history`, fuer „viele Aenderungen je Tag“) und die Ports als JSON - je Lauf
  ersetzt, der Verlauf wird mitgenommen.
- `StpTarget` (Laufzeitdaten je Mandant, in der App gepflegt): Soll-Root je VLAN-Bereich bzw.
  Standort; ohne Eintrag meldet die Pruefung nur, wer Root ist.
- `StpSetting` je Mandant: Schwelle der Topologieaenderungen je Tag (ohne Zeile die Vorgabe im
  Code, `store/stp.TC_PER_DAY`).

Nur lesend gegenueber den Geraeten; kein Schreibweg.
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class _StpRow:
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    source_system_id: Mapped[int] = mapped_column(
        ForeignKey("source_system.id", ondelete="CASCADE"), index=True
    )
    device: Mapped[str] = mapped_column(String(200))
    read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class StpDevice(_StpRow, Base):
    __tablename__ = "stp_device"

    status: Mapped[str] = mapped_column(String(16))  # read | off | unsupported | unreadable
    mode: Mapped[str | None] = mapped_column(String(32), nullable=True)  # pvst | rapid-pvst | mst
    region: Mapped[str | None] = mapped_column(String(200), nullable=True)
    revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    errdisabled: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{port, reason, vlans}]
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class StpInstance(_StpRow, Base):
    __tablename__ = "stp_instance"
    __table_args__ = (Index("ix_stp_instance_tenant_vlan", "tenant_id", "vlan"),)

    instance: Mapped[str] = mapped_column(String(32))  # VLAN0010 | MST1
    vlan: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mst: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vlans: Mapped[str | None] = mapped_column(Text, nullable=True)  # MST: VLANs der Instanz
    protocol: Mapped[str | None] = mapped_column(String(16), nullable=True)  # ieee | rstp | mstp
    root_priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    root_mac: Mapped[str | None] = mapped_column(String(17), nullable=True)
    root_cost: Mapped[int | None] = mapped_column(Integer, nullable=True)
    root_port: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_root: Mapped[bool] = mapped_column(Boolean, default=False)
    bridge_priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bridge_mac: Mapped[str | None] = mapped_column(String(17), nullable=True)
    tc_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tc_last_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tc_from: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tc_history: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [[ISO-Zeit, Zaehler]]
    ports: Mapped[list | None] = mapped_column(JSON, nullable=True)


class StpTarget(Base):
    """Soll-Root je VLAN-Bereich bzw. Standort (Laufzeitdaten, in der App gepflegt)."""

    __tablename__ = "stp_target"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    # Standort der Switches (Standort ihres Quellsystems); ohne = alle Standorte. Ohne
    # Fremdschluessel: ein geloeschter Standort bleibt sichtbar stehen („Standort fehlt“)
    site_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vlans: Mapped[str | None] = mapped_column(String(500), nullable=True)  # `10,20-30`; ohne = alle
    root: Mapped[str] = mapped_column(String(200))  # Geraet (Name wie gelesen)
    backup: Mapped[str | None] = mapped_column(String(200), nullable=True)  # Ersatz-Root
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    changed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class StpSetting(Base):
    """Einstellungen des Spanning Tree je Mandant (ohne Zeile: Vorgaben im Code)."""

    __tablename__ = "stp_setting"

    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), primary_key=True
    )
    tc_per_day: Mapped[int] = mapped_column(Integer)
    changed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


STP_MODELS = (StpDevice, StpInstance)
