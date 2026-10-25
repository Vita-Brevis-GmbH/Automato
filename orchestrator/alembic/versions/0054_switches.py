"""Switches und Verkabelung (0.62.0, ADR 0025)

source_system.read_scope als Text (Adressen vieler Switches), source_system.ssh_host_keys
(festgehaltene SSH-Host-Schluessel je Adresse); inv_interface mit Betriebszustand, Speed,
Duplex, Native VLAN, erlaubten VLANs des Trunks und weiteren Merkmalen (`attrs`); inv_neighbor
= Nachbarn aus CDP/LLDP je Port.

Revision ID: 0054
Revises: 0053
Create Date: 2026-10-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None

_INTERFACE = (
    ("status", sa.String(length=32)),
    ("speed", sa.String(length=32)),
    ("duplex", sa.String(length=16)),
    ("native_vlan", sa.Integer()),
    ("trunk_vlans", sa.Text()),
    ("attrs", sa.JSON()),
)


def upgrade() -> None:
    with op.batch_alter_table("source_system") as batch:
        batch.alter_column("read_scope", existing_type=sa.String(length=500), type_=sa.Text(),
                           existing_nullable=False, existing_server_default="")  # fmt: skip
        batch.add_column(sa.Column("ssh_host_keys", sa.JSON(), nullable=True))
    with op.batch_alter_table("inv_interface") as batch:
        for name, kind in _INTERFACE:
            batch.add_column(sa.Column(name, kind, nullable=True))
    op.create_table(
        "inv_neighbor",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("source_system_id", sa.Integer(), nullable=False),
        sa.Column("device", sa.String(length=200), nullable=False),
        sa.Column("port", sa.String(length=200), nullable=False),
        sa.Column("protocol", sa.String(length=8), nullable=False),
        sa.Column("remote_name", sa.String(length=300), nullable=False),
        sa.Column("remote_port", sa.String(length=200), nullable=True),
        sa.Column("remote_ip", sa.String(length=64), nullable=True),
        sa.Column("remote_platform", sa.String(length=200), nullable=True),
        sa.Column("remote_caps", sa.JSON(), nullable=True),
        sa.Column("remote_description", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_inv_neighbor_tenant"),
        sa.ForeignKeyConstraint(
            ["source_system_id"], ["source_system.id"], name="fk_inv_neighbor_source_system"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inv_neighbor_tenant_id", "inv_neighbor", ["tenant_id"])
    op.create_index("ix_inv_neighbor_source_system_id", "inv_neighbor", ["source_system_id"])


def downgrade() -> None:
    op.drop_index("ix_inv_neighbor_source_system_id", table_name="inv_neighbor")
    op.drop_index("ix_inv_neighbor_tenant_id", table_name="inv_neighbor")
    op.drop_table("inv_neighbor")
    with op.batch_alter_table("inv_interface") as batch:
        for name, _ in reversed(_INTERFACE):
            batch.drop_column(name)
    with op.batch_alter_table("source_system") as batch:
        batch.drop_column("ssh_host_keys")
        batch.alter_column("read_scope", existing_type=sa.Text(), type_=sa.String(length=500),
                           existing_nullable=False, existing_server_default="")  # fmt: skip
