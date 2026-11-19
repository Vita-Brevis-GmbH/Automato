"""Client-Secret des Anmelde-Tors versiegelt aus dem Cockpit (1.13.0, ADR 0055)

Anwender 2026-11-16 auf den Vorschlag (Cockpit liefert das Geheimnis von `automato-front`
versiegelt an die Automato-VM, nur die VM kann es entschlüsseln): „go der Vorschlag“ –
`portal_customer.tor_secret` hält nur das Chiffrat (`realm`, `kennung`, `versiegelt`), nie den
Klartext. Ohne Wert: wie bisher (Client-Secret von Hand mit `front.sh tor`).

Revision ID: 0082
Revises: 0081
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("portal_customer") as batch:
        batch.add_column(sa.Column("tor_secret", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("portal_customer") as batch:
        batch.drop_column("tor_secret")
