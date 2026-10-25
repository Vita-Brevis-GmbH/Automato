from datetime import datetime
from typing import Literal

from pydantic import BaseModel

PlatformName = Literal["fortigate", "cisco_aci", "infoblox", "netbox", "vmware", "cisco_switch"]


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
    trigger: str = "manual"  # manual | schedule
    queued_at: datetime | None = None
    started_at: datetime | None = None  # abgeholt bzw. vom zentralen Worker gestartet
    finished_at: datetime | None = None


class CollectAllRequest(BaseModel):
    tenant_id: int


class CollectAllItem(BaseModel):
    source_system_id: int
    name: str
    platform: str
    run_id: str | None = None
    execution: str | None = None
    skipped: str | None = None  # Grund, wenn kein Lauf angelegt wurde
    blocking_run_id: str | None = None  # offener Lauf, der das Quellsystem belegt (abbrechbar)


class CollectAllResponse(BaseModel):
    queued: int
    items: list[CollectAllItem]
