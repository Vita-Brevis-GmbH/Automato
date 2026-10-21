"""IPv4/IPv6 getrennt + abgeleiteter Regelindex (Dev-Befunde 0.9.0)

- device_object: eindeutig je Quellsystem/Kategorie/Familie/Name (FortiGate: gleiche Namen in
  address und address6 sind verschiedene Objekte)
- rule.ip_family, rule_object_ref.family
- rule_index / rule_object_usage / rule_ip_range: Suchtext, Listenzeile, Objekt-Nutzung (auch
  ueber Gruppen) und IP-Bereiche je Regel - wird beim naechsten Lauf je Quellsystem aufgebaut

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-21
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

IP_HEX = sa.String(32).with_variant(postgresql.VARCHAR(32, collation="C"), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("device_object") as batch:
        batch.drop_constraint("uq_device_object_source_cat_name", type_="unique")
        batch.create_unique_constraint(
            "uq_device_object_source_cat_fam_name",
            ["source_system_id", "category", "family", "name"],
        )
    with op.batch_alter_table("rule") as batch:
        batch.add_column(sa.Column("ip_family", sa.String(4), nullable=True))
    with op.batch_alter_table("rule_object_ref") as batch:
        batch.add_column(sa.Column("family", sa.String(4), nullable=True))

    op.create_table(
        "rule_index",
        sa.Column(
            "rule_id",
            sa.Integer(),
            sa.ForeignKey("rule.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("source_system_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("names", sa.Text(), nullable=False),
        sa.Column("nat", sa.Text(), nullable=False),
        sa.Column("row", sa.JSON(), nullable=True),
        sa.Column("pair_rule_id", sa.Integer(), nullable=True),
        sa.Column("pair_match", sa.String(8), nullable=True),
        sa.Column("pair_diff", sa.JSON(), nullable=True),
    )
    op.create_index("ix_rule_index_tenant_id", "rule_index", ["tenant_id"])
    op.create_index("ix_rule_index_source_system_id", "rule_index", ["source_system_id"])

    op.create_table(
        "rule_object_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "rule_id", sa.Integer(), sa.ForeignKey("rule.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "device_object_id",
            sa.Integer(),
            sa.ForeignKey("device_object.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_system_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(8), nullable=False),
        sa.Column("direct", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_rule_object_usage_rule_id", "rule_object_usage", ["rule_id"])
    op.create_index(
        "ix_rule_object_usage_device_object_id", "rule_object_usage", ["device_object_id"]
    )
    op.create_index(
        "ix_rule_object_usage_source_system_id", "rule_object_usage", ["source_system_id"]
    )

    op.create_table(
        "rule_ip_range",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "rule_id", sa.Integer(), sa.ForeignKey("rule.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("source_system_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(4), nullable=False),
        sa.Column("family", sa.SmallInteger(), nullable=False),
        sa.Column("lo", IP_HEX, nullable=False),
        sa.Column("hi", IP_HEX, nullable=False),
        sa.Column("is_any", sa.Boolean(), nullable=False),
        sa.Column("via", sa.String(300), nullable=True),
    )
    op.create_index("ix_rule_ip_range_rule_id", "rule_ip_range", ["rule_id"])
    op.create_index("ix_rule_ip_range_source_system_id", "rule_ip_range", ["source_system_id"])
    op.create_index("ix_rule_ip_range_lookup", "rule_ip_range", ["tenant_id", "family", "lo"])


def downgrade() -> None:
    op.drop_table("rule_ip_range")
    op.drop_table("rule_object_usage")
    op.drop_table("rule_index")
    with op.batch_alter_table("rule_object_ref") as batch:
        batch.drop_column("family")
    with op.batch_alter_table("rule") as batch:
        batch.drop_column("ip_family")
    # Gleichnamige IPv6-Objekte entfallen (das alte Schema kennt je Name nur ein Objekt);
    # der naechste Lauf liest sie wieder ein
    op.execute(
        "DELETE FROM device_object WHERE family = 'v6' AND EXISTS ("
        "SELECT 1 FROM device_object d4 WHERE d4.source_system_id = device_object.source_system_id"
        " AND d4.category = device_object.category AND d4.name = device_object.name"
        " AND d4.id <> device_object.id)"
    )
    with op.batch_alter_table("device_object") as batch:
        batch.drop_constraint("uq_device_object_source_cat_fam_name", type_="unique")
        batch.create_unique_constraint(
            "uq_device_object_source_cat_name", ["source_system_id", "category", "name"]
        )
