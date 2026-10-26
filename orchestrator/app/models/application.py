"""Applikation (ADR 0012): fachliche Klammer ueber Systeme eines Mandanten.

Eine Applikation fasst Application Profiles (ACI), einzelne EPGs und Server (Host-Objekte
der Firewall) zusammen. Mitglieder sind **stabile Text-Verweise** statt Fremdschluessel -
EPGs und Objekte werden je Lauf ersetzt, die Zuordnung soll das ueberleben:

- `ap`   -> `<ACI-Tenant>/<AP>` (alle EPGs dieses Application Profiles)
- `epg`  -> DN der EPG (`uni/tn-…/ap-…/epg-…`)
- `host` -> Name des Host-Objekts (`H_app01-002-…`)

Ein Mitglied darf in mehreren Applikationen stehen (gemeinsam genutzter Server).

Seit 0.69.0 lassen sich Regeln in den Kategorien der Doku ausblenden (`ApplicationRuleHide`):
je Regel (stabiler Verweis wie bei der direkten Zuordnung) und Kategorie bzw. in allen
Kategorien, immer mit Begruendung, wer und wann.
Kuerzel/Verantwortliche/Kritikalitaet, externer/interner Name und Verwendungszweck
(Dev/Test/Prod, 0.14.0) sind Stammdaten ohne Funktionsrelevanz.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

MEMBER_KINDS = ("ap", "epg", "host")
CRITICALITIES = ("low", "medium", "high", "critical")
PURPOSES = ("dev", "test", "prod")


class Application(Base):
    __tablename__ = "application"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_application_tenant_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    # Name der Applikation gegen aussen (Fachbereich/Kunde) bzw. intern (Betrieb), 0.14.0
    external_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    internal_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    purpose: Mapped[str | None] = mapped_column(String(8), nullable=True)  # dev | test | prod
    # Kuerzel, ueblicherweise die 3-stellige Projektnummer (AP_<nr>, H_…-<nr>-…)
    code: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    owner: Mapped[str | None] = mapped_column(String(200), nullable=True)  # Verantwortliche
    criticality: Mapped[str | None] = mapped_column(String(16), nullable=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("site.id"), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    members: Mapped[list["ApplicationMember"]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="ApplicationMember.id",
    )
    hides: Mapped[list["ApplicationRuleHide"]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="ApplicationRuleHide.id",
    )


class ApplicationMember(Base):
    __tablename__ = "application_member"
    __table_args__ = (
        UniqueConstraint("application_id", "kind", "ref", name="uq_application_member"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("application.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(8))  # ap | epg | host
    ref: Mapped[str] = mapped_column(String(400), index=True)
    # manual | suggestion (aus dem Projektnummer-Vorschlag uebernommen)
    origin: Mapped[str] = mapped_column(String(16), default="manual")

    application: Mapped[Application] = relationship(back_populates="members")


class ApplicationRuleHide(Base):
    """In den Kategorien der Doku ausgeblendete Regel (0.69.0): `rule_ref` =
    `<Quellsystem>|<VDOM>|<Policy-ID bzw. DN>`, `category` 1-10 bzw. leer = alle Kategorien."""

    __tablename__ = "application_rule_hide"
    __table_args__ = (
        UniqueConstraint("application_id", "rule_ref", "category", name="uq_application_rule_hide"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("application.id", ondelete="CASCADE"), index=True
    )
    rule_ref: Mapped[str] = mapped_column(String(400))
    category: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(String(400))  # Bezeichnung der Regel beim Ausblenden
    created_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    application: Mapped[Application] = relationship(back_populates="hides")
