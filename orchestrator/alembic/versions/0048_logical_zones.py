"""Logische Zonen und Absicht je Zonenpaar (0.51.0, ADR 0024)

logical_zone (Name, Kuerzel, Art, Kunde, Farbe, Reihenfolge), logical_zone_selector (Art, Wert,
Quellsystem, ausschliessen - wie beim Kunden, dazu `exit`) und zone_policy (Absicht
allow/limited/deny je Paar Quell- und Zielzone, Notiz).

Revision ID: 0048
Revises: 0047
Create Date: 2026-10-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "logical_zone",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("code", sa.String(50), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customer.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("color", sa.String(16), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_logical_zone_tenant_name"),
    )
    op.create_index("ix_logical_zone_tenant_id", "logical_zone", ["tenant_id"])
    op.create_index("ix_logical_zone_customer_id", "logical_zone", ["customer_id"])
    op.create_table(
        "logical_zone_selector",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "zone_id",
            sa.Integer(),
            sa.ForeignKey("logical_zone.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("value", sa.String(400), nullable=False),
        sa.Column(
            "source_system_id",
            sa.Integer(),
            sa.ForeignKey("source_system.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("exclude", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_logical_zone_selector_zone_id", "logical_zone_selector", ["zone_id"])
    op.create_table(
        "zone_policy",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "src_zone_id",
            sa.Integer(),
            sa.ForeignKey("logical_zone.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dst_zone_id",
            sa.Integer(),
            sa.ForeignKey("logical_zone.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("intent", sa.String(8), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.String(200), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("src_zone_id", "dst_zone_id", name="uq_zone_policy_pair"),
    )
    op.create_index("ix_zone_policy_tenant_id", "zone_policy", ["tenant_id"])
    op.create_index("ix_zone_policy_src_zone_id", "zone_policy", ["src_zone_id"])
    op.create_index("ix_zone_policy_dst_zone_id", "zone_policy", ["dst_zone_id"])


def downgrade() -> None:
    op.drop_index("ix_zone_policy_dst_zone_id", table_name="zone_policy")
    op.drop_index("ix_zone_policy_src_zone_id", table_name="zone_policy")
    op.drop_index("ix_zone_policy_tenant_id", table_name="zone_policy")
    op.drop_table("zone_policy")
    op.drop_index("ix_logical_zone_selector_zone_id", table_name="logical_zone_selector")
    op.drop_table("logical_zone_selector")
    op.drop_index("ix_logical_zone_customer_id", table_name="logical_zone")
    op.drop_index("ix_logical_zone_tenant_id", table_name="logical_zone")
    op.drop_table("logical_zone")
