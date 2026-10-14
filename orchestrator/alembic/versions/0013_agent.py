"""agent (externer Collector-Agent) + collector_run.execution

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-14

Registrierte Collector-Agenten (ADR 0008, Pull-Modell) und das Ausfuehrungsmodell je Lauf
(inprocess | external). Additiv.
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_agent_tenant"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_agent_name"),
    )
    op.create_index("ix_agent_token_hash", "agent", ["token_hash"])
    op.create_index("ix_agent_tenant_id", "agent", ["tenant_id"])
    op.add_column(
        "collector_run",
        sa.Column("execution", sa.String(length=16), server_default="inprocess", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("collector_run", "execution")
    op.drop_index("ix_agent_tenant_id", table_name="agent")
    op.drop_index("ix_agent_token_hash", table_name="agent")
    op.drop_table("agent")
