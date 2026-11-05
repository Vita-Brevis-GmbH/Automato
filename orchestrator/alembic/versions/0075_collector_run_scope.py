"""Lauf nur der Regeln einer Applikation (0.139.0)

Anwender 2026-11-05: „Wenn ich im Realen System was ändere für eine App auf diversen Firewalls
und ACI, würde ich gerne nicht alle Firewalls und ACI neu integrieren, sondern nur die Regeln
neu berechnen, welche geändert haben“ – `scope` = `rules` liest nur Regeln, Objekte und NAT
(ohne Interfaces, Routing, Fabric); `application_id` = die Applikation, die danach neu rechnet.

Revision ID: 0075
Revises: 0074
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("collector_run", sa.Column("scope", sa.String(16), nullable=True))
    op.add_column("collector_run", sa.Column("application_id", sa.Integer(), nullable=True))
    op.create_index("ix_collector_run_application_id", "collector_run", ["application_id"])


def downgrade() -> None:
    op.drop_index("ix_collector_run_application_id", table_name="collector_run")
    op.drop_column("collector_run", "application_id")
    op.drop_column("collector_run", "scope")
