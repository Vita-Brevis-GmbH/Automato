"""Abbau mit Sperre und Loeschauftraegen, Ist-Stand der Instanzen (ADR 0059)

Anwender 2026-11-16 auf den Abbau-Plan mit Frist („zum Entfernen vorgemerkt“): „Einstellbar, aber
sonst ok, Dann sollten wir auch noch die Instanzen auslesen, damit wir sehen können, ob wir
altlasten haben“.

- `portal_customer.gesperrt`: vom Cockpit zum Entfernen vorgemerkt - der Front-Proxy sperrt alle
  Adressen des Kunden, Daten bleiben. Vorgabe aus.
- `abbau_auftrag`: Loeschauftraege aus dem Cockpit (`PUT /freigaben`, `entfernen`) je Kunde mit dem
  Stand je Teil (NetBox, Kunde in Automato, Anmelde-Tor).
- `ist_meldung`: letzte Meldung eines Werkzeugs zu seinem Ist-Stand (Agent des NetBox-Hosts).

Revision ID: 0087
Revises: 0085
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0087"
down_revision = "0085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("portal_customer") as batch:
        batch.add_column(
            sa.Column("gesperrt", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
    op.create_table(
        "abbau_auftrag",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("short_name", sa.String(length=42), nullable=False),
        sa.Column("kennung", sa.String(length=32), nullable=False),
        sa.Column("realm", sa.String(length=64), nullable=True),
        sa.Column("netbox", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("automato_kunde", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("tor", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("automato_kunden", sa.JSON(), nullable=False),
        sa.Column("teile", sa.JSON(), nullable=False),
        sa.Column("angenommen_am", sa.DateTime(timezone=True), nullable=False),
        sa.Column("erledigt_am", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_abbau_auftrag_short_name", "abbau_auftrag", ["short_name"], unique=True)
    op.create_table(
        "ist_meldung",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("quelle", sa.String(length=32), nullable=False),
        sa.Column("daten", sa.JSON(), nullable=False),
        sa.Column("empfangen", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("quelle"),
    )


def downgrade() -> None:
    op.drop_table("ist_meldung")
    op.drop_index("ix_abbau_auftrag_short_name", table_name="abbau_auftrag")
    op.drop_table("abbau_auftrag")
    with op.batch_alter_table("portal_customer") as batch:
        batch.drop_column("gesperrt")
