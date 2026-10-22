"""Routing, BGP, HA und L3Out je Quellsystem (ADR 0019 Stufe 2) - Grundlage fuer Pfade.

Wie das Netzinventar je Lauf des Quellsystems ersetzt (aktueller Stand, ein Verlauf folgt bei
Bedarf). Ein Routing-Kontext ist ein FortiGate-VDOM bzw. eine ACI-VRF auf einem Border-Leaf
(`topo_context`, mit dem, was nicht lesbar war - die Aussage ist dann „unbekannt“).

- `topo_route`: wirksame Routing-Tabelle (`rib`, ECMP = mehrere Next-Hops), alle BGP-Pfade
  (`bgp`, auch nicht beste) und konfigurierte statische Routen (`static`, auch inaktive). Der
  Bereich des Praefixes steht als Hex fester Breite (`lo`/`hi`, wie im Regelindex) fuer die
  Suche nach dem laengsten Praefix.
- `topo_peer`: BGP-Nachbarn mit Zustand; `topo_ha`: HA-Cluster und Session-Sync (FGSP);
  `topo_policy_route`: Policy-Routen und SD-WAN-Regeln; `topo_l3out`: ACI-L3Outs mit
  Border-Leafs, Interfaces/SVIs, Peers und statischen Routen.
"""

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Index, Integer, SmallInteger, String
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

IP_HEX = String(32).with_variant(postgresql.VARCHAR(32, collation="C"), "postgresql")


class _TopoRow:
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    source_system_id: Mapped[int] = mapped_column(ForeignKey("source_system.id"), index=True)


class TopoContext(_TopoRow, Base):
    __tablename__ = "topo_context"

    node: Mapped[str] = mapped_column(String(200))  # Firewall bzw. Border-Leaf
    context: Mapped[str] = mapped_column(String(200))  # VDOM bzw. Tenant:VRF
    kind: Mapped[str] = mapped_column(String(8))  # vdom | vrf
    missing: Mapped[list | None] = mapped_column(JSON, nullable=True)  # nicht lesbar


class TopoRoute(_TopoRow, Base):
    __tablename__ = "topo_route"
    __table_args__ = (Index("ix_topo_route_lookup", "tenant_id", "family", "lo"),)

    node: Mapped[str] = mapped_column(String(200))
    context: Mapped[str] = mapped_column(String(200))
    family: Mapped[int] = mapped_column(SmallInteger)  # 4 | 6
    prefix: Mapped[str] = mapped_column(String(64))
    prefix_len: Mapped[int] = mapped_column(SmallInteger)
    lo: Mapped[str] = mapped_column(IP_HEX)
    hi: Mapped[str] = mapped_column(IP_HEX)
    table: Mapped[str] = mapped_column(String(8))  # rib | bgp | static
    protocol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    best: Mapped[bool] = mapped_column(Boolean, default=True)
    distance: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metric: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    next_hops: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{ip, interface}]
    attrs: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class TopoPeer(_TopoRow, Base):
    __tablename__ = "topo_peer"

    node: Mapped[str] = mapped_column(String(200))
    context: Mapped[str] = mapped_column(String(200))
    peer_ip: Mapped[str] = mapped_column(String(64))
    local_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    remote_as: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attrs: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class TopoHa(_TopoRow, Base):
    __tablename__ = "topo_ha"

    node: Mapped[str] = mapped_column(String(200))
    mode: Mapped[str | None] = mapped_column(String(16), nullable=True)  # standalone | a-p | a-a
    group_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    members: Mapped[list | None] = mapped_column(JSON, nullable=True)
    session_pickup: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sync_peers: Mapped[list | None] = mapped_column(JSON, nullable=True)  # FGSP
    attrs: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class TopoPolicyRoute(_TopoRow, Base):
    __tablename__ = "topo_policy_route"

    node: Mapped[str] = mapped_column(String(200))
    context: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(8))  # policy | sdwan
    seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    match: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    gateway: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_device: Mapped[list | None] = mapped_column(JSON, nullable=True)
    attrs: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class TopoL3Out(_TopoRow, Base):
    __tablename__ = "topo_l3out"

    aci_tenant: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    dn: Mapped[str] = mapped_column(String(400))
    vrf: Mapped[str | None] = mapped_column(String(200), nullable=True)
    nodes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    paths: Mapped[list | None] = mapped_column(JSON, nullable=True)
    peers: Mapped[list | None] = mapped_column(JSON, nullable=True)
    ext_epgs: Mapped[list | None] = mapped_column(JSON, nullable=True)


TOPOLOGY_MODELS = (TopoContext, TopoRoute, TopoPeer, TopoHa, TopoPolicyRoute, TopoL3Out)
