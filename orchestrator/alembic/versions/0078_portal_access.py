"""Zugriff je Kunde (1.5.0, ADR 0051 Nachtrag)

Anwender 2026-11-10: „Kann ich eventuell noch einstellen, von wo ich die einzelnen Kunden drauf
zugreifen können? Bei VB wäre es am liebsten nur von intern möglich, andere ‚zahlende‘ Kunden von
extern“ – `portal_customer.access` (ueberall | intern | netze) und `access_nets` (Netze bei
„netze“). Vorgabe „ueberall“ = bisheriges Verhalten.

Revision ID: 0078
Revises: 0077
Create Date: 2026-11-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("portal_customer") as batch:
        batch.add_column(
            sa.Column("access", sa.String(length=16), server_default="ueberall", nullable=False)
        )
        batch.add_column(sa.Column("access_nets", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("portal_customer") as batch:
        batch.drop_column("access_nets")
        batch.drop_column("access")
