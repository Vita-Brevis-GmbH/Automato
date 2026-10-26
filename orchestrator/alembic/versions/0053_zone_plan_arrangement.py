"""Anordnung des Zonenplans (0.61.0, ADR 0024 Nr. 14)

zone_plan_arrangement: von Hand gezogene Positionen der Zonen je Mandant und Ansicht
(`overview`, `all`, `customer:<id>`) als JSON `{"<Zone>": [x, y]}`.

Revision ID: 0053
Revises: 0052
Create Date: 2026-10-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "zone_plan_arrangement",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("view", sa.String(64), nullable=False),
        sa.Column("positions", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.String(200), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "view", name="uq_zone_plan_arrangement"),
    )
    op.create_index("ix_zone_plan_arrangement_tenant_id", "zone_plan_arrangement", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_zone_plan_arrangement_tenant_id", table_name="zone_plan_arrangement")
    op.drop_table("zone_plan_arrangement")
