"""Agent-Selbstupdate: gemeldete Version + Schalter je Agent (ADR 0011)

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent") as batch:
        batch.add_column(sa.Column("version", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column("auto_update", sa.Boolean(), nullable=False, server_default=sa.true())
        )


def downgrade() -> None:
    with op.batch_alter_table("agent") as batch:
        batch.drop_column("auto_update")
        batch.drop_column("version")
