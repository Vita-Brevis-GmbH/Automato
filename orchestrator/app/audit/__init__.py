"""Schreibzugriff auf das Audit-Log - einzige Stelle, die AuditLog-Zeilen anlegt.

Lückenlos (SECURITY.md #5): wer (actor), wann (timestamp), was (action), woran
(subject), mit welchem Ergebnis (detail). Der Payload durchläuft zwei Schutzschichten:
1. Allowlist (`allowlist.py`): nur pro Aktion benannte, unkritische Keys werden
   gespeichert; alles andere wird verworfen (fail-closed) -> Secrets landen
   strukturell nie im Log.
2. Optionale At-Rest-Verschlüsselung (`crypto.py`), per Konfiguration zuschaltbar.

Lesen eines Payloads immer über `decode_detail`, damit (ggf.) entschlüsselt wird.
"""

from sqlalchemy.orm import Session

from app.audit.allowlist import sanitize
from app.audit.crypto import decrypt_detail, encrypt_detail
from app.models import AuditLog


def record(
    session: Session,
    *,
    actor: str,
    action: str,
    subject: str,
    detail: dict | None = None,
) -> None:
    safe = sanitize(action, detail)
    session.add(AuditLog(actor=actor, action=action, subject=subject, detail=encrypt_detail(safe)))


def decode_detail(row: AuditLog) -> dict | None:
    """Payload einer AuditLog-Zeile lesbar machen (entschlüsselt, falls nötig)."""
    return decrypt_detail(row.detail)
