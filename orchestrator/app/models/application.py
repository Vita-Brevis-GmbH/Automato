"""Applikation (ADR 0012): fachliche Klammer ueber Systeme eines Mandanten.

Eine Applikation fasst Application Profiles (ACI), einzelne EPGs und Server (Host-Objekte
der Firewall) zusammen. Mitglieder sind **stabile Text-Verweise** statt Fremdschluessel -
EPGs und Objekte werden je Lauf ersetzt, die Zuordnung soll das ueberleben:

- `ap`   -> `<ACI-Tenant>/<AP>` (alle EPGs dieses Application Profiles)
- `epg`  -> DN der EPG (`uni/tn-…/ap-…/epg-…`)
- `host` -> Name des Host-Objekts (`H_app01-002-…`)

Ein Mitglied darf in mehreren Applikationen stehen (gemeinsam genutzter Server).
Kuerzel/Verantwortliche/Kritikalitaet, externer/interner Name und Verwendungszweck
(Dev/Test/Prod, 0.14.0) sind Stammdaten ohne Funktionsrelevanz.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
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
