"""NetBox-Sync: Vorschau/Uebernahme (`sync_plan`) + Standort am Quellsystem (ADR 0010)

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-20

`sync_plan`: Vorschau des NetBox-Syncs je Mandant und Ziel-NetBox (Aktionen, Kennzahlen,
Fingerabdruck) und Ergebnis der Uebernahme. `source_system.site_id`: Standort des Geraets -
dort legt der Sync Geraete und Ports in NetBox an.
"""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_plan",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("source_system_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("items", sa.JSON(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column("applied_by", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_sync_plan_tenant"),
        sa.ForeignKeyConstraint(
            ["source_system_id"],
            ["source_system.id"],
            name="fk_sync_plan_source_system",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sync_plan_tenant_id", "sync_plan", ["tenant_id"])
    op.create_index("ix_sync_plan_source_system_id", "sync_plan", ["source_system_id"])
    with op.batch_alter_table("source_system") as batch:
        batch.add_column(sa.Column("site_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_source_system_site", "site", ["site_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_index("ix_source_system_site_id", ["site_id"])


def downgrade() -> None:
    with op.batch_alter_table("source_system") as batch:
        batch.drop_index("ix_source_system_site_id")
        batch.drop_constraint("fk_source_system_site", type_="foreignkey")
        batch.drop_column("site_id")
    op.drop_index("ix_sync_plan_source_system_id", table_name="sync_plan")
    op.drop_index("ix_sync_plan_tenant_id", table_name="sync_plan")
    op.drop_table("sync_plan")
