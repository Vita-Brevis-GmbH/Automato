"""tenant.code (GG-Kuerzel) + site (Standorte) - ADR 0004-Ergaenzung

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-14

Ein Mandant (= Gruppengesellschaft) bekommt ein optionales Kuerzel und hat einen oder
mehrere Standorte mit dreistelligem Code. Additiv (tenant.code nullable), damit
bestehende Zeilen gueltig bleiben. Stammdaten, keine Funktionsrelevanz.
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant", sa.Column("code", sa.String(length=20), nullable=True))
    op.create_index("ix_tenant_code", "tenant", ["code"])
    op.create_table(
        "site",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=3), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_site_tenant"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_site_tenant_code"),
    )
    op.create_index("ix_site_tenant_id", "site", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_site_tenant_id", table_name="site")
    op.drop_table("site")
    op.drop_index("ix_tenant_code", table_name="tenant")
    op.drop_column("tenant", "code")
