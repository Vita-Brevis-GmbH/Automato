"""Routing, BGP, HA und L3Out je Quellsystem (ADR 0019 Stufe 2) - Grundlage fuer Pfade.

Wie das Netzinventar je Lauf des Quellsystems ersetzt (aktueller Stand; die Aenderungen von Lauf
zu Lauf haelt `topo_change` fest, ADR 0030). Ein Routing-Kontext ist ein FortiGate-VDOM bzw. eine
ACI-VRF auf einem Border-Leaf (`topo_context`, mit dem, was nicht lesbar war - die Aussage ist
dann „unbekannt“).

- `topo_route`: wirksame Routing-Tabelle (`rib`, ECMP = mehrere Next-Hops), alle BGP-Pfade
  (`bgp`, auch nicht beste) und konfigurierte statische Routen (`static`, auch inaktive). Der
  Bereich des Praefixes steht als Hex fester Breite (`lo`/`hi`, wie im Regelindex) fuer die
  Suche nach dem laengsten Praefix.
- `topo_peer`: BGP-Nachbarn mit Zustand; `topo_ha`: HA-Cluster und Session-Sync (FGSP);
  `topo_policy_route`: Policy-Routen und SD-WAN-Regeln; `topo_l3out`: ACI-L3Outs mit
  Border-Leafs, Interfaces/SVIs, Peers und statischen Routen.

Von Hand gepflegt (0.18.0, nicht je Lauf ersetzt): `topo_exit` - Anbindungen nach aussen
(Internet, MPLS, WAN, Partner), an denen ein Weg endet bzw. weitergeht; `topo_exit_dismissal`
(0.21.0) - erkannte Router, die nicht mehr als Anbindung vorgeschlagen werden.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
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


class TopoChange(Base):
    """Verlauf des Routings (ADR 0030): was sich zwischen zwei Laeufen eines Quellsystems an
    Routen, BGP-Nachbarn, HA, Policy-Routen, Kontexten und L3Outs geaendert hat - beim Einspielen
    aus dem Vergleich alter gegen neuer Stand, bevor `topology.replace` ersetzt. Nicht je Lauf
    ersetzt, sondern gesammelt bis zur Frist `retention_topo_changes_days` (`store/retention`).

    `change`: added | removed | changed; Vermerke: baseline (erster Lauf = Ausgangsstand),
    unreadable (Kontext nicht lesbar - Entfernungen nicht erfasst), truncated (Obergrenze je Lauf
    erreicht, Rest gezaehlt). `before`/`after` nur mit den verglichenen Feldern. Der Lauf steht
    als Kennung ohne Fremdschluessel da - Laeufe haben eine kuerzere Frist."""

    __tablename__ = "topo_change"
    __table_args__ = (
        Index("ix_topo_change_tenant_at", "tenant_id", "at"),
        Index("ix_topo_change_route", "source_system_id", "kind", "prefix"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    source_system_id: Mapped[int] = mapped_column(ForeignKey("source_system.id"), index=True)
    collector_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # route | bgp_peer | ha | policy_route | context | l3out
    kind: Mapped[str] = mapped_column(String(16))
    node: Mapped[str | None] = mapped_column(String(200), nullable=True)  # Firewall bzw. Leaf
    context: Mapped[str | None] = mapped_column(String(200), nullable=True)  # VDOM bzw. VRF
    # rib | bgp | static (Routen) bzw. policy | sdwan (Policy-Routen)
    table: Mapped[str | None] = mapped_column(String(8), nullable=True)
    key: Mapped[str] = mapped_column(String(400))  # z. B. Praefix (+ VRF, Nachbar, seq)
    prefix: Mapped[str | None] = mapped_column(String(64), nullable=True)
    family: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    lo: Mapped[str | None] = mapped_column(IP_HEX, nullable=True)  # Bereich (Kunden-Filter)
    hi: Mapped[str | None] = mapped_column(IP_HEX, nullable=True)
    change: Mapped[str] = mapped_column(String(12))
    before: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)


class TopoExit(Base):
    """Anbindung nach aussen (0.18.0, von Hand erfasst): Ein Weg, der ueber einen dieser
    Next-Hops bzw. ueber dieses Interface/diese Zone (bestimmter Firewalls oder aller) geht, endet
    hier („Internet“, „MPLS Kunde A“) - oder geht nach den manuellen Routen (`routes`: Praefix
    -> Knoten `<Quellsystem>|<Kontext>`) bei einem gelesenen Knoten weiter (z. B. ueber MPLS
    zur Firewall eines anderen Standorts). Gilt nur, wenn der Next-Hop keinem gelesenen Geraet
    gehoert."""

    __tablename__ = "topo_exit"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))  # internet | mpls | wan | partner | other
    # nur fuer diese Firewalls (Next-Hops, Interfaces/Zonen), z. B. beide Session-Sync-Partner;
    # leer = jede (0.20.0; vorher eine Firewall)
    source_system_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    interfaces: Mapped[list | None] = mapped_column(JSON, nullable=True)  # Interfaces bzw. Zonen
    next_hops: Mapped[list | None] = mapped_column(JSON, nullable=True)  # IPs der Gegenstelle
    prefixes: Mapped[list | None] = mapped_column(JSON, nullable=True)  # Netze dahinter
    routes: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{prefix, target}]
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class TopoExitDismissal(Base):
    """Erkannter Router (IP), der nicht mehr als Anbindung vorgeschlagen wird (0.21.0,
    „ausblenden“). Ein Vorschlag verschwindet, wenn alle seine offenen Router ausgeblendet
    sind; ein neu erkannter Router der Anbindung zeigt ihn wieder."""

    __tablename__ = "topo_exit_dismissal"
    __table_args__ = (UniqueConstraint("tenant_id", "ip", name="uq_topo_exit_dismissal"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    ip: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
