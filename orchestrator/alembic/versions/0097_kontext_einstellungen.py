"""Einstellungen je Routing-Kontext (ADR 0070)

Die Weg-Suche rechnet wie das Gerät: Reverse Path und Central NAT der FortiGate hängen an
`system settings` des VDOMs (`asymroute`, `strict-src-check`, `central-nat`), an `src-check` je
Interface und an der Tabelle `firewall central-snat-map`. Der Collector liest sie (nur lesend) mit
dem Routing; sie stehen je Kontext in `topo_context.settings` und werden je Lauf ersetzt. Leer =
nicht gelesen (älterer Lauf, kein Leserecht) – die Weg-Suche weist die Stelle dann als „nicht
geprüft“ aus.

Revision ID: 0097_kontext_einstellungen
Revises: 0096_export_auftrag
Create Date: 2026-11-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0097_kontext_einstellungen"
down_revision = "0096_export_auftrag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("topo_context", sa.Column("settings", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("topo_context", "settings")
