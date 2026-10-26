"""Firewall-Objekte: weitere Attribute wie auf dem Geraet

`device_object.attrs` (JSON): bei Services Protokoll, Ports inkl. Quellports, ICMP-Typ/-Code,
Kategorie, Timer, Ziel-Beschraenkung (iprange/fqdn); bei ACI-Filtern Tenant, DN und Eintraege.
Neue Befund-Typen (service_mismatch, rule_incomplete) brauchen kein Schema.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("device_object") as batch:
        batch.add_column(sa.Column("attrs", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("device_object") as batch:
        batch.drop_column("attrs")
