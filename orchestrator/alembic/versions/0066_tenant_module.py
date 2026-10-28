"""Module je Mandant schalten (0.107.0, ADR 0036)

Anwender 2026-10-28: Module Worker, Gateway, Doku, Wegsucher, Findings, Analyse (dazu Design und
der Schreibpfad) „damit man die auch modular deaktivieren kann“ - „je Mandant wählbar“. Die
Module stehen im Code (`app/modules.py`); hier nur, was je Mandant von der Vorgabe (an)
abweicht.

Revision ID: 0066
Revises: 0065
Create Date: 2026-10-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_module",
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("module", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("changed_by", sa.String(length=200), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id", "module"),
    )


def downgrade() -> None:
    op.drop_table("tenant_module")
