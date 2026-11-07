"""NetBox-Sync: Nachkontrolle nach dem Übernehmen (0.115.0, ADR 0042)

Roadmap Version 1 Nr. 5 (Anwender 2026-10-30: „go 115“, gewählt die Nachkontrolle): nach dem
Schreiben liest Automato NetBox über einen rein lesenden Client neu und vergleicht je
geschriebenem Objekt; das Ergebnis steht am Plan (`sync_plan.verification`).

Revision ID: 0071
Revises: 0070
Create Date: 2026-10-30
"""

import sqlalchemy as sa
from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sync_plan", sa.Column("verification", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("sync_plan", "verification")
