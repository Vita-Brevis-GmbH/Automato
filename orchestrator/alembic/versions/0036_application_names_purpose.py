"""Applikation: externer und interner Name, Verwendungszweck (Dev/Test/Prod) (0.14.0)

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("application", sa.Column("external_name", sa.String(200), nullable=True))
    op.add_column("application", sa.Column("internal_name", sa.String(200), nullable=True))
    op.add_column("application", sa.Column("purpose", sa.String(8), nullable=True))


def downgrade() -> None:
    op.drop_column("application", "purpose")
    op.drop_column("application", "internal_name")
    op.drop_column("application", "external_name")
