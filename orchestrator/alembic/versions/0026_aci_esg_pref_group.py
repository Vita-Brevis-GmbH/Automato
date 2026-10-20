"""ACI: ESG-Selektoren und Preferred-Group-Mitgliedschaft an der EPG

ESGs (Endpoint Security Groups) stehen als `epg.kind = esg` neben den EPGs, ihre Selektoren
(EPG, Tag, IP) in `epg.selectors`. `epg.pref_group` = Mitglied der Preferred Group seiner VRF.
Taboo-Contracts und Preferred Groups sind Regeln (deny bzw. alle Ports) und brauchen kein
eigenes Schema.

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("epg") as batch:
        batch.add_column(sa.Column("selectors", sa.JSON(), nullable=True))
        batch.add_column(
            sa.Column("pref_group", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("epg") as batch:
        batch.drop_column("pref_group")
        batch.drop_column("selectors")
