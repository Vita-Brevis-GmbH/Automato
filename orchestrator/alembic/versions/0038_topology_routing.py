"""Routing, BGP, HA und L3Out je Quellsystem (ADR 0019 Stufe 2, 0.16.0)

topo_context (gelesene Kontexte, was fehlte), topo_route (RIB, BGP-Pfade, statische Routen),
topo_peer (BGP-Nachbarn), topo_ha (HA-Cluster, Session-Sync), topo_policy_route (Policy-Routen,
SD-WAN), topo_l3out (ACI-L3Outs mit Border-Leafs) - je Lauf des Quellsystems ersetzt.

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

IP_HEX = sa.String(32).with_variant(postgresql.VARCHAR(32, collation="C"), "postgresql")
_TABLES = ("topo_context", "topo_route", "topo_peer", "topo_ha", "topo_policy_route", "topo_l3out")


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "source_system_id", sa.Integer(), sa.ForeignKey("source_system.id"), nullable=False
        ),
    ]


def _node_ctx() -> list[sa.Column]:
    return [
        sa.Column("node", sa.String(200), nullable=False),
        sa.Column("context", sa.String(200), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "topo_context",
        *_base(),
        *_node_ctx(),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("missing", sa.JSON(), nullable=True),
    )
    op.create_table(
        "topo_route",
        *_base(),
        *_node_ctx(),
        sa.Column("family", sa.SmallInteger(), nullable=False),
        sa.Column("prefix", sa.String(64), nullable=False),
        sa.Column("prefix_len", sa.SmallInteger(), nullable=False),
        sa.Column("lo", IP_HEX, nullable=False),
        sa.Column("hi", IP_HEX, nullable=False),
        sa.Column("table", sa.String(8), nullable=False),
        sa.Column("protocol", sa.String(32), nullable=True),
        sa.Column("best", sa.Boolean(), nullable=False),
        sa.Column("distance", sa.Integer(), nullable=True),
        sa.Column("metric", sa.BigInteger(), nullable=True),
        sa.Column("next_hops", sa.JSON(), nullable=True),
        sa.Column("attrs", sa.JSON(), nullable=True),
    )
    op.create_index("ix_topo_route_lookup", "topo_route", ["tenant_id", "family", "lo"])
    op.create_table(
        "topo_peer",
        *_base(),
        *_node_ctx(),
        sa.Column("peer_ip", sa.String(64), nullable=False),
        sa.Column("local_ip", sa.String(64), nullable=True),
        sa.Column("remote_as", sa.BigInteger(), nullable=True),
        sa.Column("state", sa.String(32), nullable=True),
        sa.Column("attrs", sa.JSON(), nullable=True),
    )
    op.create_table(
        "topo_ha",
        *_base(),
        sa.Column("node", sa.String(200), nullable=False),
        sa.Column("mode", sa.String(16), nullable=True),
        sa.Column("group_name", sa.String(100), nullable=True),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column("members", sa.JSON(), nullable=True),
        sa.Column("session_pickup", sa.Boolean(), nullable=True),
        sa.Column("sync_peers", sa.JSON(), nullable=True),
        sa.Column("attrs", sa.JSON(), nullable=True),
    )
    op.create_table(
        "topo_policy_route",
        *_base(),
        *_node_ctx(),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("match", sa.JSON(), nullable=True),
        sa.Column("action", sa.String(32), nullable=True),
        sa.Column("gateway", sa.String(64), nullable=True),
        sa.Column("output_device", sa.JSON(), nullable=True),
        sa.Column("attrs", sa.JSON(), nullable=True),
    )
    op.create_table(
        "topo_l3out",
        *_base(),
        sa.Column("aci_tenant", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("dn", sa.String(400), nullable=False),
        sa.Column("vrf", sa.String(200), nullable=True),
        sa.Column("nodes", sa.JSON(), nullable=True),
        sa.Column("paths", sa.JSON(), nullable=True),
        sa.Column("peers", sa.JSON(), nullable=True),
        sa.Column("ext_epgs", sa.JSON(), nullable=True),
    )
    for table in _TABLES:
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
        op.create_index(f"ix_{table}_source_system_id", table, ["source_system_id"])


def downgrade() -> None:
    op.drop_index("ix_topo_route_lookup", table_name="topo_route")
    for table in reversed(_TABLES):
        op.drop_index(f"ix_{table}_source_system_id", table_name=table)
        op.drop_index(f"ix_{table}_tenant_id", table_name=table)
        op.drop_table(table)
