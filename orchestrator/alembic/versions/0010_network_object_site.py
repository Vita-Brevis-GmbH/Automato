"""network_object.site_id (Standort-Verortung)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-14

Ordnet ein Objekt/System einem Standort zu (ADR 0004-Ergaenzung). Nullable; batch-safe
wegen SQLite-FK.
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("network_object") as batch:
        batch.add_column(sa.Column("site_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_network_object_site", "site", ["site_id"], ["id"])
        batch.create_index("ix_network_object_site_id", ["site_id"])


def downgrade() -> None:
    with op.batch_alter_table("network_object") as batch:
        batch.drop_index("ix_network_object_site_id")
        batch.drop_constraint("fk_network_object_site", type_="foreignkey")
        batch.drop_column("site_id")
