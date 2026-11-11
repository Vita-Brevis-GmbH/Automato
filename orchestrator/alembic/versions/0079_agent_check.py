"""Prüfauftrag für Geräte hinter einem Agenten (1.7.0)

Anwender 2026-11-11: „Wenn ich den internen Worker verwende, kann ich die Verbindung prüfen, beim
Collector-Agent nicht“ – Tabelle `agent_check`: der Orchestrator legt den Auftrag an, der Agent
holt ihn beim nächsten Abruf, prüft read-only und meldet das Ergebnis.

Revision ID: 0079
Revises: 0078
Create Date: 2026-11-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_check",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "agent_id", sa.Integer(), sa.ForeignKey("agent.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "source_system_id",
            sa.Integer(),
            sa.ForeignKey("source_system.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requested_by", sa.String(length=200), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
    )
    op.create_index("ix_agent_check_agent_id", "agent_check", ["agent_id"])
    op.create_index("ix_agent_check_source_system_id", "agent_check", ["source_system_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_check_source_system_id", table_name="agent_check")
    op.drop_index("ix_agent_check_agent_id", table_name="agent_check")
    op.drop_table("agent_check")
