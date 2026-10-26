"""Abgelehnte Verbund-Vorschlaege paralleler Firewalls (0.19.0)

parallel_rejection: Paar Firewalls (system_a_id < system_b_id), das nicht mehr als Verbund
vorgeschlagen wird.

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parallel_rejection",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "system_a_id",
            sa.Integer(),
            sa.ForeignKey("source_system.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "system_b_id",
            sa.Integer(),
            sa.ForeignKey("source_system.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("system_a_id", "system_b_id", name="uq_parallel_rejection"),
    )
    for column in ("tenant_id", "system_a_id", "system_b_id"):
        op.create_index(f"ix_parallel_rejection_{column}", "parallel_rejection", [column])


def downgrade() -> None:
    for column in ("tenant_id", "system_a_id", "system_b_id"):
        op.drop_index(f"ix_parallel_rejection_{column}", table_name="parallel_rejection")
    op.drop_table("parallel_rejection")
