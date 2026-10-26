"""Zuordnung Benutzer -> Kunde in Automato (0.53.0, ADR 0018 Schritt 4)

customer_user: Mandant, Kunde, Login (Benutzername, E-Mail bzw. sub, klein geschrieben), Rolle
(viewer | operator), Notiz, angelegt von/am. Eindeutig je Kunde und Login.

Revision ID: 0050
Revises: 0049
Create Date: 2026-10-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customer_user",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customer.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("login", sa.String(200), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("customer_id", "login", name="uq_customer_user_login"),
    )
    op.create_index("ix_customer_user_tenant_id", "customer_user", ["tenant_id"])
    op.create_index("ix_customer_user_customer_id", "customer_user", ["customer_id"])
    op.create_index("ix_customer_user_login", "customer_user", ["login"])


def downgrade() -> None:
    op.drop_index("ix_customer_user_login", table_name="customer_user")
    op.drop_index("ix_customer_user_customer_id", table_name="customer_user")
    op.drop_index("ix_customer_user_tenant_id", table_name="customer_user")
    op.drop_table("customer_user")
