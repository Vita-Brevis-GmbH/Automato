"""Kategorien einfach: ohne Bereich je Zone und ohne Ausgaenge von Hand (0.106.0, ADR 0035)

Anwender 2026-10-28: „Kategorien und Bereiche sind nicht notwendig“ und „die Ausgaenge muessten
aus den Daten ersichtlich sein, wenn ich den Zonen Geraete zuweise“. Die Kategorie folgt nur noch
dem Zonentyp (Richtung im Namenskonzept); welche Zone hinter einem Interface bzw. L3Out liegt,
ergibt sich aus den Geraeten der Zonen und dem Routing.

- `logical_zone.appdoc_area` (0.101.0) entfaellt;
- Selektoren `via_fw_interface`, `via_fw_zone`, `via_l3out` (0.105.0) werden entfernt.

Downgrade: Spalte wieder da (leer); die entfernten Selektoren kommen nicht zurueck.

Revision ID: 0065
Revises: 0064
Create Date: 2026-10-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM logical_zone_selector "
        "WHERE kind IN ('via_fw_interface', 'via_fw_zone', 'via_l3out')"
    )
    with op.batch_alter_table("logical_zone") as batch:
        batch.drop_column("appdoc_area")


def downgrade() -> None:
    op.add_column("logical_zone", sa.Column("appdoc_area", sa.String(length=8), nullable=True))
