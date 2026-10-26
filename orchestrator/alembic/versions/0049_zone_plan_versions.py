"""Versionen des Zonenplans (0.52.0, ADR 0024)

zone_plan_version: je Mandant fortlaufende Nummer, Bezeichnung, Notiz, Fingerabdruck des
Designs, Ersteller, Zeitpunkt und der gespeicherte Stand (Zonen, Plan, Matrix) als JSON;
geloeschte Versionen behalten ihre Nummer (`deleted_at`, Stand leer).

Revision ID: 0049
Revises: 0048
Create Date: 2026-10-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "zone_plan_version",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.String(200), nullable=True),
        sa.UniqueConstraint("tenant_id", "number", name="uq_zone_plan_version_number"),
    )
    op.create_index("ix_zone_plan_version_tenant_id", "zone_plan_version", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_zone_plan_version_tenant_id", table_name="zone_plan_version")
    op.drop_table("zone_plan_version")
