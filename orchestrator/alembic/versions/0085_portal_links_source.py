"""Kacheln der Startseite aus dem Cockpit (1.13.0, ADR 0054 zweiter Nachtrag)

Anwender 2026-11-16 (Cockpit ADR 0011, Stufe B): „Kacheln der Startseite – bisher Kunden-Portal
von Automato – künftig beim Kunden im Cockpit“. `portal_customer.links_source` (`automato` |
`cockpit`): wer die weiteren Kacheln eines Kunden setzt. Vorgabe `automato` - bestehende Kunden
behalten ihre Kacheln und bleiben in Automato aenderbar.

Revision ID: 0085
Revises: 0084
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("portal_customer") as batch:
        batch.add_column(
            sa.Column("links_source", sa.String(length=16), server_default="automato",
                      nullable=False)  # fmt: skip
        )


def downgrade() -> None:
    with op.batch_alter_table("portal_customer") as batch:
        batch.drop_column("links_source")
