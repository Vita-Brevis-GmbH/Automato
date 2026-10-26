"""VMware-Inventar: VMs, NICs, IPs, Portgruppen (ADR 0017, 0.11.0)

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vm",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "source_system_id", sa.Integer(), sa.ForeignKey("source_system.id"), nullable=False
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("uuid", sa.String(64), nullable=True),
        sa.Column("power_state", sa.String(16), nullable=True),
        sa.Column("template", sa.Boolean(), nullable=False),
        sa.Column("guest_os", sa.String(200), nullable=True),
        sa.Column("hostname", sa.String(255), nullable=True),
        sa.Column("datacenter", sa.String(100), nullable=True),
        sa.Column("cluster", sa.String(100), nullable=True),
        sa.Column("host", sa.String(255), nullable=True),
        sa.Column("vcpus", sa.Integer(), nullable=True),
        sa.Column("memory_mb", sa.Integer(), nullable=True),
        sa.Column("disk_gb", sa.Float(), nullable=True),
        sa.Column("annotation", sa.Text(), nullable=True),
        sa.Column(
            "collector_run_id", sa.String(36), sa.ForeignKey("collector_run.id"), nullable=True
        ),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_vm_tenant_id", "vm", ["tenant_id"])
    op.create_index("ix_vm_source_system_id", "vm", ["source_system_id"])
    op.create_table(
        "vm_nic",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "vm_id", sa.Integer(), sa.ForeignKey("vm.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("mac", sa.String(32), nullable=True),
        sa.Column("portgroup", sa.String(255), nullable=True),
        sa.Column("switch", sa.String(100), nullable=True),
        sa.Column("vlan", sa.Integer(), nullable=True),
        sa.Column("connected", sa.Boolean(), nullable=True),
        sa.Column("ips", sa.JSON(), nullable=True),
    )
    op.create_index("ix_vm_nic_vm_id", "vm_nic", ["vm_id"])
    op.create_table(
        "vm_ip",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column(
            "vm_id", sa.Integer(), sa.ForeignKey("vm.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "nic_id", sa.Integer(), sa.ForeignKey("vm_nic.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("ip", sa.String(64), nullable=False),
    )
    op.create_index("ix_vm_ip_tenant_id", "vm_ip", ["tenant_id"])
    op.create_index("ix_vm_ip_vm_id", "vm_ip", ["vm_id"])
    op.create_index("ix_vm_ip_ip", "vm_ip", ["ip"])
    op.create_table(
        "portgroup",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "source_system_id", sa.Integer(), sa.ForeignKey("source_system.id"), nullable=False
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("switch", sa.String(100), nullable=True),
        sa.Column("key", sa.String(64), nullable=True),
        sa.Column("vlan", sa.Integer(), nullable=True),
        sa.Column("vlan_ranges", sa.String(200), nullable=True),
        sa.Column("vm_count", sa.Integer(), nullable=False),
        sa.Column(
            "epg_id", sa.Integer(), sa.ForeignKey("epg.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("epg_match", sa.String(8), nullable=True),
    )
    op.create_index("ix_portgroup_tenant_id", "portgroup", ["tenant_id"])
    op.create_index("ix_portgroup_source_system_id", "portgroup", ["source_system_id"])
    op.create_index("ix_portgroup_epg_id", "portgroup", ["epg_id"])


def downgrade() -> None:
    op.drop_table("portgroup")
    op.drop_table("vm_ip")
    op.drop_table("vm_nic")
    op.drop_table("vm")
