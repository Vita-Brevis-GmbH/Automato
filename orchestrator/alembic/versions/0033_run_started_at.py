"""Collector-Lauf: Beginn (abgeholt bzw. vom zentralen Worker gestartet) fuer das Ablaufen
haengender Laeufe (0.11.3)

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "collector_run", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("collector_run", "started_at")
