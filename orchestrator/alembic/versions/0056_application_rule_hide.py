"""Regeln je Kategorie der Applikations-Doku ausblenden (0.69.0, ADR 0012 Ergaenzung 0.69.0)

application_rule_hide: je Applikation, Regel (stabiler Verweis) und Kategorie (leer = alle) mit
Begruendung, wer und wann.

Revision ID: 0056
Revises: 0055
Create Date: 2026-10-26
"""

import sqlalchemy as sa
from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "application_rule_hide",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("rule_ref", sa.String(length=400), nullable=False),
        sa.Column("category", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("label", sa.String(length=400), nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id"], ["application.id"], name="fk_application_rule_hide_application",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "rule_ref", "category",
                            name="uq_application_rule_hide"),
    )  # fmt: skip
    op.create_index("ix_application_rule_hide_application_id", "application_rule_hide",
                    ["application_id"])  # fmt: skip


def downgrade() -> None:
    op.drop_index("ix_application_rule_hide_application_id", table_name="application_rule_hide")
    op.drop_table("application_rule_hide")
