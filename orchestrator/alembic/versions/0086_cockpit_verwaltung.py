"""Verwaltung nur aus dem Cockpit (1.14.0, ADR 0058)

Anwender 2026-11-16: „die Rollen und User … sollen nur noch im Cockpit sein. Die Kunden sollen
dies nicht machen können“ und „Weiter sollen die Kunden und Portale im Automato durch das Cockpit
erstellt und verwaltet werden, nicht ein Mix wie heute.“

- `portal_customer.users`: Benutzer des Kunden-Realms mit Rolle aus dem Cockpit (`benutzer` in
  `PUT /freigaben`); null = keine Aussage (alle Benutzer des Realms mit der Standard-Rolle).
- `cockpit_customer`: Kunden in Automato, die Automato fuer einen Portal-Kunden aus dem Cockpit
  angelegt hat (`zuordnung.kunde_anlegen`) - ein Kunde je Portal-Kunde und Mandant.

Revision ID: 0086_cockpit_verwaltung
Revises: 0085
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0086_cockpit_verwaltung"
down_revision = "0085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("portal_customer") as batch:
        batch.add_column(sa.Column("users", sa.JSON(), nullable=True))
    op.create_table(
        "cockpit_customer",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("short_name", sa.String(length=42), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["customer.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("customer_id"),
        sa.UniqueConstraint("short_name", "tenant_id", name="uq_cockpit_customer_short_tenant"),
    )
    op.create_index("ix_cockpit_customer_short_name", "cockpit_customer", ["short_name"])
    op.create_index("ix_cockpit_customer_tenant_id", "cockpit_customer", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_cockpit_customer_tenant_id", table_name="cockpit_customer")
    op.drop_index("ix_cockpit_customer_short_name", table_name="cockpit_customer")
    op.drop_table("cockpit_customer")
    with op.batch_alter_table("portal_customer") as batch:
        batch.drop_column("users")
