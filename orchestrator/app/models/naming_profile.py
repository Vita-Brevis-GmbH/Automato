"""Namensprofile & Wörterbücher (ADR 0004-Ergänzung).

Die Namenskonvention ist ein **austauschbares Profil pro Kunde**; die Wörterbücher
(Prefix→Typ, Farbcode, Codes, Services, Standort-Codes, …) sind
anwendungsverwaltete, **erweiterbare** Daten. `kind` ist bewusst ein offener String
(kein Enum), damit neue Namenskonventionsvorgaben **ohne Schema-Änderung** dazukommen.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class NamingProfile(Base):
    __tablename__ = "naming_profile"
    __table_args__ = (UniqueConstraint("customer", "name", name="uq_naming_profile_customer_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    customer: Mapped[str] = mapped_column(String(100), index=True)  # Kunde/Umgebung (z. B. SOP)
    name: Mapped[str] = mapped_column(String(100))


class DictionaryEntry(Base):
    __tablename__ = "dictionary_entry"
    __table_args__ = (
        UniqueConstraint("profile_id", "kind", "key", name="uq_dictionary_entry_pkk"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("naming_profile.id"), index=True)
    # Offener String (erweiterbar): object_prefix | utm_prefix | color_code | l3out |
    # gg_code | function_code | site_code | service | <neue Vorgaben>
    kind: Mapped[str] = mapped_column(String(64), index=True)
    key: Mapped[str] = mapped_column(String(200))
    value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=True
    )
