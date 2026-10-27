"""Verlauf des Routings (ADR 0030)

topo_change: je Lauf eines Quellsystems die Aenderungen an Routen, BGP-Nachbarn, HA,
Policy-Routen, Kontexten und L3Outs gegenueber dem vorigen Stand (vorher/nachher mit den
verglichenen Feldern) sowie Vermerke (Ausgangsstand, nicht lesbar, Obergrenze). Gesammelt bis zur
Frist der Aufbewahrung; der Lauf als Kennung ohne Fremdschluessel.

Revision ID: 0057
Revises: 0056
Create Date: 2026-10-27
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None

IP_HEX = sa.String(32).with_variant(postgresql.VARCHAR(32, collation="C"), "postgresql")


def upgrade() -> None:
    op.create_table(
        "topo_change",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("source_system_id", sa.Integer(), nullable=False),
        sa.Column("collector_run_id", sa.String(length=36), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("node", sa.String(length=200), nullable=True),
        sa.Column("context", sa.String(length=200), nullable=True),
        sa.Column("table", sa.String(length=8), nullable=True),
        sa.Column("key", sa.String(length=400), nullable=False),
        sa.Column("prefix", sa.String(length=64), nullable=True),
        sa.Column("family", sa.SmallInteger(), nullable=True),
        sa.Column("lo", IP_HEX, nullable=True),
        sa.Column("hi", IP_HEX, nullable=True),
        sa.Column("change", sa.String(length=12), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_topo_change_tenant"),
        sa.ForeignKeyConstraint(
            ["source_system_id"], ["source_system.id"], name="fk_topo_change_source_system"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_topo_change_tenant_id", "topo_change", ["tenant_id"])
    op.create_index("ix_topo_change_source_system_id", "topo_change", ["source_system_id"])
    op.create_index("ix_topo_change_at", "topo_change", ["at"])
    op.create_index("ix_topo_change_tenant_at", "topo_change", ["tenant_id", "at"])
    op.create_index("ix_topo_change_route", "topo_change", ["source_system_id", "kind", "prefix"])


def downgrade() -> None:
    for name in ("ix_topo_change_route", "ix_topo_change_tenant_at", "ix_topo_change_at",
                 "ix_topo_change_source_system_id", "ix_topo_change_tenant_id"):  # fmt: skip
        op.drop_index(name, table_name="topo_change")
    op.drop_table("topo_change")
