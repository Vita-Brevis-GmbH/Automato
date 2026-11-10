"""Kunden-Portal (1.3.0, ADR 0050)

Anwender 2026-11-10: Kunden im Cockpit erstellen und verwalten, Anmeldung je Kunde mit eigenem
Realm (Azure oder LDAPS) – `portal_customer`: Kurzname, Werkzeuge, Kacheln, Realm und der
Ausschnitt (Mandant, Kunden), den Benutzer dieses Realms sehen.

Revision ID: 0077
Revises: 0076
Create Date: 2026-11-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portal_customer",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("short_name", sa.String(length=42), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("doc", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("um", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("cmdb", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("links", sa.JSON(), nullable=False),
        sa.Column("realm", sa.String(length=64), nullable=True),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("customer_ids", sa.JSON(), nullable=False),
        sa.Column("default_role", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(length=200), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("realm"),
    )
    op.create_index("ix_portal_customer_short_name", "portal_customer", ["short_name"], unique=True)
    op.create_index("ix_portal_customer_tenant_id", "portal_customer", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_portal_customer_tenant_id", table_name="portal_customer")
    op.drop_index("ix_portal_customer_short_name", table_name="portal_customer")
    op.drop_table("portal_customer")
