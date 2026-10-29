"""Prüfungen der Findings je Mandant schalten (0.110.0, ADR 0039)

Anwender 2026-10-29 (Roadmap Version 1 Nr. 1): „Findings als steckbare Prüfungen“. Die Prüfungen
stehen im Code (`app/checks/<modul>/<prüfung>.py`); hier nur, was je Mandant von der Vorgabe
(an) abweicht.

Revision ID: 0068
Revises: 0067
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_check",
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("check", sa.String(length=48), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("changed_by", sa.String(length=200), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id", "check"),
    )


def downgrade() -> None:
    op.drop_table("tenant_check")
