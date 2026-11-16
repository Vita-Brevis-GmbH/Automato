"""Kopplung mit einer Gegenstelle ueber einen Einmal-Code (1.13.0, ADR 0057).

Eine Zeile je Gegenstelle (`peer`, heute nur `cockpit`; spaeter z. B. der Agent des
NetBox-Hosts). Sie haelt hoechstens einen offenen Einmal-Code und hoechstens ein gekoppeltes
Token - beide nur als SHA-256-Hash (hex), nie im Klartext. Der Code gilt 15 Minuten, einmal und
hoechstens bis zum fuenften Fehlversuch; ein neuer Code ersetzt den offenen, ein neues Koppeln
das bisherige Token (`store/kopplung.py`).
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Coupling(Base):
    __tablename__ = "coupling"

    id: Mapped[int] = mapped_column(primary_key=True)
    peer: Mapped[str] = mapped_column(String(32), unique=True)
    # offener Einmal-Code: sha256 des Codes (ohne Trennzeichen), gueltig bis, Fehlversuche, von wem
    code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    code_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    code_failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    code_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # gekoppelt: sha256 des Tokens, Name der Gegenstelle (von ihr gemeldet), wann, wer den Code
    # erzeugt hat, zuletzt benutzt (hoechstens alle 5 Minuten nachgefuehrt)
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    peer_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    coupled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    coupled_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_used: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
