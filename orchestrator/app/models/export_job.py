"""Export im Hintergrund (1.21.0, ADR 0069).

Anwender 2026-11-18: „Zudem geht es sehr lange, bis der Export steht, könnte man dies als
Hintergrunddienst laufen lassen?“ - eine Zeile je Auftrag: wer ihn gestellt hat (`owner`, nur er
ruft ihn ab), Mandant, Stand (wartet, läuft, fertig, Fehler, abgebrochen, abgebrochen durch einen
Neustart), Fortschritt (n von m, aktuelle Applikation), Fehler je Applikation und die fertige
Datei (Name, Grösse) bis zum Ablauf. Das Export-Passwort steht nie hier - es lebt nur im
Speicher des Auftrags (`store/export_jobs.py`); die Datei liegt im Volume des Orchestrators.
Fällt mit dem Mandanten (`ondelete=CASCADE`).
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ExportJob(Base):
    __tablename__ = "export_job"

    # zufaellige Kennung (uuid4 hex) - nicht erratbar, auch in Pfad und Dateiname
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))  # applications
    owner: Mapped[str] = mapped_column(String(300), index=True)  # Principal.subject
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    # waiting | running | done | failed | cancelled | aborted (Neustart)
    status: Mapped[str] = mapped_column(String(16))
    total: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    current: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Fehler je Applikation: [{application_id, name, error}] - technischer Text, nie das Passwort
    errors: Mapped[list | None] = mapped_column(JSON, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)  # Grund bei Fehler/Abbruch
    filename: Mapped[str | None] = mapped_column(String(200), nullable=True)
    size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
