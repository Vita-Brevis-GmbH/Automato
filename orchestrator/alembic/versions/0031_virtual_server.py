"""FortiGate Virtual Server: VIP-Typ, Familie, Real Server und Einstellungen (0.10.0)

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("nat_rule") as batch:
        batch.add_column(sa.Column("vip_type", sa.String(32), nullable=True))
        batch.add_column(sa.Column("family", sa.String(4), nullable=True))
        batch.add_column(sa.Column("targets", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("attrs", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("nat_rule") as batch:
        for column in ("attrs", "targets", "family", "vip_type"):
            batch.drop_column(column)
