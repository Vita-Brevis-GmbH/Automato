"""agent.created_at NOT NULL wie im Modell (gefunden von `alembic check` in der CI)

Migration 0013 legte die Spalte nullable an, das Modell erwartet NOT NULL. Leere Werte
(sollte es nicht geben - die Anwendung setzt sie immer) werden vorher auf jetzt gesetzt.

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE agent SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")
    with op.batch_alter_table("agent") as batch:
        batch.alter_column(
            "created_at", existing_type=sa.DateTime(timezone=True), nullable=False
        )


def downgrade() -> None:
    with op.batch_alter_table("agent") as batch:
        batch.alter_column("created_at", existing_type=sa.DateTime(timezone=True), nullable=True)
