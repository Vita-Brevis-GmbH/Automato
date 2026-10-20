"""Standorte: virtuell (000, 001, ...) und physisch (chzrh1, ...) mit n:m-Zuordnung

`site.kind` = virtual | physical, `site.code` bis 16 Zeichen (physische Codes wie `chzrh1`).
Bestand: dreistellige Ziffern-Codes werden virtuell, alle anderen (z. B. `ZRH`) physisch.
`site_link` haelt, an welchen physischen Standorten ein virtueller Standort laeuft (ADR 0015).

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-20
"""

import re

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

_VIRTUAL = re.compile(r"^[0-9]{3}$")


def upgrade() -> None:
    with op.batch_alter_table("site") as batch:
        batch.alter_column("code", existing_type=sa.String(length=3), type_=sa.String(length=16))
        batch.add_column(
            sa.Column("kind", sa.String(length=16), nullable=False, server_default="virtual")
        )
    conn = op.get_bind()
    site = sa.table("site", sa.column("id", sa.Integer), sa.column("code"), sa.column("kind"))
    for sid, code in conn.execute(sa.select(site.c.id, site.c.code)).all():
        if not _VIRTUAL.match(code or ""):
            conn.execute(site.update().where(site.c.id == sid).values(kind="physical"))
    op.create_table(
        "site_link",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "virtual_id", sa.Integer(), sa.ForeignKey("site.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "physical_id",
            sa.Integer(),
            sa.ForeignKey("site.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("virtual_id", "physical_id", name="uq_site_link"),
    )
    op.create_index("ix_site_link_virtual_id", "site_link", ["virtual_id"])
    op.create_index("ix_site_link_physical_id", "site_link", ["physical_id"])


def downgrade() -> None:
    op.drop_index("ix_site_link_physical_id", table_name="site_link")
    op.drop_index("ix_site_link_virtual_id", table_name="site_link")
    op.drop_table("site_link")
    # Codes ueber 3 Zeichen passen nicht mehr: Standort und seine Verortungen entfallen
    conn = op.get_bind()
    long = (
        sa.select(sa.column("id"))
        .select_from(sa.table("site"))
        .where(sa.func.length(sa.column("code")) > 3)
    )
    ids = [row[0] for row in conn.execute(long).all()]
    if ids:
        for table in ("network_object", "source_system", "application"):
            t = sa.table(table, sa.column("site_id", sa.Integer))
            conn.execute(t.update().where(t.c.site_id.in_(ids)).values(site_id=None))
        s = sa.table("site", sa.column("id", sa.Integer))
        conn.execute(s.delete().where(s.c.id.in_(ids)))
    with op.batch_alter_table("site") as batch:
        batch.drop_column("kind")
        batch.alter_column("code", existing_type=sa.String(length=16), type_=sa.String(length=3))
