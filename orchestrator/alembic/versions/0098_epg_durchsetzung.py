"""EPG: Intra-EPG-Isolation und VRF ohne Contracts (ADR 0071)

Regel-Zuordnung nach echtem Treffer: das Fabric sperrt Verkehr innerhalb einer EPG mit
Intra-EPG-Isolation und wendet in einer VRF im Modus „unenforced“ keine Contracts an. Beides
stand bisher nur als „nicht beruecksichtigt“ im Code (`aci_check`).

- `epg.isolated`: `pcEnfPref=enforced` an der EPG; `epg.vrf_unenforced`: `pcEnfPref=unenforced`
  an ihrer VRF. Beide ohne Vorgabe (NULL = nicht gelesen, bis zum naechsten Lauf des APIC).
- Rueckweg: Spalten entfernen.

Die Nummer 0097 gehoert der Weg-Suche (ADR 0070); beim Zusammenfuehren zeigt `down_revision`
auf deren Migration, falls sie eine bringt.

Revision ID: 0098_epg_durchsetzung
Revises: 0096_export_auftrag
Create Date: 2026-11-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0098_epg_durchsetzung"
down_revision = "0096_export_auftrag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("epg", sa.Column("isolated", sa.Boolean(), nullable=True))
    op.add_column("epg", sa.Column("vrf_unenforced", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("epg", "vrf_unenforced")
    op.drop_column("epg", "isolated")
