"""Standard-Namensprofil „default" anlegen (Datenmigration)

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-19

Legt das Profil „default" mit den Vorgabewerten aus `app.naming_defaults` an, falls es
noch nicht existiert (idempotent). Es gilt für alle Mandanten ohne eigenes Namensprofil
und ist danach normale, in der App pflegbare Laufzeitdaten.
"""

import sqlalchemy as sa
from alembic import op

from app.naming_defaults import DEFAULT_DICTIONARY, DEFAULT_PROFILE

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

profile_t = sa.table(
    "naming_profile",
    sa.column("id", sa.Integer),
    sa.column("customer", sa.String),
    sa.column("name", sa.String),
)
entry_t = sa.table(
    "dictionary_entry",
    sa.column("profile_id", sa.Integer),
    sa.column("kind", sa.String),
    sa.column("key", sa.String),
    sa.column("value", sa.String),
)


def _default_id(bind) -> int | None:
    return bind.execute(
        sa.select(profile_t.c.id).where(
            profile_t.c.customer == DEFAULT_PROFILE, profile_t.c.name == DEFAULT_PROFILE
        )
    ).scalar()


def upgrade() -> None:
    bind = op.get_bind()
    profile_id = _default_id(bind)
    if profile_id is None:
        bind.execute(sa.insert(profile_t).values(customer=DEFAULT_PROFILE, name=DEFAULT_PROFILE))
        profile_id = _default_id(bind)
    existing = {
        (kind, key)
        for kind, key in bind.execute(
            sa.select(entry_t.c.kind, entry_t.c.key).where(entry_t.c.profile_id == profile_id)
        )
    }
    rows = [
        {"profile_id": profile_id, "kind": kind, "key": key, "value": value}
        for kind, pairs in DEFAULT_DICTIONARY.items()
        for key, value in pairs
        if (kind, key) not in existing
    ]
    if rows:
        bind.execute(sa.insert(entry_t), rows)


def downgrade() -> None:
    bind = op.get_bind()
    profile_id = _default_id(bind)
    if profile_id is None:
        return
    bind.execute(
        sa.text("UPDATE tenant SET naming_profile_id = NULL WHERE naming_profile_id = :pid"),
        {"pid": profile_id},
    )
    bind.execute(sa.delete(entry_t).where(entry_t.c.profile_id == profile_id))
    bind.execute(sa.delete(profile_t).where(profile_t.c.id == profile_id))
