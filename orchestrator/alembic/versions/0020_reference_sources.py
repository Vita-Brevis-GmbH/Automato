"""Referenzquellen (Infoblox/NetBox): Referenz-Eintraege, Rolle, Abgleich-Links (ADR 0010)

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-20

`reference_record`: komplett gelesener Stand je Referenzquelle und Lauf. `source_system`
bekommt `ref_role` (primary | enrichment) und `reference_run_id` (aktueller Stand),
`network_object` die Verknuepfung zum besten Treffer (`reference_id`, `enrichment_id`).
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reference_record",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("source_system_id", sa.Integer(), nullable=False),
        sa.Column("collector_run_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("value", sa.String(length=200), nullable=False),
        sa.Column("prefix_len", sa.Integer(), nullable=True),
        sa.Column("vrf", sa.String(length=200), nullable=True),
        sa.Column("name", sa.String(length=300), nullable=True),
        sa.Column("dns_name", sa.String(length=300), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("vlan_vid", sa.Integer(), nullable=True),
        sa.Column("vlan_name", sa.String(length=200), nullable=True),
        sa.Column("site", sa.String(length=200), nullable=True),
        sa.Column("tenant_name", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("attrs", sa.JSON(), nullable=True),
        sa.Column("ref_id", sa.String(length=300), nullable=True),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_reference_record_tenant"),
        sa.ForeignKeyConstraint(
            ["source_system_id"], ["source_system.id"], name="fk_reference_record_source_system"
        ),
        sa.ForeignKeyConstraint(
            ["collector_run_id"],
            ["collector_run.id"],
            name="fk_reference_record_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for col in ("tenant_id", "source_system_id", "collector_run_id", "value", "dns_name"):
        op.create_index(f"ix_reference_record_{col}", "reference_record", [col])
    with op.batch_alter_table("source_system") as batch:
        batch.add_column(sa.Column("ref_role", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("reference_run_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_source_system_reference_run",
            "collector_run",
            ["reference_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
    with op.batch_alter_table("network_object") as batch:
        batch.add_column(sa.Column("reference_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("reference_prefix_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("enrichment_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_network_object_reference",
            "reference_record",
            ["reference_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_network_object_reference_prefix",
            "reference_record",
            ["reference_prefix_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_network_object_enrichment",
            "reference_record",
            ["enrichment_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("network_object") as batch:
        batch.drop_constraint("fk_network_object_enrichment", type_="foreignkey")
        batch.drop_constraint("fk_network_object_reference_prefix", type_="foreignkey")
        batch.drop_constraint("fk_network_object_reference", type_="foreignkey")
        batch.drop_column("enrichment_id")
        batch.drop_column("reference_prefix_id")
        batch.drop_column("reference_id")
    with op.batch_alter_table("source_system") as batch:
        batch.drop_constraint("fk_source_system_reference_run", type_="foreignkey")
        batch.drop_column("reference_run_id")
        batch.drop_column("ref_role")
    for col in ("dns_name", "value", "collector_run_id", "source_system_id", "tenant_id"):
        op.drop_index(f"ix_reference_record_{col}", table_name="reference_record")
    op.drop_table("reference_record")
