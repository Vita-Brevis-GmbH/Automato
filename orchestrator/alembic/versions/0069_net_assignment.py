"""Zuordnung grosser Netze aus Regeln von Hand (0.111.0, ADR 0040)

Anwender 2026-10-29: ein Objekt 10.0.0.0/8 in einer Regel, dessen Teile einzeln geroutet werden,
landete bei der Default-Route - „markieren und zuordnen lassen, wo sie hin gehören, oder sie
anhand von Angaben in Anbindungen und Zonen ausschliessen oder zuweisen“.

Revision ID: 0069
Revises: 0068
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "net_assignment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("prefix", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=True),
        sa.Column("exit_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("changed_by", sa.String(length=200), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["zone_id"], ["logical_zone.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["exit_id"], ["topo_exit.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("tenant_id", "prefix", name="uq_net_assignment_prefix"),
    )
    op.create_index("ix_net_assignment_tenant_id", "net_assignment", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_net_assignment_tenant_id", table_name="net_assignment")
    op.drop_table("net_assignment")
