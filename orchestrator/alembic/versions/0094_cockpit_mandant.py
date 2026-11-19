"""Neuer Mandant je Portal-Kunde aus dem Cockpit (1.19.2, ADR 0058 Nachtrag)

Anwender 2026-11-18: „Es sollte gemäss mir einen neuen Mandanten angelegt und mit dem Account
verknüpft werden, sowie die anderen Mandanten nicht sichtbar machen.“

- `cockpit_tenant`: Mandant, den Automato fuer einen Portal-Kunden aus dem Cockpit angelegt hat
  (`zuordnung.mandant_anlegen`) - einer je Portal-Kunde (Kurzname), damit ein zweites
  `PUT /freigaben` keinen weiteren anlegt. Faellt mit dem Mandanten (`ondelete=CASCADE`).

Revision ID: 0094_cockpit_mandant
Revises: 0093_reservierungen
Create Date: 2026-11-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0094_cockpit_mandant"
down_revision = "0093_reservierungen"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cockpit_tenant",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("short_name", sa.String(length=42), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("short_name"),
        sa.UniqueConstraint("tenant_id"),
    )


def downgrade() -> None:
    op.drop_table("cockpit_tenant")
