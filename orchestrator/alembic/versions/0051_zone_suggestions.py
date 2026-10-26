"""Zonen-Vorschlaege (0.54.0, ADR 0024 Nr. 10)

inv_interface.role: Rolle des FortiGate-Interfaces (lan | wan | dmz bzw. management bei
`dedicated-to management`) - Hinweis fuer die Art einer vorgeschlagenen Zone.
zone_suggestion_dismissal: ausgeblendete Vorschlaege je Mandant (normalisierter Name).

Revision ID: 0051
Revises: 0050
Create Date: 2026-10-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inv_interface", sa.Column("role", sa.String(16), nullable=True))
    op.create_table(
        "zone_suggestion_dismissal",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("created_by", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "key", name="uq_zone_suggestion_dismissal"),
    )
    op.create_index(
        "ix_zone_suggestion_dismissal_tenant_id", "zone_suggestion_dismissal", ["tenant_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_zone_suggestion_dismissal_tenant_id", table_name="zone_suggestion_dismissal"
    )
    op.drop_table("zone_suggestion_dismissal")
    op.drop_column("inv_interface", "role")
