"""Datenmodell UC1 Phase 1 (ADR 0004): Mandant, Zonen, Objekte, Services, Regeln, Findings

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-22

Phase 1 (Model 2 / FortiGate). ACI-Tabellen (epg, epg_subnet, epg_endpoint),
nat_rule und application folgen mit ihrer Phase als eigene Migrationen.
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False, unique=True),
        sa.Column("ipam_type", sa.String(length=16), nullable=False),
        sa.Column("dns_type", sa.String(length=32), nullable=False),
        sa.Column("aci_tenant_names", sa.JSON(), nullable=True),
        sa.Column("naming_convention", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "zone",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
    )
    op.create_index("ix_zone_tenant_id", "zone", ["tenant_id"])

    op.create_table(
        "service_object",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("proto", sa.String(length=8), nullable=False),
        sa.Column("port_start", sa.Integer(), nullable=True),
        sa.Column("port_end", sa.Integer(), nullable=True),
        sa.Column("icmp_type", sa.Integer(), nullable=True),
        sa.Column("canonical_name", sa.String(length=64), nullable=False, unique=True),
    )

    op.create_table(
        "network_object",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("object_class", sa.String(length=16), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=True),
        sa.Column("value", sa.String(length=200), nullable=True),
        sa.Column("family", sa.String(length=4), nullable=True),
        sa.Column("fqdn", sa.String(length=300), nullable=True),
        sa.Column("domain", sa.String(length=200), nullable=True),
        sa.Column("zone_id", sa.Integer(), sa.ForeignKey("zone.id"), nullable=True),
        sa.Column("vlan", sa.String(length=32), nullable=True),
        sa.Column("source_of_record", sa.String(length=16), nullable=True),
        sa.Column("match_status", sa.String(length=20), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_network_object_tenant_id", "network_object", ["tenant_id"])
    op.create_index("ix_network_object_domain", "network_object", ["domain"])
    op.create_index("ix_network_object_zone_id", "network_object", ["zone_id"])

    op.create_table(
        "rule",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "source_system_id", sa.Integer(), sa.ForeignKey("source_system.id"), nullable=False
        ),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("action", sa.String(length=8), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=True),
        sa.Column("section_label", sa.String(length=100), nullable=True),
        sa.Column("src_zone_id", sa.Integer(), sa.ForeignKey("zone.id"), nullable=True),
        sa.Column("dst_zone_id", sa.Integer(), sa.ForeignKey("zone.id"), nullable=True),
        sa.Column("contract_dn", sa.String(length=400), nullable=True),
        sa.Column("subject", sa.String(length=200), nullable=True),
        sa.Column("is_inter_tenant", sa.Boolean(), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collector_run_id", sa.String(length=36), sa.ForeignKey("collector_run.id"), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=True),
    )
    op.create_index("ix_rule_tenant_id", "rule", ["tenant_id"])
    op.create_index("ix_rule_source_system_id", "rule", ["source_system_id"])

    op.create_table(
        "rule_endpoint",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("rule_id", sa.Integer(), sa.ForeignKey("rule.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(length=4), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column(
            "network_object_id", sa.Integer(), sa.ForeignKey("network_object.id"), nullable=True
        ),
        sa.Column("inline_value", sa.String(length=300), nullable=True),
        sa.Column("matched", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_rule_endpoint_rule_id", "rule_endpoint", ["rule_id"])

    op.create_table(
        "rule_service",
        sa.Column("rule_id", sa.Integer(), sa.ForeignKey("rule.id", ondelete="CASCADE"), primary_key=True),
        sa.Column(
            "service_object_id", sa.Integer(), sa.ForeignKey("service_object.id"), primary_key=True
        ),
    )

    op.create_table(
        "finding",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("subject_kind", sa.String(length=16), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_finding_tenant_id", "finding", ["tenant_id"])
    op.create_index("ix_finding_status", "finding", ["status"])


def downgrade() -> None:
    op.drop_table("finding")
    op.drop_table("rule_service")
    op.drop_table("rule_endpoint")
    op.drop_table("rule")
    op.drop_table("network_object")
    op.drop_table("service_object")
    op.drop_table("zone")
    op.drop_table("tenant")
