"""Anbindung fuer mehrere Firewalls (0.20.0)

topo_exit.source_system_ids (Liste, leer = jede Firewall) statt source_system_id - eine
Anbindung gilt z. B. fuer beide Session-Sync-Partner bzw. alle Firewalls eines Verbunds.

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

_EXIT = sa.table(
    "topo_exit",
    sa.column("id", sa.Integer()),
    sa.column("source_system_id", sa.Integer()),
    sa.column("source_system_ids", sa.JSON()),
)


def upgrade() -> None:
    op.add_column("topo_exit", sa.Column("source_system_ids", sa.JSON(), nullable=True))
    conn = op.get_bind()
    for exit_id, sid in conn.execute(sa.select(_EXIT.c.id, _EXIT.c.source_system_id)).all():
        conn.execute(
            _EXIT.update()
            .where(_EXIT.c.id == exit_id)
            .values(source_system_ids=[sid] if sid is not None else [])
        )
    op.drop_index("ix_topo_exit_source_system_id", table_name="topo_exit")
    op.drop_column("topo_exit", "source_system_id")


def downgrade() -> None:
    op.add_column(
        "topo_exit",
        sa.Column(
            "source_system_id",
            sa.Integer(),
            sa.ForeignKey("source_system.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_topo_exit_source_system_id", "topo_exit", ["source_system_id"])
    conn = op.get_bind()
    for exit_id, ids in conn.execute(sa.select(_EXIT.c.id, _EXIT.c.source_system_ids)).all():
        first = (ids or [None])[0]
        conn.execute(_EXIT.update().where(_EXIT.c.id == exit_id).values(source_system_id=first))
    op.drop_column("topo_exit", "source_system_ids")
