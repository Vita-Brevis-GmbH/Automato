"""Endpunkte der Switches (0.63.0, ADR 0025 Nr. 8)

mac_entry (MAC-Adress-Tabelle: MAC, VLAN, Port je Switch) und arp_entry (ARP: IP -> MAC) - gesammelt
mit first_seen/last_seen statt je Lauf ersetzt; alte Eintraege loescht die Aufbewahrung.

Revision ID: 0055
Revises: 0054
Create Date: 2026-10-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def _base(table: str) -> list:
    return [
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("source_system_id", sa.Integer(), nullable=False),
        sa.Column("device", sa.String(length=200), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name=f"fk_{table}_tenant"),
        sa.ForeignKeyConstraint(
            ["source_system_id"], ["source_system.id"], name=f"fk_{table}_source_system"
        ),
        sa.PrimaryKeyConstraint("id"),
    ]


def _seen() -> list:
    return [
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "mac_entry",
        *_base("mac_entry"),
        sa.Column("mac", sa.String(length=17), nullable=False),
        sa.Column("vlan", sa.Integer(), nullable=True),
        sa.Column("port", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=True),
        *_seen(),
    )
    op.create_index("ix_mac_entry_tenant_id", "mac_entry", ["tenant_id"])
    op.create_index("ix_mac_entry_source_system_id", "mac_entry", ["source_system_id"])
    op.create_index("ix_mac_entry_tenant_mac", "mac_entry", ["tenant_id", "mac"])
    op.create_table(
        "arp_entry",
        *_base("arp_entry"),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.Column("mac", sa.String(length=17), nullable=False),
        sa.Column("interface", sa.String(length=200), nullable=True),
        sa.Column("vrf", sa.String(length=100), nullable=True),
        *_seen(),
    )
    op.create_index("ix_arp_entry_tenant_id", "arp_entry", ["tenant_id"])
    op.create_index("ix_arp_entry_source_system_id", "arp_entry", ["source_system_id"])
    op.create_index("ix_arp_entry_tenant_ip", "arp_entry", ["tenant_id", "ip"])
    op.create_index("ix_arp_entry_tenant_mac", "arp_entry", ["tenant_id", "mac"])


def downgrade() -> None:
    for name in ("ix_arp_entry_tenant_mac", "ix_arp_entry_tenant_ip",
                 "ix_arp_entry_source_system_id", "ix_arp_entry_tenant_id"):  # fmt: skip
        op.drop_index(name, table_name="arp_entry")
    op.drop_table("arp_entry")
    for name in ("ix_mac_entry_tenant_mac", "ix_mac_entry_source_system_id",
                 "ix_mac_entry_tenant_id"):  # fmt: skip
        op.drop_index(name, table_name="mac_entry")
    op.drop_table("mac_entry")
