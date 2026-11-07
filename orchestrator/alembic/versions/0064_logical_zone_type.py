"""Zonentyp der Zone von Hand (0.105.0, ADR 0035)

logical_zone.zone_type: Zone des Grundschemas im Namensprofil, zu der die Zone gehoert, wenn die
Erkennung nach Name, Kurzname bzw. Vorlage je Kunde nicht passt; leer = automatisch. Der
Zonentyp bestimmt mit den Richtungen der Kategorien die Kategorie der Applikations-Doku.

Revision ID: 0064
Revises: 0063
Create Date: 2026-10-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("logical_zone", sa.Column("zone_type", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("logical_zone", "zone_type")
