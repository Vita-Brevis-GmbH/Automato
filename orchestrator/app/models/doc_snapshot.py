"""Stand der Doku je Applikation (0.108.0, ADR 0037): im Hintergrund berechnet und gespeichert.

Je Applikation, Art (`doc` = Doku, `connections` = Verbindungen mit Plan) und Variante (Sprache,
Namen der Gegenstellen, Ausschnitt eines Kunden) eine Zeile mit dem Ergebnis (JSON, gepackt) und
dem Fingerabdruck seiner Eingaben (`store/doc_snapshots.py`). Abgeleitete Daten: jederzeit neu
berechenbar, nie Quelle - beim Löschen der Applikation fallen sie mit weg.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, ForeignKey, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DocSnapshot(Base):
    __tablename__ = "doc_snapshot"
    __table_args__ = (UniqueConstraint("application_id", "kind", "variant"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("application.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))  # doc | connections
    variant: Mapped[str] = mapped_column(String(64))  # Sprache|Namen|Ausschnitt
    fingerprint: Mapped[str] = mapped_column(String(64))
    built_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    seconds: Mapped[float] = mapped_column(Float, default=0.0)  # Dauer der Berechnung
    data: Mapped[bytes] = mapped_column(LargeBinary)  # JSON, zlib
    viewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
