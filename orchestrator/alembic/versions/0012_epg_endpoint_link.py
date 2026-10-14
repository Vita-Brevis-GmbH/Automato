"""epg_endpoint + epg.network_object_id (gelernte Endpunkte, EPG<->Host)

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-14

Gelernte EPG-Endpunkte (fvCEp/fvIp, akkumulierend) und die Verknuepfung EPG->Server
(Host-Objekt). Additiv/batch-safe (SQLite-FK).
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "epg_endpoint",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("epg_id", sa.Integer(), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.Column("mac", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(["epg_id"], ["epg.id"], name="fk_epg_endpoint_epg"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("epg_id", "ip", name="uq_epg_endpoint_epg_ip"),
    )
    op.create_index("ix_epg_endpoint_epg_id", "epg_endpoint", ["epg_id"])
    with op.batch_alter_table("epg") as batch:
        batch.add_column(sa.Column("network_object_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_epg_network_object", "network_object", ["network_object_id"], ["id"]
        )
        batch.create_index("ix_epg_network_object_id", ["network_object_id"])


def downgrade() -> None:
    with op.batch_alter_table("epg") as batch:
        batch.drop_index("ix_epg_network_object_id")
        batch.drop_constraint("fk_epg_network_object", type_="foreignkey")
        batch.drop_column("network_object_id")
    op.drop_index("ix_epg_endpoint_epg_id", table_name="epg_endpoint")
    op.drop_table("epg_endpoint")
