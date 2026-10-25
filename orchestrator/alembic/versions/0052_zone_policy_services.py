"""Absicht je Dienst (0.57.0, ADR 0024 Nr. 13)

zone_policy.services: bei der Absicht „eingeschraenkt“ die erlaubten Dienste (`tcp/443`,
`udp/53`, `tcp/8000-8080`, `icmp` bzw. Name eines Service-Objekts) - eine erlaubende Regel mit
anderen Diensten zwischen den Zonen ist ein Befund. Leer = Dienste nicht geprueft.

Revision ID: 0052
Revises: 0051
Create Date: 2026-10-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("zone_policy", sa.Column("services", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("zone_policy", "services")
