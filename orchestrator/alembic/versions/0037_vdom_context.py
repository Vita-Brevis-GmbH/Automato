"""VDOM-Kontext (ADR 0019 Stufe 1, 0.15.0)

FortiGate-Quellsysteme werden je VDOM gelesen: Policy-ID und Objektname gelten je VDOM.
- rule, nat_rule, device_object, inv_interface, inv_subnet: `vdom`
- device_object: eindeutig je Quellsystem/VDOM/Kategorie/Familie/Name
- rule_pair_decision: eindeutig je Quellsystem/VDOM/Policy-IDs; bestehende Entscheidungen
  ohne VDOM gelten fuer jeden VDOM des Quellsystems

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

_TABLES = ("rule", "nat_rule", "device_object", "inv_interface", "inv_subnet")
_PAIR_COLUMNS = ["source_system_id", "v4_external_id", "v6_external_id"]
# SQLite: unbenannte Unique-Constraints bekommen beim Neuaufbau (batch) diesen Namen
_NAMING = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def _unique_name(table: str, columns: list[str]) -> str:
    """Name der (in 0035 unbenannten) Unique-Constraint, wie die Datenbank sie fuehrt."""
    for uc in sa.inspect(op.get_bind()).get_unique_constraints(table):
        if uc["column_names"] == columns and uc.get("name"):
            return uc["name"]
    return _NAMING["uq"] % {"table_name": table, "column_0_name": columns[0]}


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("vdom", sa.String(64), nullable=True))
    op.add_column("rule_pair_decision", sa.Column("vdom", sa.String(64), nullable=True))
    with op.batch_alter_table("device_object") as batch:
        batch.drop_constraint("uq_device_object_source_cat_fam_name", type_="unique")
        batch.create_unique_constraint(
            "uq_device_object_source_vdom_cat_fam_name",
            ["source_system_id", "vdom", "category", "family", "name"],
        )
    old = _unique_name("rule_pair_decision", _PAIR_COLUMNS)
    with op.batch_alter_table("rule_pair_decision", naming_convention=_NAMING) as batch:
        batch.drop_constraint(old, type_="unique")
        batch.create_unique_constraint(
            "uq_rule_pair_decision_source_vdom_ids",
            ["source_system_id", "vdom", "v4_external_id", "v6_external_id"],
        )


def downgrade() -> None:
    # Das alte Schema kennt je Quellsystem nur einen VDOM: gleichnamige Objekte bzw. gleiche
    # Paare weiterer VDOMs entfallen (der naechste Lauf liest sie wieder ein)
    op.execute(
        "DELETE FROM rule_pair_decision WHERE EXISTS (SELECT 1 FROM rule_pair_decision d"
        " WHERE d.source_system_id = rule_pair_decision.source_system_id"
        " AND d.v4_external_id = rule_pair_decision.v4_external_id"
        " AND d.v6_external_id = rule_pair_decision.v6_external_id"
        " AND d.id < rule_pair_decision.id)"
    )
    with op.batch_alter_table("rule_pair_decision") as batch:
        batch.drop_constraint("uq_rule_pair_decision_source_vdom_ids", type_="unique")
        batch.create_unique_constraint("uq_rule_pair_decision_source_ids", _PAIR_COLUMNS)
        batch.drop_column("vdom")
    op.execute(
        "DELETE FROM device_object WHERE EXISTS (SELECT 1 FROM device_object d"
        " WHERE d.source_system_id = device_object.source_system_id"
        " AND d.category = device_object.category AND d.name = device_object.name"
        " AND (d.family = device_object.family"
        " OR (d.family IS NULL AND device_object.family IS NULL))"
        " AND d.id < device_object.id)"
    )
    with op.batch_alter_table("device_object") as batch:
        batch.drop_constraint("uq_device_object_source_vdom_cat_fam_name", type_="unique")
        batch.create_unique_constraint(
            "uq_device_object_source_cat_fam_name",
            ["source_system_id", "category", "family", "name"],
        )
    for table in reversed(_TABLES):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("vdom")
