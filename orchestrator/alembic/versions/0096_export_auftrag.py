"""Export im Hintergrund (1.21.0, ADR 0069)

Anwender 2026-11-18: „Zudem geht es sehr lange, bis der Export steht, könnte man dies als
Hintergrunddienst laufen lassen?“

- `export_job`: ein Auftrag je Export der Applikations-Doku - wer ihn gestellt hat, Mandant,
  Stand, Fortschritt, Fehler je Applikation, die fertige Datei (Name, Grösse) und der Ablauf.
  Das Export-Passwort steht nie in der Datenbank. Fällt mit dem Mandanten (`ondelete=CASCADE`).
- Rückweg: Tabelle entfernen (die Dateien im Volume räumt eine ältere Fassung nicht weg; sie sind
  verschlüsselt und werden mit dem nächsten Update wieder aufgeräumt).

Revision ID: 0096_export_auftrag
Revises: 0095_rolle_operator
Create Date: 2026-11-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0096_export_auftrag"
down_revision = "0095_rolle_operator"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "export_job",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("owner", sa.String(length=300), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("done", sa.Integer(), nullable=False),
        sa.Column("current", sa.String(length=200), nullable=True),
        sa.Column("errors", sa.JSON(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("filename", sa.String(length=200), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_export_job_owner", "export_job", ["owner"])
    op.create_index("ix_export_job_tenant_id", "export_job", ["tenant_id"])
    op.create_index("ix_export_job_expires_at", "export_job", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_export_job_expires_at", table_name="export_job")
    op.drop_index("ix_export_job_tenant_id", table_name="export_job")
    op.drop_index("ix_export_job_owner", table_name="export_job")
    op.drop_table("export_job")
