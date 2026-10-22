"""EPG (Endpoint Group) + Subnetze + gelernte Endpunkte (ACI / Model 1, ADR 0004 Phase 2).

Eine EPG ist die ACI-Gruppierung eines Servers („ein Server = genau eine EPG");
die AP-Klammer (`AP_###`) ist das Projekt. EPGs sind Endpunkte von ACI-Contracts
(`rule_endpoint.epg_id`) - ebenso externe EPGs eines L3Out (`kind=external`) und vzAny
einer VRF (`kind=vzany`). Identitaet ist die DN (`uni/tn-<TE>/ap-<AP>/epg-<EG>`):
dieselben Namen gibt es in vielen ACI-Tenants. `epg_subnet` haelt die Subnetze (EPG-,
BD- bzw. externe Subnetze, dual-stack), `epg_endpoint` die gelernten Endpunkte
(`fvCEp`/`fvIp`). `epg.network_object_id` verknuepft die EPG mit dem Server (Host-Objekt).
"""

from sqlalchemy import JSON, Boolean, ForeignKey, String, UniqueConstraint, false, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Epg(Base):
    __tablename__ = "epg"
    __table_args__ = (UniqueConstraint("tenant_id", "dn", name="uq_epg_tenant_dn"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))  # EG_...
    # epg | external (L3Out-EPG) | vzany (alle EPGs einer VRF)
    kind: Mapped[str] = mapped_column(String(16), default="epg", server_default=text("'epg'"))
    aci_tenant: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # DN aus dem APIC; aeltere Laeufe ohne (dann nur ueber den Namen gefunden)
    dn: Mapped[str | None] = mapped_column(String(400), nullable=True)
    vrf: Mapped[str | None] = mapped_column(String(200), nullable=True)
    l3out: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # APIC, der die EPG zuletzt gemeldet hat (veraltete EPGs je APIC aufraeumen)
    source_system_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_system.id"), nullable=True, index=True
    )
    ap: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )  # AP_### (Projekt)
    bd: Mapped[str | None] = mapped_column(String(100), nullable=True)  # BD_...
    # ESG: Selektoren als Text ("EPG <dn>", "Tag k=v", "IP <ausdruck>")
    selectors: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Mitglied der Preferred Group seiner VRF (prefGrMemb=include)
    pref_group: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
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
    # epg (fvSubnet der EPG) | bd (Subnetz der Bridge Domain) | ext (l3extSubnet)
    origin: Mapped[str] = mapped_column(String(8), default="epg", server_default=text("'epg'"))
    # externe EPG: `l3extSubnet.scope` (import-security = Zuordnung); leer = APIC-Standard
    scope: Mapped[str | None] = mapped_column(String(200), nullable=True)


class EpgEndpoint(Base):
    """Gelernter Endpunkt einer EPG (fvCEp/fvIp) - akkumulierend."""

    __tablename__ = "epg_endpoint"
    __table_args__ = (UniqueConstraint("epg_id", "ip", name="uq_epg_endpoint_epg_ip"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    epg_id: Mapped[int] = mapped_column(ForeignKey("epg.id"), index=True)
    ip: Mapped[str] = mapped_column(String(64))
    mac: Mapped[str | None] = mapped_column(String(32), nullable=True)
