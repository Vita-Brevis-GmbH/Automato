"""Spanning Tree der Switches (1.19.0)

Anwender 2026-11-17 (Prioritaet 5 „Spanningtree“): je Switch Modus, Lesestatus und err-disabled
Ports (`stp_device`), je Switch und VLAN bzw. MST-Instanz Root, Bridge, Ports und
Topologieaenderungen (`stp_instance`, je Lauf ersetzt, Verlauf der Zaehler mitgenommen); Soll-Root
je VLAN-Bereich bzw. Standort (`stp_target`) und die Schwelle der Topologieaenderungen
(`stp_setting`) als Laufzeitdaten je Mandant.

Revision ID: 0091_stp
Revises: 0088_automato_instanz
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0091_stp"
down_revision = "0088_automato_instanz"
branch_labels = None
depends_on = None


def _row() -> list:
    return [
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("source_system_id", sa.Integer(), nullable=False),
        sa.Column("device", sa.String(length=200), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_system_id"], ["source_system.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    ]


def upgrade() -> None:
    op.create_table(
        "stp_device",
        *_row(),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=True),
        sa.Column("region", sa.String(length=200), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=True),
        sa.Column("errdisabled", sa.JSON(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
    )
    op.create_index("ix_stp_device_tenant_id", "stp_device", ["tenant_id"])
    op.create_index("ix_stp_device_source_system_id", "stp_device", ["source_system_id"])
    op.create_table(
        "stp_instance",
        *_row(),
        sa.Column("instance", sa.String(length=32), nullable=False),
        sa.Column("vlan", sa.Integer(), nullable=True),
        sa.Column("mst", sa.Integer(), nullable=True),
        sa.Column("vlans", sa.Text(), nullable=True),
        sa.Column("protocol", sa.String(length=16), nullable=True),
        sa.Column("root_priority", sa.Integer(), nullable=True),
        sa.Column("root_mac", sa.String(length=17), nullable=True),
        sa.Column("root_cost", sa.Integer(), nullable=True),
        sa.Column("root_port", sa.String(length=200), nullable=True),
        sa.Column("is_root", sa.Boolean(), nullable=False),
        sa.Column("bridge_priority", sa.Integer(), nullable=True),
        sa.Column("bridge_mac", sa.String(length=17), nullable=True),
        sa.Column("tc_count", sa.Integer(), nullable=True),
        sa.Column("tc_last_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tc_from", sa.String(length=200), nullable=True),
        sa.Column("tc_history", sa.JSON(), nullable=True),
        sa.Column("ports", sa.JSON(), nullable=True),
    )
    op.create_index("ix_stp_instance_tenant_id", "stp_instance", ["tenant_id"])
    op.create_index("ix_stp_instance_source_system_id", "stp_instance", ["source_system_id"])
    op.create_index("ix_stp_instance_tenant_vlan", "stp_instance", ["tenant_id", "vlan"])
    op.create_table(
        "stp_target",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=True),
        sa.Column("vlans", sa.String(length=500), nullable=True),
        sa.Column("root", sa.String(length=200), nullable=False),
        sa.Column("backup", sa.String(length=200), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("changed_by", sa.String(length=200), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stp_target_tenant_id", "stp_target", ["tenant_id"])
    op.create_table(
        "stp_setting",
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("tc_per_day", sa.Integer(), nullable=False),
        sa.Column("changed_by", sa.String(length=200), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id"),
    )


def downgrade() -> None:
    op.drop_table("stp_setting")
    op.drop_index("ix_stp_target_tenant_id", table_name="stp_target")
    op.drop_table("stp_target")
    for name in ("ix_stp_instance_tenant_vlan", "ix_stp_instance_source_system_id",
                 "ix_stp_instance_tenant_id"):  # fmt: skip
        op.drop_index(name, table_name="stp_instance")
    op.drop_table("stp_instance")
    for name in ("ix_stp_device_source_system_id", "ix_stp_device_tenant_id"):
        op.drop_index(name, table_name="stp_device")
    op.drop_table("stp_device")
