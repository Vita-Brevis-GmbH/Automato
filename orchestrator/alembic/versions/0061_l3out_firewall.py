"""L3Outs: Firewall dahinter von Hand (ADR 0034)

l3out_firewall: je ACI-L3Out (APIC und DN) die Firewall dahinter - bestaetigt bzw. von Hand
gewaehlt (Quellsystem, VDOM, Interface zum L3Out, je Firewall eine Zeile) oder ausdruecklich
keine (`origin` none, ohne Firewall). Ohne Zeile gilt die Ableitung aus dem Topologie-Modell.

Revision ID: 0061
Revises: 0060
Create Date: 2026-10-27
"""

import sqlalchemy as sa
from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "l3out_firewall",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("aci_source_system_id", sa.Integer(), nullable=False),
        sa.Column("l3out_dn", sa.String(length=400), nullable=False),
        sa.Column("l3out_name", sa.String(length=200), nullable=False),
        sa.Column("firewall_id", sa.Integer(), nullable=True),
        sa.Column("vdom", sa.String(length=100), nullable=True),
        sa.Column("interface", sa.String(length=200), nullable=True),
        sa.Column("origin", sa.String(length=12), nullable=False),
        sa.Column("updated_by", sa.String(length=200), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_l3out_firewall_tenant"),
        sa.ForeignKeyConstraint(
            ["aci_source_system_id"], ["source_system.id"], name="fk_l3out_firewall_aci",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["firewall_id"], ["source_system.id"], name="fk_l3out_firewall_firewall",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )  # fmt: skip
    op.create_index("ix_l3out_firewall_tenant_id", "l3out_firewall", ["tenant_id"])
    op.create_index("ix_l3out_firewall_firewall_id", "l3out_firewall", ["firewall_id"])
    op.create_index(
        "ix_l3out_firewall_l3out", "l3out_firewall", ["aci_source_system_id", "l3out_dn"]
    )


def downgrade() -> None:
    for name in ("ix_l3out_firewall_l3out", "ix_l3out_firewall_firewall_id",
                 "ix_l3out_firewall_tenant_id"):  # fmt: skip
        op.drop_index(name, table_name="l3out_firewall")
    op.drop_table("l3out_firewall")
