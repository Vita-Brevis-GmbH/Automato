"""Kunden-Rolle `customer_operator` (ADR 0068)

Anwender 2026-11-18: „Operatoren sollen alles sehen und nur das einlesen anstossen können.“

- `portal_customer.default_role` und `customer_user.role` fassen 32 statt 16 Zeichen – der Wert
  `customer_operator` hat 17.
- Rückweg: `customer_operator` wird `viewer` (fail-closed: eine ältere Fassung kennt die Rolle
  nicht und läse sie ohnehin als `viewer`), dann wieder 16 Zeichen.

Revision ID: 0095_rolle_operator
Revises: 0094_cockpit_mandant
Create Date: 2026-11-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0095_rolle_operator"
down_revision = "0094_cockpit_mandant"
branch_labels = None
depends_on = None

_SPALTEN = (("portal_customer", "default_role"), ("customer_user", "role"))


def upgrade() -> None:
    for table, column in _SPALTEN:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                column, existing_type=sa.String(length=16), type_=sa.String(length=32)
            )


def downgrade() -> None:
    for table, column in _SPALTEN:
        op.execute(
            sa.text(f"UPDATE {table} SET {column} = 'viewer' WHERE length({column}) > 16")
        )
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                column, existing_type=sa.String(length=32), type_=sa.String(length=16)
            )
