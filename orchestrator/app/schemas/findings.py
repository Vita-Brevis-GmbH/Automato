from datetime import datetime

from pydantic import BaseModel


class FindingOut(BaseModel):
    id: int
    tenant_id: int
    type: str
    severity: str
    status: str
    subject_kind: str
    subject_id: str  # i. d. R. der Objektname (bei unmatched_object/invalid_name)
    source_system_id: int | None = None  # Quellsystem, auf dessen Daten der Befund beruht
    detail: dict | None = None
    message: str | None = None  # Befund in einem Satz: was genau nicht stimmt
    hint: str | None = None  # was zu tun waere (am Quellsystem; Automato liest nur)
    note: str | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
