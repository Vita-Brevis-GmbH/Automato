"""Kunden als Ausschnitt der Umgebung (0.30.0, ADR 0018 Schritt 1)

customer (Name, Kuerzel, geteilt), customer_selector (Art, Wert, Quellsystem, ausschliessen)
und customer_member (abgeleitete Zugehoerigkeit: EPG, Netz, Werte-Objekt, Regel, VM).

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customer",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("code", sa.String(50), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("shared", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("derived_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "name", name="uq_customer_tenant_name"),
    )
    op.create_index("ix_customer_tenant_id", "customer", ["tenant_id"])
    op.create_table(
        "customer_selector",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customer.id", ondelete="CASCADE"),
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
    op.create_index("ix_customer_selector_customer_id", "customer_selector", ["customer_id"])
    op.create_table(
        "customer_member",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customer.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("ref_id", sa.Integer(), nullable=True),
        sa.Column("value", sa.String(64), nullable=True),
        sa.Column("via", sa.String(200), nullable=True),
    )
    op.create_index("ix_customer_member_tenant_id", "customer_member", ["tenant_id"])
    op.create_index("ix_customer_member_customer_id", "customer_member", ["customer_id"])
    op.create_index(
        "ix_customer_member_lookup", "customer_member", ["customer_id", "kind", "ref_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_customer_member_lookup", table_name="customer_member")
    op.drop_index("ix_customer_member_customer_id", table_name="customer_member")
    op.drop_index("ix_customer_member_tenant_id", table_name="customer_member")
    op.drop_table("customer_member")
    op.drop_index("ix_customer_selector_customer_id", table_name="customer_selector")
    op.drop_table("customer_selector")
    op.drop_index("ix_customer_tenant_id", table_name="customer")
    op.drop_table("customer")
