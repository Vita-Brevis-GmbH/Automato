"""IPv4/IPv6-Paare automatisch bestaetigen je Mandant (0.138.0)

Anwender 2026-11-05: „Ich habe 500 Firewall Rule Paare, welche ich bestätigen müsste, da
eigentlich 99% stimmen ist es ein riesiger Aufwand … in den Basis Settings angeben, dass die
Paare automatisch bestätigt werden und dann aber wir die Möglichkeit haben, sie auseinander zu
nehmen, wenn sie nicht stimmen“ – Schalter am Mandanten, Vorgabe aus.

Revision ID: 0074
Revises: 0073
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant",
        sa.Column("rule_pairs_auto", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("tenant", "rule_pairs_auto")
