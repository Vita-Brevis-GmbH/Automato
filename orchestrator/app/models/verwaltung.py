"""Kunden in Automato, die Automato fuer das Cockpit angelegt hat (1.14.0, ADR 0058).

`PUT /freigaben` mit `zuordnung.kunde_anlegen` legt im Mandanten des Portal-Kunden einen Kunden
(ADR 0018) mit dessen Namen an und haelt hier fest, fuer welchen Portal-Kunden (Kurzname) und in
welchem Mandanten. So bleibt das Anlegen idempotent (ein Kunde je Portal-Kunde und Mandant), und
der Abbau weiss spaeter, welche Kunden vom Cockpit stammen - auch nachdem der Portal-Kunde selbst
entfernt ist (darum der Kurzname statt eines Verweises auf `portal_customer`). Wird der Kunde in
Automato geloescht, faellt der Eintrag mit (`ondelete=CASCADE`); das naechste `kunde_anlegen`
legt dann wieder einen an.

Seit 1.19.2 (ADR 0058 Nachtrag, Anwender 2026-11-18) dazu `CockpitTenant`: der Mandant, den
Automato fuer einen Portal-Kunden aus dem Cockpit angelegt hat (`zuordnung.mandant_anlegen`) -
einer je Portal-Kunde (Kurzname), damit ein zweites `PUT /freigaben` keinen weiteren anlegt. Wird
der Mandant in Automato geloescht, faellt der Eintrag mit; das naechste `mandant_anlegen` legt dann
wieder einen an (Soll-Stand des Cockpits).
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CockpitCustomer(Base):
    __tablename__ = "cockpit_customer"
    __table_args__ = (
        UniqueConstraint("short_name", "tenant_id", name="uq_cockpit_customer_short_tenant"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Kurzname des Portal-Kunden (wie `portal_customer.short_name`)
    short_name: Mapped[str] = mapped_column(String(42), index=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customer.id", ondelete="CASCADE"), unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class CockpitTenant(Base):
    """Mandant, den Automato fuer einen Portal-Kunden aus dem Cockpit angelegt hat (1.19.2)."""

    __tablename__ = "cockpit_tenant"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Kurzname des Portal-Kunden (wie `portal_customer.short_name`) - einer je Portal-Kunde
    short_name: Mapped[str] = mapped_column(String(42), unique=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
