"""Netzinventar je Quellsystem: Geraete, Ports, VLANs, Subnetze, VRFs (ADR 0010)

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-20

Additiv. Je Lauf eines Quellsystems ersetzt; Quelle fuer die Ablage in NetBox.
"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inv_device",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("source_system_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("serial", sa.String(length=100), nullable=True),
        sa.Column("node_id", sa.String(length=32), nullable=True),
        sa.Column("mgmt_ip", sa.String(length=64), nullable=True),
        sa.Column("version", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_inv_device_tenant"),
        sa.ForeignKeyConstraint(
            ["source_system_id"], ["source_system.id"], name="fk_inv_device_source_system"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inv_device_tenant_id", "inv_device", ["tenant_id"])
    op.create_index("ix_inv_device_source_system_id", "inv_device", ["source_system_id"])
    op.create_table(
        "inv_interface",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("source_system_id", sa.Integer(), nullable=False),
        sa.Column("device", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=True),
        sa.Column("mode", sa.String(length=16), nullable=True),
        sa.Column("untagged_vlan", sa.Integer(), nullable=True),
        sa.Column("tagged_vlans", sa.JSON(), nullable=True),
        sa.Column("lag", sa.String(length=200), nullable=True),
        sa.Column("parent", sa.String(length=200), nullable=True),
        sa.Column("ips", sa.JSON(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("zone", sa.String(length=100), nullable=True),
        sa.Column("neighbor", sa.String(length=200), nullable=True),
        sa.Column("neighbor_port", sa.String(length=100), nullable=True),
        sa.Column("policy_group", sa.String(length=200), nullable=True),
        sa.Column("epgs", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_inv_interface_tenant"),
        sa.ForeignKeyConstraint(
            ["source_system_id"], ["source_system.id"], name="fk_inv_interface_source_system"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inv_interface_tenant_id", "inv_interface", ["tenant_id"])
    op.create_index("ix_inv_interface_source_system_id", "inv_interface", ["source_system_id"])
    op.create_table(
        "inv_vlan",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("source_system_id", sa.Integer(), nullable=False),
        sa.Column("vid", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("group", sa.String(length=200), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("epgs", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_inv_vlan_tenant"),
        sa.ForeignKeyConstraint(
            ["source_system_id"], ["source_system.id"], name="fk_inv_vlan_source_system"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inv_vlan_tenant_id", "inv_vlan", ["tenant_id"])
    op.create_index("ix_inv_vlan_source_system_id", "inv_vlan", ["source_system_id"])
    op.create_table(
        "inv_subnet",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("source_system_id", sa.Integer(), nullable=False),
        sa.Column("prefix", sa.String(length=64), nullable=False),
        sa.Column("gateway", sa.String(length=64), nullable=True),
        sa.Column("vrf", sa.String(length=200), nullable=True),
        sa.Column("vlan", sa.Integer(), nullable=True),
        sa.Column("context", sa.String(length=16), nullable=False),
        sa.Column("context_name", sa.String(length=200), nullable=False),
        sa.Column("aci_tenant", sa.String(length=100), nullable=True),
        sa.Column("ap", sa.String(length=100), nullable=True),
        sa.Column("epg", sa.String(length=200), nullable=True),
        sa.Column("bd", sa.String(length=100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_inv_subnet_tenant"),
        sa.ForeignKeyConstraint(
            ["source_system_id"], ["source_system.id"], name="fk_inv_subnet_source_system"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inv_subnet_tenant_id", "inv_subnet", ["tenant_id"])
    op.create_index("ix_inv_subnet_source_system_id", "inv_subnet", ["source_system_id"])
    op.create_index("ix_inv_subnet_prefix", "inv_subnet", ["prefix"])
    op.create_table(
        "inv_vrf",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("source_system_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_inv_vrf_tenant"),
        sa.ForeignKeyConstraint(
            ["source_system_id"], ["source_system.id"], name="fk_inv_vrf_source_system"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inv_vrf_tenant_id", "inv_vrf", ["tenant_id"])
    op.create_index("ix_inv_vrf_source_system_id", "inv_vrf", ["source_system_id"])


def downgrade() -> None:
    op.drop_index("ix_inv_vrf_source_system_id", table_name="inv_vrf")
    op.drop_index("ix_inv_vrf_tenant_id", table_name="inv_vrf")
    op.drop_table("inv_vrf")
    op.drop_index("ix_inv_subnet_prefix", table_name="inv_subnet")
    op.drop_index("ix_inv_subnet_source_system_id", table_name="inv_subnet")
    op.drop_index("ix_inv_subnet_tenant_id", table_name="inv_subnet")
    op.drop_table("inv_subnet")
    op.drop_index("ix_inv_vlan_source_system_id", table_name="inv_vlan")
    op.drop_index("ix_inv_vlan_tenant_id", table_name="inv_vlan")
    op.drop_table("inv_vlan")
    op.drop_index("ix_inv_interface_source_system_id", table_name="inv_interface")
    op.drop_index("ix_inv_interface_tenant_id", table_name="inv_interface")
    op.drop_table("inv_interface")
    op.drop_index("ix_inv_device_source_system_id", table_name="inv_device")
    op.drop_index("ix_inv_device_tenant_id", table_name="inv_device")
    op.drop_table("inv_device")
