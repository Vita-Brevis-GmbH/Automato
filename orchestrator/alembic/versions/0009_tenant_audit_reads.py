"""tenant.audit_reads (per-Mandant Lesezugriffs-Audit)

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-14

Per Mandant zuschaltbares Audit fuer Lesezugriffe auf die (sensible) Doku (ADR 0007 D5).
Additiv mit server_default false -> bestehende Zeilen bleiben gueltig, Audit standardmaessig
aus.
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant",
        sa.Column("audit_reads", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("tenant", "audit_reads")
