"""Indizes fuer die Wege grosser Applikationen (0.133.1)

Anwender 2026-11-04: „Das Berechnen hat immer noch über eine Stunde für eine riesige App … woran
kann es liegen, dass bei mir es ewig dauert und bei dir Sekunden?“ – je Verbindung und Hop fragte
die Weg-Suche BGP- bzw. statische Routen gleichen Praefixes (Backup-Wege) und gelernte Endpunkte
einer IP ab; ohne passenden Index las Postgres dafuer jedes Mal alle Routen des Geraets bzw. alle
Endpunkte (bei grossen BGP-Tabellen Hunderte Millisekunden je Abfrage).

Revision ID: 0073
Revises: 0072
Create Date: 2026-11-04
"""

from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_topo_route_node_prefix", "topo_route",
                    ["source_system_id", "context", "prefix"])  # fmt: skip
    op.create_index("ix_epg_endpoint_ip", "epg_endpoint", ["ip"])


def downgrade() -> None:
    op.drop_index("ix_epg_endpoint_ip", table_name="epg_endpoint")
    op.drop_index("ix_topo_route_node_prefix", table_name="topo_route")
