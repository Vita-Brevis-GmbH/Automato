"""Kunden in Automato, die Automato fuer das Cockpit angelegt hat (1.14.0, ADR 0058).

`PUT /freigaben` mit `zuordnung.kunde_anlegen` legt im Mandanten des Portal-Kunden einen Kunden
(ADR 0018) mit dessen Namen an und haelt hier fest, fuer welchen Portal-Kunden (Kurzname) und in
welchem Mandanten. So bleibt das Anlegen idempotent (ein Kunde je Portal-Kunde und Mandant), und
der Abbau weiss spaeter, welche Kunden vom Cockpit stammen - auch nachdem der Portal-Kunde selbst
entfernt ist (darum der Kurzname statt eines Verweises auf `portal_customer`). Wird der Kunde in
Automato geloescht, faellt der Eintrag mit (`ondelete=CASCADE`); das naechste `kunde_anlegen`
legt dann wieder einen an.
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
