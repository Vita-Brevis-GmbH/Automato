"""Zuordnung in Automato aus dem Cockpit (1.13.0, ADR 0054 Nachtrag)

Anwender 2026-11-16 zum Vorschlag „Mandant und Kunden für Automato künftig direkt im Cockpit beim
Kunden wählen; das Cockpit zeigt dafür die Liste aus Automato“: „Ja gerne“ –
`portal_customer.scope_source` (`automato` | `cockpit`): wer Mandant, Kunden und Standard-Rolle
setzt. Vorgabe `automato` – bestehende Kunden bleiben in Automato änderbar.

Revision ID: 0083
Revises: 0082
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("portal_customer") as batch:
        batch.add_column(
            sa.Column("scope_source", sa.String(length=16), server_default="automato",
                      nullable=False)  # fmt: skip
        )


def downgrade() -> None:
    with op.batch_alter_table("portal_customer") as batch:
        batch.drop_column("scope_source")
