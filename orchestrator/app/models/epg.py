"""EPG (Endpoint Group) + Subnetze + gelernte Endpunkte (ACI / Model 1, ADR 0004 Phase 2).

Eine EPG ist die ACI-Gruppierung eines Servers („ein Server = genau eine EPG");
die AP-Klammer (`AP_###`) ist das Projekt. EPGs sind Endpunkte von ACI-Contracts
(`rule_endpoint.epg_id`). `epg_subnet` haelt die hinterlegten Subnetze (`fvSubnet`,
dual-stack), `epg_endpoint` die gelernten Endpunkte (`fvCEp`/`fvIp`, akkumulierend).
`epg.network_object_id` verknuepft die EPG mit dem zugehoerigen Server (Host-Objekt).
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
    # Zugehoeriger Server (Host-Objekt), sofern aufloesbar ("ein Server = eine EPG")
    network_object_id: Mapped[int | None] = mapped_column(
        ForeignKey("network_object.id"), nullable=True, index=True
    )


class EpgSubnet(Base):
    __tablename__ = "epg_subnet"

    id: Mapped[int] = mapped_column(primary_key=True)
    epg_id: Mapped[int] = mapped_column(ForeignKey("epg.id"), index=True)
    ip: Mapped[str] = mapped_column(String(64))  # CIDR, dual-stack
    family: Mapped[str | None] = mapped_column(String(4), nullable=True)  # "v4" | "v6"


class EpgEndpoint(Base):
    """Gelernter Endpunkt einer EPG (fvCEp/fvIp) - akkumulierend."""

    __tablename__ = "epg_endpoint"
    __table_args__ = (UniqueConstraint("epg_id", "ip", name="uq_epg_endpoint_epg_ip"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    epg_id: Mapped[int] = mapped_column(ForeignKey("epg.id"), index=True)
    ip: Mapped[str] = mapped_column(String(64))
    mac: Mapped[str | None] = mapped_column(String(32), nullable=True)
