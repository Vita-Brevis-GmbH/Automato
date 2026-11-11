"""Hersteller im Netzinventar (1.8.0)

Anwender: „Wird in VMware die Ports und Nachbarschaft nicht ausgelesen von den Hosts?“ – die
ESXi-Hosts eines vCenters kommen ins Netzinventar (`inv_device` Rolle `esxi`, vmnics, Nachbarn
aus CDP/LLDP). `inv_device.vendor` hält den Hardware-Hersteller fest (der NetBox-Sync braucht ihn
für den Gerätetyp).

Revision ID: 0080
Revises: 0079
Create Date: 2026-11-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inv_device", sa.Column("vendor", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("inv_device", "vendor")
