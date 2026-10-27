"""Feinere Rollen der Kunden-Benutzer (ADR 0018 Ergaenzung „Feinere Rollen“)

customer_user.origin: wer die Zuordnung angelegt hat - `staff` (Betreuer, wie bisher) bzw.
`customer` (Kunden-Admin). Zuordnungen eines Kunden-Admins wirken nie auf Betreuer. Die Rolle
bleibt `String(16)` und nimmt jetzt viewer | triage | editor | customer_admin auf; bestehende Werte
bleiben unveraendert (`operator` aus 0.53.0 gilt als triage).

Revision ID: 0059
Revises: 0056
Create Date: 2026-10-27
"""

import sqlalchemy as sa
from alembic import op

revision = "0059"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("customer_user") as batch:
        batch.add_column(sa.Column("origin", sa.String(16), nullable=False,
                                   server_default="staff"))  # fmt: skip


def downgrade() -> None:
    with op.batch_alter_table("customer_user") as batch:
        batch.drop_column("origin")
