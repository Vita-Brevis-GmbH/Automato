"""Freigaben aus dem Cockpit (1.11.0, ADR 0054)

Anwender 2026-11-13: „starte mal P2“ nach dem Vorschlag „Ein Ort für Kunden: im Cockpit Kunden
anlegen und Werkzeuge wählen; Automato übernimmt“ – `portal_customer.source` (Herkunft:
`oberflaeche` | `cockpit`) und `nb` (NetBox `-nb` als wählbares Werkzeug). Vorgaben: Herkunft
Oberfläche, NetBox an – bestehende Kunden bleiben, wie sie sind.

Revision ID: 0081
Revises: 0080
Create Date: 2026-11-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("portal_customer") as batch:
        batch.add_column(sa.Column("nb", sa.Boolean(), server_default=sa.true(), nullable=False))
        batch.add_column(
            sa.Column("source", sa.String(length=16), server_default="oberflaeche", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("portal_customer") as batch:
        batch.drop_column("source")
        batch.drop_column("nb")
