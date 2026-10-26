"""epg + epg_subnet, rule.aci_scope, rule_endpoint.epg_id (ACI Phase 2)

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-14

EPG-Tabellen (Model 1): `epg` (Server=EPG, AP-Klammer, BD), `epg_subnet` (fvSubnet,
dual-stack). Regeln tragen den Contract-`scope` (Applikations-Doku-Kategorie), EPG-
Endpunkte referenzieren die EPG (`rule_endpoint.epg_id`). Additiv.
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "epg",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("ap", sa.String(length=100), nullable=True),
        sa.Column("bd", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_epg_tenant"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_epg_tenant_name"),
    )
    op.create_index("ix_epg_tenant_id", "epg", ["tenant_id"])
    op.create_index("ix_epg_ap", "epg", ["ap"])
    op.create_table(
        "epg_subnet",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("epg_id", sa.Integer(), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.Column("family", sa.String(length=4), nullable=True),
        sa.ForeignKeyConstraint(["epg_id"], ["epg.id"], name="fk_epg_subnet_epg"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_epg_subnet_epg_id", "epg_subnet", ["epg_id"])
    op.add_column("rule", sa.Column("aci_scope", sa.String(length=32), nullable=True))
    with op.batch_alter_table("rule_endpoint") as batch:
        batch.add_column(sa.Column("epg_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_rule_endpoint_epg", "epg", ["epg_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("rule_endpoint") as batch:
        batch.drop_constraint("fk_rule_endpoint_epg", type_="foreignkey")
        batch.drop_column("epg_id")
    op.drop_column("rule", "aci_scope")
    op.drop_index("ix_epg_subnet_epg_id", table_name="epg_subnet")
    op.drop_table("epg_subnet")
    op.drop_index("ix_epg_ap", table_name="epg")
    op.drop_index("ix_epg_tenant_id", table_name="epg")
    op.drop_table("epg")
