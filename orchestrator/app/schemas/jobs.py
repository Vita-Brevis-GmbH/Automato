from datetime import datetime
from typing import Literal

from pydantic import BaseModel

PlatformName = Literal["fortigate", "cisco_aci"]


class CollectJobRequest(BaseModel):
    platform: PlatformName
    # Optional: Ziel des Laufs. Ohne diese Angaben wird das Ergebnis nur gezaehlt/
    # auditiert, aber nicht in den Rule & Service Store persistiert (M7).
    tenant_id: int | None = None
    source_system_id: int | None = None
    # inprocess: zentraler Celery-Worker erfasst. external: der Lauf wird nur angelegt,
    # ein externer Collector (ADR 0008, z. B. Windows-Agent) erfasst und liefert via
    # /internal das Ergebnis ab - fuer Zielsysteme, die nur der Agent erreicht.
    execution: Literal["inprocess", "external"] = "inprocess"


class CollectJobResponse(BaseModel):
    run_id: str
    status: str
    execution: str = "inprocess"


class CollectorRunOut(BaseModel):
    id: str
    platform: str
    status: str
    tenant_id: int | None = None
    source_system_id: int | None = None
    rule_count: int | None = None
    message: str | None = None
    execution: str = "inprocess"
    queued_at: datetime | None = None
    finished_at: datetime | None = None
