"""Applikation als Infrastruktur kennzeichnen (0.88.0, ADR 0012 Ergaenzung 0.88.0)

application.infrastructure: Monitoring, Backup, DNS u. a. - Abhaengigkeiten zu ihnen stehen in
der Doku getrennt von den fachlichen.

Revision ID: 0057
Revises: 0056
Create Date: 2026-10-27
"""

import sqlalchemy as sa
from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "application",
        sa.Column("infrastructure", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("application", "infrastructure")
