"""Erkannte Anbindungen ausblenden (0.21.0)

topo_exit_dismissal: erkannter Router (IP) je Mandant, der nicht mehr als Anbindung
vorgeschlagen wird.

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "topo_exit_dismissal",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("ip", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "ip", name="uq_topo_exit_dismissal"),
    )
    op.create_index("ix_topo_exit_dismissal_tenant_id", "topo_exit_dismissal", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_topo_exit_dismissal_tenant_id", table_name="topo_exit_dismissal")
    op.drop_table("topo_exit_dismissal")
