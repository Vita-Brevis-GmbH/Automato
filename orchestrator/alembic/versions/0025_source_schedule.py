"""Zeitgesteuerte Laeufe je Quellsystem: Zeitplan (Cron) und Ausloeser des Laufs

`source_system.schedule` (Cron, 5 Felder) und `schedule_last_at` (zuletzt ausgeloester
Termin; der Scheduler beansprucht einen Termin per bedingtem UPDATE, damit mehrere
Orchestrator-Prozesse ihn nur einmal ausloesen). `collector_run.trigger` = manual | schedule.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("source_system") as batch:
        batch.add_column(sa.Column("schedule", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("schedule_last_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("collector_run") as batch:
        batch.add_column(
            sa.Column("trigger", sa.String(length=16), nullable=False, server_default="manual")
        )


def downgrade() -> None:
    with op.batch_alter_table("collector_run") as batch:
        batch.drop_column("trigger")
    with op.batch_alter_table("source_system") as batch:
        batch.drop_column("schedule_last_at")
        batch.drop_column("schedule")
