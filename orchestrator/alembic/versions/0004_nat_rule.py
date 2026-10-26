"""nat_rule-Tabelle (VIP/DNAT, ADR 0004 Phase 1.5)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-13

Dokumentiert eingehende Virtual IPs / DNAT der FortiGate: externe IP (+ Port) ->
internes Ziel (+ Port). Rein lesend; wird je Quellsystem bei jedem Lauf ersetzt.
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nat_rule",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("source_system_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("ext_ip", sa.String(length=200), nullable=True),
        sa.Column("ext_port", sa.String(length=64), nullable=True),
        sa.Column("mapped_ip", sa.String(length=200), nullable=True),
        sa.Column("mapped_port", sa.String(length=64), nullable=True),
        sa.Column("protocol", sa.String(length=16), nullable=True),
        sa.Column("portforward", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("collector_run_id", sa.String(length=36), nullable=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_nat_rule_tenant"),
        sa.ForeignKeyConstraint(
            ["source_system_id"], ["source_system.id"], name="fk_nat_rule_source_system"
        ),
        sa.ForeignKeyConstraint(
            ["collector_run_id"], ["collector_run.id"], name="fk_nat_rule_run"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_nat_rule_tenant_id", "nat_rule", ["tenant_id"])
    op.create_index("ix_nat_rule_source_system_id", "nat_rule", ["source_system_id"])


def downgrade() -> None:
    op.drop_index("ix_nat_rule_source_system_id", table_name="nat_rule")
    op.drop_index("ix_nat_rule_tenant_id", table_name="nat_rule")
    op.drop_table("nat_rule")
