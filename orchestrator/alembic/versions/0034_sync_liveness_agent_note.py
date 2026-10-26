"""NetBox-Sync: Beginn und Lebenszeichen der Uebernahme; Agent: Ergebnis der Update-Pruefung
(0.11.4)

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sync_plan", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "sync_plan", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("agent", sa.Column("update_note", sa.String(300), nullable=True))


def downgrade() -> None:
    op.drop_column("agent", "update_note")
    op.drop_column("sync_plan", "heartbeat_at")
    op.drop_column("sync_plan", "started_at")
