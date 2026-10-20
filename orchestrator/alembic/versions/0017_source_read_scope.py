"""Lesebereich je Quellsystem (ACI-Tenants), ADR 0009

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-20

`source_system.read_scope`: bei Cisco ACI die zu lesenden ACI-Tenants (kommagetrennt,
leer = alle ausser infra/mgmt). Additiv, bestehende Geraete behalten ihr Verhalten.
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("source_system") as batch:
        batch.add_column(
            sa.Column("read_scope", sa.String(500), server_default="", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("source_system") as batch:
        batch.drop_column("read_scope")
