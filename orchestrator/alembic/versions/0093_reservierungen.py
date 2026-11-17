"""Reservierungen von VLAN- und AS-Nummern (1.19.0, ADR 0066)

Anwender 2026-10-30 (Roadmap Version 2 Nr. 10) und 2026-11-17 (Prioritaet 6 „Reservierungen“,
„Reservationen auch ins Netbox“): `reservation` je Mandant - Art (vlan | asn), Nummer bzw.
Bereich, Standort (ohne Fremdschluessel, wie `stp_target`) und VLAN-Gruppe in NetBox, Zweck, wer
und wann, dazu die beim Reservieren schon gelesen belegten Nummern.

Revision ID: 0093_reservierungen
Revises: 0091_stp
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0093_reservierungen"
down_revision = "0091_stp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reservation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("low", sa.BigInteger(), nullable=False),
        sa.Column("high", sa.BigInteger(), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=True),
        sa.Column("vlan_group", sa.String(length=100), nullable=True),
        sa.Column("purpose", sa.String(length=500), nullable=False),
        sa.Column("used_before", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("changed_by", sa.String(length=200), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reservation_tenant_kind", "reservation", ["tenant_id", "kind"])


def downgrade() -> None:
    op.drop_index("ix_reservation_tenant_kind", table_name="reservation")
    op.drop_table("reservation")
