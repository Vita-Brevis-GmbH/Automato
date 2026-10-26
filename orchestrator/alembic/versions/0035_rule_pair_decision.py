"""IPv4/IPv6-Paare bestaetigen bzw. ablehnen (0.12.0)

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rule_pair_decision",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column(
            "source_system_id",
            sa.Integer(),
            sa.ForeignKey("source_system.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("v4_external_id", sa.String(200), nullable=False),
        sa.Column("v6_external_id", sa.String(200), nullable=False),
        sa.Column("decision", sa.String(12), nullable=False),
        sa.Column("decided_by", sa.String(200), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_system_id", "v4_external_id", "v6_external_id"),
    )
    op.create_index("ix_rule_pair_decision_tenant_id", "rule_pair_decision", ["tenant_id"])
    op.create_index(
        "ix_rule_pair_decision_source_system_id", "rule_pair_decision", ["source_system_id"]
    )
    with op.batch_alter_table("rule_index") as batch:
        batch.alter_column(
            "pair_match", existing_type=sa.String(8), type_=sa.String(12), existing_nullable=True
        )


def downgrade() -> None:
    op.execute("UPDATE rule_index SET pair_match = 'similar' WHERE pair_match = 'confirmed'")
    with op.batch_alter_table("rule_index") as batch:
        batch.alter_column(
            "pair_match", existing_type=sa.String(12), type_=sa.String(8), existing_nullable=True
        )
    op.drop_index("ix_rule_pair_decision_source_system_id", table_name="rule_pair_decision")
    op.drop_index("ix_rule_pair_decision_tenant_id", table_name="rule_pair_decision")
    op.drop_table("rule_pair_decision")
