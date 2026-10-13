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
    detail: dict | None = None
    note: str | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
