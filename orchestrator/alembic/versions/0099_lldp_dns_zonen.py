"""Nachbar aus LLDP mit Chassis-ID, Zonen eines AD-DNS-Laufs (1.23.0)

Anwender 2026-11-20 (Test von 1.22.0):

- „die Firewalls werden nicht in der Verkabelung erkannt, LLDP ist aktiv“ - ein Switch meldet die
  FortiGate mit Systemname, Chassis-ID (eine MAC fuer alle Ports) und Port-ID. Bisher blieb nur
  der Systemname (bzw. ohne ihn die Chassis-ID); die Verkabelung ordnet die Gegenstelle jetzt auch
  ueber die Chassis-ID zu (MAC eines gelesenen Interfaces, `store/cabling`).
  `inv_neighbor.remote_chassis`: Chassis-ID wie gemeldet, NULL = nicht geliefert.
- „DNS aus dem AD nicht ganz geprueft … ohne gelesene Rueckwaertszone“, obwohl die Zone
  eingetragen und uebertragen ist (nur SOA/NS, keine PTR). Gelesen ist eine Zone, wenn der Lauf
  sie uebertragen hat - auch ohne Eintraege. `collector_run.zones`: Eintraege je uebertragener
  Zone (AD-DNS, auch 0); NULL = nicht geliefert (aeltere Laeufe: wie bisher nach Eintraegen).
- Rueckweg: Spalten entfernen.

Revision ID: 0099_lldp_dns_zonen
Revises: 0098_epg_durchsetzung
Create Date: 2026-11-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0099_lldp_dns_zonen"
down_revision = "0098_epg_durchsetzung"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inv_neighbor", sa.Column("remote_chassis", sa.String(100), nullable=True))
    op.add_column("collector_run", sa.Column("zones", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("collector_run", "zones")
    op.drop_column("inv_neighbor", "remote_chassis")
