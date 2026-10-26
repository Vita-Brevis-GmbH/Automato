"""naming_profile + dictionary_entry (ADR 0004-Ergaenzung)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-13

Namensprofile pro Kunde + generische, erweiterbare Woerterbuecher. `kind` ist ein
offener String, damit neue Namenskonventionsvorgaben ohne Schema-Aenderung dazukommen.
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "naming_profile",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("customer", "name", name="uq_naming_profile_customer_name"),
    )
    op.create_index("ix_naming_profile_customer", "naming_profile", ["customer"])
    op.create_table(
        "dictionary_entry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("value", sa.String(length=500), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["naming_profile.id"], name="fk_dictionary_entry_profile"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_id", "kind", "key", name="uq_dictionary_entry_pkk"),
    )
    op.create_index("ix_dictionary_entry_profile_id", "dictionary_entry", ["profile_id"])
    op.create_index("ix_dictionary_entry_kind", "dictionary_entry", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_dictionary_entry_kind", table_name="dictionary_entry")
    op.drop_index("ix_dictionary_entry_profile_id", table_name="dictionary_entry")
    op.drop_table("dictionary_entry")
    op.drop_index("ix_naming_profile_customer", table_name="naming_profile")
    op.drop_table("naming_profile")
