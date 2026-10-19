"""tenant.naming_profile_id (Parser nutzt Profil-Woerterbuecher)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-14

Verknuepft einen Mandanten mit seinem Namensprofil, dessen `object_prefix`-Woerterbuch
den Namenskonvention-Parser steuert (app/naming.py). Nullable -> ohne Profil gilt die
Standard-Konvention (keine Funktionsabhaengigkeit).
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tenant") as batch:
        batch.add_column(sa.Column("naming_profile_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_tenant_naming_profile", "naming_profile", ["naming_profile_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("tenant") as batch:
        batch.drop_constraint("fk_tenant_naming_profile", type_="foreignkey")
        batch.drop_column("naming_profile_id")
