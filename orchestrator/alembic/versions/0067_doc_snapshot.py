"""Stand der Doku je Applikation (0.108.0, ADR 0037)

Anwender 2026-10-28: „das Speichern dauert zu lange“, Doku im Hintergrund rechnen. Je
Applikation, Art (Doku, Verbindungen) und Variante das zuletzt berechnete Ergebnis mit dem
Fingerabdruck seiner Eingaben - abgeleitet, jederzeit neu berechenbar.

Revision ID: 0067
Revises: 0066
Create Date: 2026-10-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "doc_snapshot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("variant", sa.String(length=64), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seconds", sa.Float(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["application.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "kind", "variant"),
    )
    op.create_index("ix_doc_snapshot_application_id", "doc_snapshot", ["application_id"])


def downgrade() -> None:
    op.drop_index("ix_doc_snapshot_application_id", table_name="doc_snapshot")
    op.drop_table("doc_snapshot")
