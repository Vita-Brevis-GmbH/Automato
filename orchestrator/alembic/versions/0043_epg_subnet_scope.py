"""Scope der Subnetze externer EPGs (0.28.0)

epg_subnet.scope: `l3extSubnet.scope` aus dem APIC (z. B. `import-security,shared-rtctrl`).
Nur Subnetze mit `import-security` ordnen Verkehr der externen EPG zu; leer = APIC-Standard
bzw. aelterer Lauf (gilt als `import-security`).

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("epg_subnet", sa.Column("scope", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("epg_subnet", "scope")
