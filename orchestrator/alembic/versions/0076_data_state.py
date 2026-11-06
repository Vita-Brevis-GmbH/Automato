"""Datenstand nach Inhalt (0.141.0, ADR 0049)

Anwender 2026-11-06: „Die Berechnungen nur anstossen, wenn was geändert hat, also nur die Wege
berechnen, welche von der geänderten Firewall Regel betroffen sind“ – `data_state` (Abdruck je
Quellsystem), `data_change` (was ein Lauf geändert hat) und am Stand der Doku der Abdruck ohne
Datenstand, der Datenstand beim Rechnen und der Fussabdruck (was der Stand benutzt).

Revision ID: 0076
Revises: 0075
Create Date: 2026-11-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_state",
        sa.Column("source_system_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("parts", sa.JSON(), nullable=False),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_system_id"], ["source_system.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("source_system_id"),
    )
    op.create_index("ix_data_state_tenant_id", "data_state", ["tenant_id"])
    op.create_table(
        "data_change",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("source_system_id", sa.Integer(), nullable=True),
        sa.Column("run_id", sa.String(36), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parts", sa.JSON(), nullable=False),
        sa.Column("all", sa.Boolean(), nullable=False),
        sa.Column("net", sa.Boolean(), nullable=False),
        sa.Column("rules", sa.JSON(), nullable=True),
        sa.Column("devices", sa.JSON(), nullable=True),
        sa.Column("ips", sa.JSON(), nullable=True),
        sa.Column("epgs", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_data_change_tenant_id", "data_change", ["tenant_id"])
    op.create_index("ix_data_change_seq", "data_change", ["seq"])
    op.add_column("doc_snapshot", sa.Column("base", sa.String(64), nullable=True))
    op.add_column("doc_snapshot", sa.Column("data_seq", sa.Integer(), nullable=True))
    op.add_column("doc_snapshot", sa.Column("footprint", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("doc_snapshot", "footprint")
    op.drop_column("doc_snapshot", "data_seq")
    op.drop_column("doc_snapshot", "base")
    op.drop_index("ix_data_change_seq", table_name="data_change")
    op.drop_index("ix_data_change_tenant_id", table_name="data_change")
    op.drop_table("data_change")
    op.drop_index("ix_data_state_tenant_id", table_name="data_state")
    op.drop_table("data_state")
