"""Standort (Site) eines Mandanten (GG) - ADR 0004-Ergaenzung, ADR 0015.

Ein Mandant (= Gruppengesellschaft) hat Standorte zweier Arten:
- **virtuell** (`kind = "virtual"`): dreistellige Nummer der Namenskonvention (`000`, `001`, ...),
  steht in Netz- und Cluster-Namen (`N_<tenant>-000-...`, `fwc11-000-<tenant>`); `000` ist
  z. B. das Stretched DC. Ein virtueller Standort kann ueber mehrere physische laufen.
- **physisch** (`kind = "physical"`): Ort/Rechenzentrum, z. B. `chzrh1` (Land, Ort, Nummer),
  steht in Namen physischer Geraete (`evh01-chzrh1-<tenant>`).

Die Zuordnung virtuell -> physisch ist n:m (`site_link`). Reine Stammdaten/Beschriftung -
keine Funktionsrelevanz fuer den Plattform-Kern; Code pro Mandant eindeutig.
"""

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

SITE_KINDS = ("virtual", "physical")


class Site(Base):
    __tablename__ = "site"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_site_tenant_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    code: Mapped[str] = mapped_column(String(16))  # virtuell "000", physisch "chzrh1"
    kind: Mapped[str] = mapped_column(String(16), default="virtual", server_default="virtual")
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class SiteLink(Base):
    """Virtueller Standort laeuft an einem physischen Standort (n:m)."""

    __tablename__ = "site_link"
    __table_args__ = (UniqueConstraint("virtual_id", "physical_id", name="uq_site_link"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    virtual_id: Mapped[int] = mapped_column(ForeignKey("site.id", ondelete="CASCADE"), index=True)
    physical_id: Mapped[int] = mapped_column(ForeignKey("site.id", ondelete="CASCADE"), index=True)
