"""IPsec-Tunnel der FortiGates lesen (1.19.0, ADR 0063)

Anwender 2026-11-17: „VPN integrieren“ - „IPsec reicht, ssl-vpn wird nicht mehr unterstützt“.
Die Tunnel im Interface-Modus werden strikt lesend mit dem Routing eingelesen und je Lauf ersetzt:
`topo_vpn_tunnel` je FortiGate und VDOM mit Phase 1, Phase 2 (Selektoren, Zustand) und bei
Dial-up den verbundenen Gegenstellen.

Revision ID: 0089_vpn
Revises: 0088_automato_instanz
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0089_vpn"
down_revision = "0088_automato_instanz"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "topo_vpn_tunnel",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "source_system_id", sa.Integer(), sa.ForeignKey("source_system.id"), nullable=False
        ),
        sa.Column("node", sa.String(200), nullable=False),
        sa.Column("context", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("interface", sa.String(200), nullable=True),
        sa.Column("remote_gw", sa.String(255), nullable=True),
        sa.Column("local_gw", sa.String(64), nullable=True),
        sa.Column("ike_version", sa.SmallInteger(), nullable=True),
        sa.Column("status", sa.String(8), nullable=True),
        sa.Column("phase2", sa.JSON(), nullable=True),
        sa.Column("instances", sa.JSON(), nullable=True),
        sa.Column("attrs", sa.JSON(), nullable=True),
    )
    op.create_index("ix_topo_vpn_tunnel_tenant_id", "topo_vpn_tunnel", ["tenant_id"])
    op.create_index(
        "ix_topo_vpn_tunnel_source_system_id", "topo_vpn_tunnel", ["source_system_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_topo_vpn_tunnel_source_system_id", table_name="topo_vpn_tunnel")
    op.drop_index("ix_topo_vpn_tunnel_tenant_id", table_name="topo_vpn_tunnel")
    op.drop_table("topo_vpn_tunnel")
