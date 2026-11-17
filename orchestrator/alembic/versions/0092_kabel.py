"""Verkabelung im Detail: Transceiver je Port und Kabelnummern (1.19.0, ADR 0031 Nachtrag)

Anwender 2026-11-17: „Verkabelung im Detail“ - „Kabelnummern können auch in Automato erfasst
werden, beides möglich“.

- `inv_transceiver`: Transceiver je Port eines Switches (Typ, Hersteller, Teilenummer,
  Seriennummer), nur gelesen und je Lauf ersetzt wie das uebrige Netzinventar;
- `inv_device.transceivers`: Lesestatus der Transceiver je Switch (read | unreadable |
  unsupported) - was nicht geht, wird ausgewiesen;
- `cable_label`: Kabelnummer je Port-Paar, in Automato erfasst (Laufzeitdaten je Mandant, eine
  Erfassung je Paar); der NetBox-Sync schreibt sie als Label des Kabels.

Revision ID: 0092_kabel
Revises: 0089_vpn
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0092_kabel"
down_revision = "0089_vpn"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inv_transceiver",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "source_system_id", sa.Integer(), sa.ForeignKey("source_system.id"), nullable=False
        ),
        sa.Column("device", sa.String(200), nullable=False),
        sa.Column("port", sa.String(200), nullable=False),
        sa.Column("type", sa.String(100), nullable=True),
        sa.Column("vendor", sa.String(100), nullable=True),
        sa.Column("part", sa.String(100), nullable=True),
        sa.Column("serial", sa.String(100), nullable=True),
    )
    op.create_index("ix_inv_transceiver_tenant_id", "inv_transceiver", ["tenant_id"])
    op.create_index(
        "ix_inv_transceiver_source_system_id", "inv_transceiver", ["source_system_id"]
    )
    op.add_column("inv_device", sa.Column("transceivers", sa.String(16), nullable=True))
    op.create_table(
        "cable_label",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("pair", sa.String(500), nullable=False),
        sa.Column("a_device", sa.String(200), nullable=False),
        sa.Column("a_port", sa.String(200), nullable=False),
        sa.Column("b_device", sa.String(200), nullable=False),
        sa.Column("b_port", sa.String(200), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("changed_by", sa.String(200), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "pair", name="uq_cable_label_tenant_pair"),
    )
    op.create_index("ix_cable_label_tenant_id", "cable_label", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_cable_label_tenant_id", table_name="cable_label")
    op.drop_table("cable_label")
    op.drop_column("inv_device", "transceivers")
    op.drop_index("ix_inv_transceiver_source_system_id", table_name="inv_transceiver")
    op.drop_index("ix_inv_transceiver_tenant_id", table_name="inv_transceiver")
    op.drop_table("inv_transceiver")
