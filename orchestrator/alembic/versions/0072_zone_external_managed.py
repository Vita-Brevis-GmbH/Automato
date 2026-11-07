"""Zone „fremd verwaltet“ (0.132.0, ADR 0041 Nr. 24)

Anwender 2026-11-04: „Eingangsfilter gibt es nur bei Verbindungen nach nicht durch mich
verwaltete Geräte. Also WAN, MPLS und Fremde Anbindungen“ – je Zone ein Merkmal, Vorgabe
selbst verwaltet („Nein Standard immer intern Verwaltet nehmen“).

Revision ID: 0072
Revises: 0071
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "logical_zone",
        sa.Column("external_managed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("logical_zone", "external_managed")
