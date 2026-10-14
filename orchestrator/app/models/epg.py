"""EPG (Endpoint Group) + Subnetze (ACI / Model 1, ADR 0004 Phase 2).

Eine EPG ist die ACI-Gruppierung eines Servers („ein Server = genau eine EPG");
die AP-Klammer (`AP_###`) ist das Projekt. EPGs sind Endpunkte von ACI-Contracts
(`rule_endpoint.epg_id`). `epg_subnet` haelt die an der EPG hinterlegten Subnetze
(`fvSubnet`, dual-stack). Gelernte Endpunkte (`epg_endpoint`) folgen bei Bedarf.
"""

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Epg(Base):
    __tablename__ = "epg"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_epg_tenant_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))  # EG_...
    ap: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )  # AP_### (Projekt)
    bd: Mapped[str | None] = mapped_column(String(100), nullable=True)  # BD_...


class EpgSubnet(Base):
    __tablename__ = "epg_subnet"

    id: Mapped[int] = mapped_column(primary_key=True)
    epg_id: Mapped[int] = mapped_column(ForeignKey("epg.id"), index=True)
    ip: Mapped[str] = mapped_column(String(64))  # CIDR, dual-stack
    family: Mapped[str | None] = mapped_column(String(4), nullable=True)  # "v4" | "v6"
