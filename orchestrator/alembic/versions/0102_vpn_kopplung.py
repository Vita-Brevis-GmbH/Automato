"""VPN-Tunnel koppeln: Kopplung zweier IPsec-Tunnel von Hand (1.23.0, ADR 0063 Nachtrag 1.23.0)

Anwender 2026-11-20: „Einseitig ist es dynamisch, kann man dies nicht koppeln? Manuell, wenn es
dynamisch nicht geht?“

- `vpn_pair`: je Mandant zwei Tunnel-Enden (Quellsystem, VDOM, Tunnel) mit `mode` = `coupled`
  (von Hand gekoppelt) bzw. `separated` (automatische Kopplung von Hand geloest); Laufzeitdaten,
  nicht je Lauf ersetzt.
- Rueckweg: Tabelle entfernen.

Revision ID: 0102_vpn_kopplung
Revises: 0099_lldp_dns_zonen
Create Date: 2026-11-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0102_vpn_kopplung"
down_revision = "0099_lldp_dns_zonen"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vpn_pair",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("a_system_id", sa.Integer(), sa.ForeignKey("source_system.id"), nullable=False),
        sa.Column("a_context", sa.String(200), nullable=False),
        sa.Column("a_tunnel", sa.String(200), nullable=False),
        sa.Column("b_system_id", sa.Integer(), sa.ForeignKey("source_system.id"), nullable=False),
        sa.Column("b_context", sa.String(200), nullable=False),
        sa.Column("b_tunnel", sa.String(200), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("changed_by", sa.String(200), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "a_system_id", "a_context", "a_tunnel", "b_system_id",
                            "b_context", "b_tunnel", name="uq_vpn_pair_ends"),
    )  # fmt: skip
    op.create_index("ix_vpn_pair_tenant_id", "vpn_pair", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_vpn_pair_tenant_id", table_name="vpn_pair")
    op.drop_table("vpn_pair")
