"""Kategorien der Applikations-Doku je Zone (0.101.0, ADR 0012 Ergaenzung 0.101.0)

logical_zone.appdoc_area: Bereich der Kategorien, zu dem die Zone gehoert - Internet (`inet`,
Kategorien 1/2), Unternehmensnetzwerk (`bit`, 3/4), VPN (`vpn`, 5), Remote Access (`sra`, 6),
andere Security Tenants (`res`, 9); leer = keiner. Eine Firewall-Regel kommt in die Kategorie
der Zone ihrer Gegenstelle, auch wenn diese ein grosser Bereich ist.

Revision ID: 0063
Revises: 0062
Create Date: 2026-10-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("logical_zone", sa.Column("appdoc_area", sa.String(length=8), nullable=True))


def downgrade() -> None:
    op.drop_column("logical_zone", "appdoc_area")
