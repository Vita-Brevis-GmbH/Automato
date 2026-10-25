"""Logische Zonen (0.51.0, ADR 0024): in Automato gepflegt, nicht gelesen.

Eine **logische Zone** fasst Netze zusammen, die gleich behandelt werden („Server Kunde A“,
„DMZ“, „Internet“, „Management“). Wie ein Kunde (ADR 0018) wird sie durch **Selektoren**
beschrieben statt durch Listen - dieselben Arten wie beim Kunden (ACI-Tenant/VRF/AP/EPG/BD,
VDOM/Zone/Interface, Netz, Kuerzel, IPAM-Merkmal) und dazu `exit` (die Netze hinter einer
Anbindung, z. B. Internet). Welche Netze dazugehoeren, ist abgeleitet (`store/zones.py`):
neue EPGs oder Interfaces gehoeren ohne Pflege dazu.

Die Firewall-Zonen der FortiGate (`zone`, gelesen) sind etwas anderes: sie gelten je Geraet
und VDOM; eine logische Zone gilt in der ganzen Umgebung.

Je Zonenpaar haelt `zone_policy` die **Absicht** (erlaubt, eingeschraenkt, gesperrt) fuer die
Zonenmatrix; eine erlaubende Regel zwischen gesperrten Zonen ist ein Befund.

Ein Kunde besitzt Zonen (`customer_id`); geteilte Zonen (Internet, Shared Services,
Management) gehoeren keinem. Die Sicht der Kunden auf ihre Zonen folgt mit 0.53.0.

Seit 0.54.0 schlaegt Automato Zonen aus dem Gelesenen vor (`store/zone_detect.py`);
ausgeblendete Vorschlaege merkt sich `zone_suggestion_dismissal`.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.customer import SELECTOR_KINDS

# Art der Zone - Gliederung in Matrix und Plan, keine Funktion. Seit 0.56.0 die Ebenen des
# Zonenmodells (Datacenter, Edge, Transfer, Core, Distribution, Access, Public = extern) - in
# dieser Reihenfolge von links nach rechts im Zonenplan; die bisherigen Arten bleiben gueltig.
ZONE_KINDS = ("dc", "edge", "transfer", "core", "distribution", "access", "external", "internal",
              "dmz", "management", "shared")  # fmt: skip
# Selektoren wie beim Kunden, dazu die Netze hinter einer Anbindung (`topo_exit`, Name)
ZONE_SELECTOR_KINDS = (*SELECTOR_KINDS, "exit")
# Absicht je Zonenpaar: erlaubt, eingeschraenkt (nur bestimmte Dienste), gesperrt
INTENTS = ("allow", "limited", "deny")


class LogicalZone(Base):
    __tablename__ = "logical_zone"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_logical_zone_tenant_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str | None] = mapped_column(String(50), nullable=True)  # Kurzname im Plan
    kind: Mapped[str] = mapped_column(String(16), default="internal")  # siehe ZONE_KINDS
    # Kunde, dem die Zone gehoert; leer = geteilt bzw. Infrastruktur
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customer.id", ondelete="SET NULL"), nullable=True, index=True
    )
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)  # #rrggbb im Plan
    position: Mapped[int] = mapped_column(Integer, default=0)  # Reihenfolge in Matrix und Plan
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    selectors: Mapped[list["LogicalZoneSelector"]] = relationship(
        back_populates="zone",
        cascade="all, delete-orphan",
        order_by="LogicalZoneSelector.id",
    )


class LogicalZoneSelector(Base):
    __tablename__ = "logical_zone_selector"

    id: Mapped[int] = mapped_column(primary_key=True)
    zone_id: Mapped[int] = mapped_column(
        ForeignKey("logical_zone.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))  # siehe ZONE_SELECTOR_KINDS
    value: Mapped[str] = mapped_column(String(400))
    # FortiGate-Selektoren: nur dieses Quellsystem (leer = alle)
    source_system_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_system.id", ondelete="CASCADE"), nullable=True
    )
    exclude: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    zone: Mapped[LogicalZone] = relationship(back_populates="selectors")


class ZonePolicy(Base):
    """Absicht fuer den Verkehr von Zone `src` zu Zone `dst` (Zonenmatrix)."""

    __tablename__ = "zone_policy"
    __table_args__ = (UniqueConstraint("src_zone_id", "dst_zone_id", name="uq_zone_policy_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    src_zone_id: Mapped[int] = mapped_column(
        ForeignKey("logical_zone.id", ondelete="CASCADE"), index=True
    )
    dst_zone_id: Mapped[int] = mapped_column(
        ForeignKey("logical_zone.id", ondelete="CASCADE"), index=True
    )
    intent: Mapped[str] = mapped_column(String(8))  # siehe INTENTS
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ZoneSuggestionDismissal(Base):
    """Zonen-Vorschlag, der nicht mehr vorgeschlagen wird (0.54.0, „ausblenden“): Schluessel
    = normalisierter Name des Vorschlags (`store/zone_detect.key_of`). Rueckgaengig mit
    „wieder vorschlagen“."""

    __tablename__ = "zone_suggestion_dismissal"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_zone_suggestion_dismissal"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    key: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ZonePlanVersion(Base):
    """Gespeicherter Stand des Zonenplans (0.52.0, ADR 0024): Zonen mit Selektoren, Plan und
    Matrix samt Absichten so, wie sie beim Speichern abgeleitet waren - zum Nachlesen,
    Vergleichen und Exportieren. `fingerprint` fasst das Design zusammen (Zonen, Absichten,
    Zonen an den Knoten, Netze ohne Zone; ohne Regel-Zahlen), damit „unveraendert“ erkennbar
    ist. Nur Daten der Plattform; geloescht wird nur der Stand, die Nummer bleibt vergeben."""

    __tablename__ = "zone_plan_version"
    __table_args__ = (UniqueConstraint("tenant_id", "number", name="uq_zone_plan_version_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)  # je Mandant fortlaufend
    label: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    data: Mapped[dict] = mapped_column(JSON)
    # geloescht (Admin): der Stand faellt weg, die Nummer bleibt vergeben
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
