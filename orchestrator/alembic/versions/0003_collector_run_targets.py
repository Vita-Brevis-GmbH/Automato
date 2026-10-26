"""collector_run um Ziel-Referenzen erweitern (M7): tenant_id, source_system_id

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-22

Erlaubt, einen Collector-Lauf einem Mandanten und Quellsystem zuzuordnen, damit
die Ergebnisse in den Rule & Service Store persistiert werden koennen (ADR 0004).
Beide Spalten sind nullable (rueckwaertskompatibel: ohne Angabe wird nur gezaehlt).

Batch-Modus, weil SQLite keine FK-Constraints per ALTER hinzufuegen kann
(copy-and-move); auf Postgres werden normale ALTER-Statements erzeugt.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("collector_run", schema=None) as batch_op:
        batch_op.add_column(sa.Column("tenant_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("source_system_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_collector_run_tenant", "tenant", ["tenant_id"], ["id"]
        )
        batch_op.create_foreign_key(
            "fk_collector_run_source_system", "source_system", ["source_system_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("collector_run", schema=None) as batch_op:
        batch_op.drop_constraint("fk_collector_run_source_system", type_="foreignkey")
        batch_op.drop_constraint("fk_collector_run_tenant", type_="foreignkey")
        batch_op.drop_column("source_system_id")
        batch_op.drop_column("tenant_id")
