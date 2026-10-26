"""Grundaufbau: Audit-Log, Quellsysteme, Collector-Laeufe

Revision ID: 0001
Revises:
Create Date: 2026-07-21

Das normalisierte Regelmodell (Ist-Regeln, Adressobjekt-Referenzen,
Service-Objekte) folgt in Meilenstein M5 als eigene Migration.
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
    )
    op.create_index("ix_audit_log_timestamp", "audit_log", ["timestamp"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])

    op.create_table(
        "source_system",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False, unique=True),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "collector_run",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rule_count", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
    )
    op.create_index("ix_collector_run_platform", "collector_run", ["platform"])


def downgrade() -> None:
    op.drop_table("collector_run")
    op.drop_table("source_system")
    op.drop_table("audit_log")
