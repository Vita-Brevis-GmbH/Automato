"""Standort (Site) eines Mandanten (GG) - ADR 0004-Ergaenzung.

Ein Mandant (= Gruppengesellschaft) hat einen oder mehrere Standorte. Jeder Standort
traegt einen **dreistelligen Code** (SOP-Konvention, z. B. "000"), der Objekte (VMs etc.)
einem Ort zuordnet. Reine Stammdaten/Beschriftung - keine Funktionsrelevanz fuer den
Plattform-Kern; Code pro Mandant eindeutig.
"""

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Site(Base):
    __tablename__ = "site"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_site_tenant_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    code: Mapped[str] = mapped_column(String(3))  # dreistelliger Standort-Code (z. B. "000")
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
