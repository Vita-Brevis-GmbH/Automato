"""Namen der Gegenstellen in der Applikations-Doku (0.95.0, ADR 0012 Ergaenzung 0.95.0)

tenant.appdoc_foreign_names: Gegenstellen, von denen eine Firewall-Regel nur die Adresse kennt,
zeigt die Doku nach Kategorien mit Namen (IPAM, VM, EPG bzw. Firewall-Objekt) - nur fuer
Betreuer, Standard aus, setzt der Admin.

Revision ID: 0060
Revises: 0059
Create Date: 2026-10-27
"""

import sqlalchemy as sa
from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant",
        sa.Column("appdoc_foreign_names", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("tenant", "appdoc_foreign_names")
