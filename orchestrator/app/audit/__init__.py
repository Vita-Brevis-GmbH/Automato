"""Schreibzugriff auf das Audit-Log - einzige Stelle, die AuditLog-Zeilen anlegt."""

from sqlalchemy.orm import Session

from app.models import AuditLog


def record(
    session: Session,
    *,
    actor: str,
    action: str,
    subject: str,
    detail: dict | None = None,
) -> None:
    session.add(AuditLog(actor=actor, action=action, subject=subject, detail=detail))
