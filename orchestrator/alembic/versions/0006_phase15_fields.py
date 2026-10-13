"""Phase 1.5-Felder: rule.protections, nat_rule.nat_type, network_object.color

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-13

Additiv (nullable bzw. mit server_default), damit bestehende Zeilen gueltig bleiben.
- rule.protections: FortiGate-UTM-Profile ("Schutzmechanismen").
- nat_rule.nat_type: dnat_vip | snat_pool (SNAT-IP-Pools).
- network_object.color: FortiGate-Farbcode (Signal).
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rule", sa.Column("protections", sa.JSON(), nullable=True))
    op.add_column(
        "nat_rule",
        sa.Column("nat_type", sa.String(length=16), nullable=False, server_default="dnat_vip"),
    )
    op.add_column("network_object", sa.Column("color", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("network_object", "color")
    op.drop_column("nat_rule", "nat_type")
    op.drop_column("rule", "protections")
