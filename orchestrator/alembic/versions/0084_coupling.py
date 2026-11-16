"""Kopplung mit dem Cockpit ueber einen Einmal-Code (1.13.0, ADR 0057)

Anwender 2026-11-16 (Cockpit ADR 0011, Stufe C): „Automato mit dem Cockpit koppeln – bisher
Token kopieren (`aufbau.sh cockpit-token`) – künftig Kopplung mit Einmal-Code“. Tabelle
`coupling`: je Gegenstelle hoechstens ein offener Code und ein gekoppeltes Token, beide nur als
SHA-256-Hash. Das Token aus `.env` (`AUTOMATO_COCKPIT_TOKEN`) gilt weiter.

Revision ID: 0084
Revises: 0081 (beim Zusammenfuehren hinter die uebrigen Migrationen von 1.13.0 stellen)
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0084"
down_revision = "0081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "coupling",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("peer", sa.String(length=32), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=True),
        sa.Column("code_expires", sa.DateTime(timezone=True), nullable=True),
        sa.Column("code_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("code_by", sa.String(length=200), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=True),
        sa.Column("peer_name", sa.String(length=100), nullable=True),
        sa.Column("coupled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("coupled_by", sa.String(length=200), nullable=True),
        sa.Column("last_used", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("peer"),
    )


def downgrade() -> None:
    op.drop_table("coupling")
