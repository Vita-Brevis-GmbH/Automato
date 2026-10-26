"""Parallele Firewalls und Anbindungen nach aussen (0.18.0)

source_system.parallel_group (Verbund paralleler Firewalls), rule_index.twin_of/parallel
(gleiche Regel einer anderen Firewall des Verbunds, abgeleitet) und topo_exit (von Hand
erfasste Anbindungen: Internet, MPLS, WAN, Partner).

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("source_system", sa.Column("parallel_group", sa.String(100), nullable=True))
    op.add_column("rule_index", sa.Column("twin_of", sa.Integer(), nullable=True))
    op.add_column("rule_index", sa.Column("parallel", sa.JSON(), nullable=True))
    op.create_index("ix_rule_index_twin_of", "rule_index", ["twin_of"])
    op.create_table(
        "topo_exit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column(
            "source_system_id",
            sa.Integer(),
            sa.ForeignKey("source_system.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("interfaces", sa.JSON(), nullable=True),
        sa.Column("next_hops", sa.JSON(), nullable=True),
        sa.Column("prefixes", sa.JSON(), nullable=True),
        sa.Column("routes", sa.JSON(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_topo_exit_tenant_id", "topo_exit", ["tenant_id"])
    op.create_index("ix_topo_exit_source_system_id", "topo_exit", ["source_system_id"])


def downgrade() -> None:
    op.drop_index("ix_topo_exit_source_system_id", table_name="topo_exit")
    op.drop_index("ix_topo_exit_tenant_id", table_name="topo_exit")
    op.drop_table("topo_exit")
    op.drop_index("ix_rule_index_twin_of", table_name="rule_index")
    op.drop_column("rule_index", "parallel")
    op.drop_column("rule_index", "twin_of")
    op.drop_column("source_system", "parallel_group")
