"""FT_ als Service-/Filter-Praefix im Standardprofil (Datenmigration, 0.36.0)

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-23

Das Praefix fuer Services (FortiGate) und Filter (ACI) war bisher eingebaut. Ab 0.36.0 steht
es in der Tabelle „Objekt-Praefix -> Typ“ (Woerterbuch `object_prefix`, Typ `service`) des
Profils „default“ und ist dort pflegbar. Nur ergaenzt, wenn FT_ noch fehlt (idempotent); andere
Profile behalten ihre Eintraege - ohne Service-Praefix gilt dort weiter FT_.
"""

import sqlalchemy as sa
from alembic import op

revision = "0045"
down_revision = "0044"
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
KIND, KEY, VALUE = "object_prefix", "FT_", "service"


def _default_id(bind) -> int | None:
    return bind.execute(
        sa.select(profile_t.c.id).where(
            profile_t.c.customer == "default", profile_t.c.name == "default"
        )
    ).scalar()


def upgrade() -> None:
    bind = op.get_bind()
    profile_id = _default_id(bind)
    if profile_id is None:
        return
    exists = bind.execute(
        sa.select(entry_t.c.key).where(
            entry_t.c.profile_id == profile_id, entry_t.c.kind == KIND, entry_t.c.key == KEY
        )
    ).first()
    if exists is None:
        bind.execute(
            sa.insert(entry_t).values(profile_id=profile_id, kind=KIND, key=KEY, value=VALUE)
        )


def downgrade() -> None:
    bind = op.get_bind()
    profile_id = _default_id(bind)
    if profile_id is None:
        return
    bind.execute(
        sa.delete(entry_t).where(
            entry_t.c.profile_id == profile_id,
            entry_t.c.kind == KIND,
            entry_t.c.key == KEY,
            entry_t.c.value == VALUE,
        )
    )
