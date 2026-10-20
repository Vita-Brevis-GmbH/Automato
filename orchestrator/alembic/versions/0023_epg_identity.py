"""EPG-Identitaet per DN je ACI-Tenant: Art, Tenant, VRF, L3Out, Quellsystem, Subnetz-Herkunft

Dieselben EPG-Namen gibt es in vielen ACI-Tenants; der Schluessel (Mandant, Name) legte sie
zusammen. Neu: `dn` (eindeutig je Mandant), `kind` (epg | external | vzany), `aci_tenant`,
`vrf`, `l3out`, `source_system_id` und `epg_subnet.origin` (epg | bd | ext). Bestehende
EPGs behalten ihren Namen ohne DN; der naechste ACI-Lauf legt sie per DN neu an und raeumt
die alten auf.

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("epg") as batch:
        batch.add_column(
            sa.Column("kind", sa.String(length=16), nullable=False, server_default="epg")
        )
        batch.add_column(sa.Column("aci_tenant", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("dn", sa.String(length=400), nullable=True))
        batch.add_column(sa.Column("vrf", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("l3out", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("source_system_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_epg_source_system", "source_system", ["source_system_id"], ["id"]
        )
        batch.drop_constraint("uq_epg_tenant_name", type_="unique")
        batch.create_unique_constraint("uq_epg_tenant_dn", ["tenant_id", "dn"])
    op.create_index("ix_epg_source_system_id", "epg", ["source_system_id"])
    with op.batch_alter_table("epg_subnet") as batch:
        batch.add_column(
            sa.Column("origin", sa.String(length=8), nullable=False, server_default="epg")
        )


def downgrade() -> None:
    with op.batch_alter_table("epg_subnet") as batch:
        batch.drop_column("origin")
    # Zurueck auf (Mandant, Name): EPGs mit DN bleiben nur einmal je Name erhalten
    op.execute(
        "DELETE FROM epg_subnet WHERE epg_id IN (SELECT id FROM epg WHERE id NOT IN "
        "(SELECT MIN(id) FROM epg GROUP BY tenant_id, name))"
    )
    op.execute(
        "DELETE FROM epg_endpoint WHERE epg_id IN (SELECT id FROM epg WHERE id NOT IN "
        "(SELECT MIN(id) FROM epg GROUP BY tenant_id, name))"
    )
    op.execute(
        "UPDATE rule_endpoint SET epg_id = NULL WHERE epg_id IN (SELECT id FROM epg WHERE id "
        "NOT IN (SELECT MIN(id) FROM epg GROUP BY tenant_id, name))"
    )
    op.execute(
        "DELETE FROM epg WHERE id NOT IN (SELECT MIN(id) FROM epg GROUP BY tenant_id, name)"
    )
    op.drop_index("ix_epg_source_system_id", table_name="epg")
    with op.batch_alter_table("epg") as batch:
        batch.drop_constraint("uq_epg_tenant_dn", type_="unique")
        batch.create_unique_constraint("uq_epg_tenant_name", ["tenant_id", "name"])
        batch.drop_constraint("fk_epg_source_system", type_="foreignkey")
        for col in ("source_system_id", "l3out", "vrf", "dn", "aci_tenant", "kind"):
            batch.drop_column(col)
