"""Applikation als eigenes Objekt (ADR 0012): Mitglieder AP/EPG/Server als Text-Verweise

Die bisherige "Applikation" war ein publizierter Dienst (VIP) und heisst jetzt so
(/published-services). Neu: `application` (Name, Kuerzel, Verantwortliche, Kritikalitaet,
Standort, Beschreibung) und `application_member` (kind ap|epg|host, ref, origin).

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "application",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=True),
        sa.Column("owner", sa.String(length=200), nullable=True),
        sa.Column("criticality", sa.String(length=16), nullable=True),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("site.id"), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_application_tenant_name"),
    )
    op.create_index("ix_application_tenant_id", "application", ["tenant_id"])
    op.create_index("ix_application_code", "application", ["code"])
    op.create_table(
        "application_member",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "application_id",
            sa.Integer(),
            sa.ForeignKey("application.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("ref", sa.String(length=400), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False, server_default="manual"),
        sa.UniqueConstraint("application_id", "kind", "ref", name="uq_application_member"),
    )
    op.create_index(
        "ix_application_member_application_id", "application_member", ["application_id"]
    )
    op.create_index("ix_application_member_ref", "application_member", ["ref"])


def downgrade() -> None:
    op.drop_index("ix_application_member_ref", table_name="application_member")
    op.drop_index("ix_application_member_application_id", table_name="application_member")
    op.drop_table("application_member")
    op.drop_index("ix_application_code", table_name="application")
    op.drop_index("ix_application_tenant_id", table_name="application")
    op.drop_table("application")
