"""source_system.tenant_id: Quellsysteme gehoeren zu einem Mandanten

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-19

Quellsysteme sind nicht mehr global, sondern je Mandant (Name je Mandant eindeutig statt
global). Bestehende Systeme werden dem Mandanten zugeordnet, dessen Regeln bzw. Laeufe sie
tragen - nur wenn das eindeutig ist; sonst bleiben sie "nicht zugeordnet" und werden im
UI einem Mandanten zugewiesen.
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

_SINGLE_TENANT = """
UPDATE source_system SET tenant_id = (
    SELECT MIN({t}.tenant_id) FROM {t} WHERE {t}.source_system_id = source_system.id
)
WHERE tenant_id IS NULL AND (
    SELECT COUNT(DISTINCT {t}.tenant_id) FROM {t}
    WHERE {t}.source_system_id = source_system.id AND {t}.tenant_id IS NOT NULL
) = 1
"""


def upgrade() -> None:
    bind = op.get_bind()
    # Globalen Unique-Constraint auf "name" (0001) finden - Name je DB verschieden
    # (Postgres: source_system_name_key, SQLite: unbenannt -> Namenskonvention unten).
    old_unique = [
        u["name"] or "uq_source_system_name"
        for u in sa.inspect(bind).get_unique_constraints("source_system")
        if u["column_names"] == ["name"]
    ]
    with op.batch_alter_table(
        "source_system", naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"}
    ) as batch:
        for name in old_unique:
            batch.drop_constraint(name, type_="unique")
        batch.add_column(sa.Column("tenant_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_source_system_tenant", "tenant", ["tenant_id"], ["id"])
        batch.create_unique_constraint("uq_source_system_tenant_name", ["tenant_id", "name"])
    op.create_index("ix_source_system_tenant_id", "source_system", ["tenant_id"])
    for table in ("rule", "nat_rule", "collector_run"):
        op.execute(_SINGLE_TENANT.format(t=table))


def downgrade() -> None:
    op.drop_index("ix_source_system_tenant_id", table_name="source_system")
    with op.batch_alter_table("source_system") as batch:
        batch.drop_constraint("uq_source_system_tenant_name", type_="unique")
        batch.drop_constraint("fk_source_system_tenant", type_="foreignkey")
        batch.drop_column("tenant_id")
        batch.create_unique_constraint("source_system_name_key", ["name"])
