"""Geraete-Zugangsdaten + Agent-Zuweisung (ADR 0009)

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-20

Quellsysteme bekommen den verwaltenden Collector-Agenten (`agent_id`), verschluesselte
Zugangsdaten (`credentials`, Fernet-Token) und `verify_tls`. Laeufe merken sich den Agenten,
der sie abgeholt hat (`collector_run.agent_id`) - nur dieser darf das Ergebnis abliefern.
Additiv; bestehende Geraete behalten ihr Verhalten (ohne Agent/Zugangsdaten).
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("source_system") as batch:
        batch.add_column(sa.Column("agent_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("credentials", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column("verify_tls", sa.Boolean(), server_default=sa.true(), nullable=False)
        )
        batch.create_foreign_key("fk_source_system_agent", "agent", ["agent_id"], ["id"])
    op.create_index("ix_source_system_agent_id", "source_system", ["agent_id"])
    with op.batch_alter_table("collector_run") as batch:
        batch.add_column(sa.Column("agent_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_collector_run_agent", "agent", ["agent_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("collector_run") as batch:
        batch.drop_constraint("fk_collector_run_agent", type_="foreignkey")
        batch.drop_column("agent_id")
    op.drop_index("ix_source_system_agent_id", table_name="source_system")
    with op.batch_alter_table("source_system") as batch:
        batch.drop_constraint("fk_source_system_agent", type_="foreignkey")
        batch.drop_column("verify_tls")
        batch.drop_column("credentials")
        batch.drop_column("agent_id")
