"""Eigene Automato-Instanz je Kunde (1.16.0, ADR 0061)

Cockpit P4 „Ausrollen“ (Anwender 2026-11-16: „Bitte weiter bis P5“): eine eigene Automato-Instanz
je Kunde, angelegt vom Host-Agenten des Cockpits (`deploy/betrieb.sh installieren`).
`portal_customer.automato_instanz`: Name der Instanz aus den Freigaben des Cockpits - dann leitet
der Front-Proxy `<kunde>-doc` an `automato-<instanz>` statt an die Hauptinstanz. Vorgabe leer
(gemeinsame Instanz wie bisher).

Revision ID: 0088_automato_instanz
Revises: 0087
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0088_automato_instanz"
down_revision = "0087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("portal_customer") as batch:
        batch.add_column(sa.Column("automato_instanz", sa.String(length=31), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("portal_customer") as batch:
        batch.drop_column("automato_instanz")
