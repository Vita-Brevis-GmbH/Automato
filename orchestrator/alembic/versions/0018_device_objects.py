"""Firewall-Objekte mit Namen + Regel-Verweise; Findings je Quellsystem (ADR 0010)

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-20

`device_object`: Objekte wie auf dem Geraet (Adressen, Gruppen, VIPs, IP-Pools, Services,
Service-Gruppen). `rule_object_ref`: Verweise einer Regel per Objektname. `finding` bekommt
`source_system_id` und ein breiteres `subject_id` (Objektnamen bis 300 Zeichen).
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_object",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("source_system_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("obj_type", sa.String(length=64), nullable=True),
        sa.Column("family", sa.String(length=4), nullable=True),
        sa.Column("values", sa.JSON(), nullable=True),
        sa.Column("members", sa.JSON(), nullable=True),
        sa.Column("fqdn", sa.String(length=300), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("color", sa.Integer(), nullable=True),
        sa.Column("interface", sa.String(length=100), nullable=True),
        sa.Column("network_object_id", sa.Integer(), nullable=True),
        sa.Column("collector_run_id", sa.String(length=36), nullable=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_device_object_tenant"),
        sa.ForeignKeyConstraint(
            ["source_system_id"], ["source_system.id"], name="fk_device_object_source_system"
        ),
        sa.ForeignKeyConstraint(
            ["network_object_id"],
            ["network_object.id"],
            name="fk_device_object_network_object",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["collector_run_id"], ["collector_run.id"], name="fk_device_object_run"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_system_id", "category", "name", name="uq_device_object_source_cat_name"
        ),
    )
    op.create_index("ix_device_object_tenant_id", "device_object", ["tenant_id"])
    op.create_index("ix_device_object_source_system_id", "device_object", ["source_system_id"])
    op.create_index("ix_device_object_network_object_id", "device_object", ["network_object_id"])

    op.create_table(
        "rule_object_ref",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=8), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("device_object_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["rule_id"], ["rule.id"], name="fk_rule_object_ref_rule", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["device_object_id"],
            ["device_object.id"],
            name="fk_rule_object_ref_device_object",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rule_object_ref_rule_id", "rule_object_ref", ["rule_id"])
    op.create_index(
        "ix_rule_object_ref_device_object_id", "rule_object_ref", ["device_object_id"]
    )

    with op.batch_alter_table("finding") as batch:
        batch.alter_column(
            "subject_id", existing_type=sa.String(length=64), type_=sa.String(length=300)
        )
        batch.add_column(sa.Column("source_system_id", sa.Integer(), nullable=True))
        batch.create_index("ix_finding_source_system_id", ["source_system_id"])


def downgrade() -> None:
    with op.batch_alter_table("finding") as batch:
        batch.drop_index("ix_finding_source_system_id")
        batch.drop_column("source_system_id")
        batch.alter_column(
            "subject_id", existing_type=sa.String(length=300), type_=sa.String(length=64)
        )
    op.drop_index("ix_rule_object_ref_device_object_id", table_name="rule_object_ref")
    op.drop_index("ix_rule_object_ref_rule_id", table_name="rule_object_ref")
    op.drop_table("rule_object_ref")
    op.drop_index("ix_device_object_network_object_id", table_name="device_object")
    op.drop_index("ix_device_object_source_system_id", table_name="device_object")
    op.drop_index("ix_device_object_tenant_id", table_name="device_object")
    op.drop_table("device_object")
