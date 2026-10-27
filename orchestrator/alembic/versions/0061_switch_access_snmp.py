"""Switches & Router ueber SNMPv3 (ADR 0033)

source_system.access: Zugang eines Quellsystems „Switches & Router“ - leer bzw. `ssh` (wie
bisher, show-Befehle) oder `snmp` (SNMPv3 authPriv, nur GET/GETBULK). Die SNMPv3-Zugangsdaten
liegen wie alle Zugangsdaten verschluesselt in `source_system.credentials` (keine neue Spalte).

Revision ID: 0061
Revises: 0059
Create Date: 2026-10-27
"""

import sqlalchemy as sa
from alembic import op

revision = "0061"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("source_system") as batch:
        batch.add_column(sa.Column("access", sa.String(16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("source_system") as batch:
        batch.drop_column("access")
