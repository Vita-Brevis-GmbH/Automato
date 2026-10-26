"""Herkunft der EPG-Endpunkte: statisch, VMM oder gelernt (0.46.0)

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-23

- `epg_endpoint.origin`: `static` (fvStCEp bzw. fvCEp mit lcC static - Konfiguration), `vmm`
  (das ACI kennt die VM vom vCenter) bzw. `learned` (vom Fabric gelernt). Konfiguration geht
  bei der Zuordnung einer IP zur EPG vor Gelerntem (R12, Anwender). Altbestand: leer = gelernt.
"""

import sqlalchemy as sa
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("epg_endpoint", sa.Column("origin", sa.String(8), nullable=True))


def downgrade() -> None:
    op.drop_column("epg_endpoint", "origin")
