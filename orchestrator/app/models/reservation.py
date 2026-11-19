"""Reservierungen von VLAN- und AS-Nummern (Automato 1.19.0, ADR 0066).

Anwender 2026-10-30 (Roadmap Version 2 Nr. 10) und 2026-11-17 (Prioritaet 6, „Reservationen auch
ins Netbox“): Elemente festlegen und reservieren - erst VLANs und BGP-AS-Nummern.

- `Reservation` (Laufzeitdaten je Mandant, in der App gepflegt): Art (`vlan` | `asn`), Nummer
  bzw. Bereich (`low`..`high`), bei VLANs der Standort (ohne = alle Standorte, wie die
  VLAN-Ansicht je Standort seit 1.9.0) und wahlweise die VLAN-Gruppe in NetBox (Slug), Zweck,
  wer und wann. `used_before`: die Nummern, die beim Reservieren schon gelesen in Gebrauch waren
  - danach unterscheidet die Pruefung „jetzt belegt“ (Hinweis) von „Konflikt“.
- Der Standort steht ohne Fremdschluessel (wie `stp_target`): ein geloeschter Standort bleibt
  sichtbar stehen („Standort fehlt“) statt die Reservierung still zu loeschen bzw. auf alle
  Standorte auszuweiten.

Nur Daten der Plattform; nach NetBox nur ueber den NetBox-Sync (Vorschau -> Uebernehmen).
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

RESERVATION_KINDS = ("vlan", "asn")


class Reservation(Base):
    __tablename__ = "reservation"
    __table_args__ = (Index("ix_reservation_tenant_kind", "tenant_id", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(8))  # vlan | asn
    low: Mapped[int] = mapped_column(BigInteger)
    high: Mapped[int] = mapped_column(BigInteger)
    site_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # nur VLAN
    vlan_group: Mapped[str | None] = mapped_column(String(100), nullable=True)  # Slug in NetBox
    purpose: Mapped[str] = mapped_column(String(500))
    used_before: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    changed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
