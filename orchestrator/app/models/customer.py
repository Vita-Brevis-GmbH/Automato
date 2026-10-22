"""Kunde als Ausschnitt einer Umgebung (ADR 0018, Schritt 1).

Die Umgebung ist der heutige Mandant (`tenant`): ihre Quellsysteme werden vollstaendig
gelesen. Ein **Kunde** ist ein Ausschnitt davon, beschrieben durch **Selektoren** („gehoert
dazu, wenn …“) statt Listen:

- ACI: `aci_tenant`, `aci_vrf` (`Tenant:VRF` bzw. VRF), `aci_ap` (`Tenant/AP`), `aci_epg`
  (DN bzw. `Tenant/AP/EPG`), `aci_bd` (`Tenant/BD`)
- FortiGate: `fw_vdom`, `fw_zone`, `fw_interface` (optional je Quellsystem)
- Netze: `prefix` (CIDR)
- `code`: Kuerzel aus der Namenskonvention (`TE_<kuerzel>`, Objekt- und Servernamen)

Jeder Selektor schliesst ein oder aus (`exclude`). Ein Kunde mit `shared` ist der geteilte
Teil der Umgebung (z. B. Tenant `common`, Internet-Rand): er gilt fuer alle Kunden.

Die **Zugehoerigkeit** (`customer_member`) ist abgeleiteter Zustand wie Regelindex und
Findings: nach jedem Lauf und bei geaenderten Selektoren neu berechnet, nie von Hand gepflegt
(`store/customers.py`). `ref_id` zeigt je Art auf EPG, Regel, Werte-Objekt bzw. VM - ohne
Fremdschluessel, weil diese je Lauf ersetzt werden; Netze stehen als `value` (CIDR).
"""

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

SELECTOR_KINDS = (
    "aci_tenant",
    "aci_vrf",
    "aci_ap",
    "aci_epg",
    "aci_bd",
    "fw_vdom",
    "fw_zone",
    "fw_interface",
    "prefix",
    "code",
)
MEMBER_KINDS = ("epg", "net", "object", "rule", "vm")


class Customer(Base):
    __tablename__ = "customer"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_customer_tenant_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    # Kuerzel aus der Namenskonvention (TE_<code>, VRF_<code>, Server- und Objektnamen)
    code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # geteilter Teil der Umgebung: gilt fuer alle Kunden (Tenant common, Internet-Rand …)
    shared: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    # Zeitpunkt der letzten Ableitung der Zugehoerigkeit
    derived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    selectors: Mapped[list["CustomerSelector"]] = relationship(
        back_populates="customer",
        cascade="all, delete-orphan",
        order_by="CustomerSelector.id",
    )


class CustomerSelector(Base):
    __tablename__ = "customer_selector"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customer.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))  # siehe SELECTOR_KINDS
    value: Mapped[str] = mapped_column(String(400))
    # FortiGate-Selektoren: nur dieses Quellsystem (leer = alle)
    source_system_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_system.id", ondelete="CASCADE"), nullable=True
    )
    exclude: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    customer: Mapped[Customer] = relationship(back_populates="selectors")


class CustomerMember(Base):
    __tablename__ = "customer_member"
    __table_args__ = (Index("ix_customer_member_lookup", "customer_id", "kind", "ref_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customer.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(8))  # epg | net | object | rule | vm
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    value: Mapped[str | None] = mapped_column(String(64), nullable=True)  # Netz (CIDR)
    via: Mapped[str | None] = mapped_column(String(200), nullable=True)  # warum (Selektor)
