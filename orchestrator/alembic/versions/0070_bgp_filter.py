"""BGP-Konfiguration und Route Control lesen (0.113.0, ADR 0041)

Anwender 2026-10-29: Backup-Wege, die die Filter der Firewalls davor nie zulassen - die
BGP-Filter (Prefix-Listen, Route-Maps) der FortiGate und die Route Control der ACI-L3Outs
werden strikt lesend eingelesen: `topo_bgp_object` je VDOM, `topo_l3out.route_control`.

Revision ID: 0070
Revises: 0069
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "topo_bgp_object",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "source_system_id", sa.Integer(), sa.ForeignKey("source_system.id"), nullable=False
        ),
        sa.Column("node", sa.String(200), nullable=False),
        sa.Column("context", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("family", sa.SmallInteger(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
    )
    op.create_index("ix_topo_bgp_object_tenant_id", "topo_bgp_object", ["tenant_id"])
    op.create_index(
        "ix_topo_bgp_object_source_system_id", "topo_bgp_object", ["source_system_id"]
    )
    op.add_column("topo_l3out", sa.Column("route_control", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("topo_l3out", "route_control")
    op.drop_index("ix_topo_bgp_object_source_system_id", table_name="topo_bgp_object")
    op.drop_index("ix_topo_bgp_object_tenant_id", table_name="topo_bgp_object")
    op.drop_table("topo_bgp_object")
