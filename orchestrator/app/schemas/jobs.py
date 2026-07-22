from typing import Literal

from pydantic import BaseModel

PlatformName = Literal["fortigate", "cisco_aci"]


class CollectJobRequest(BaseModel):
    platform: PlatformName
    # Optional: Ziel des Laufs. Ohne diese Angaben wird das Ergebnis nur gezaehlt/
    # auditiert, aber nicht in den Rule & Service Store persistiert (M7).
    tenant_id: int | None = None
    source_system_id: int | None = None


class CollectJobResponse(BaseModel):
    run_id: str
    status: str


class CollectorRunOut(BaseModel):
    id: str
    platform: str
    status: str
    tenant_id: int | None = None
    source_system_id: int | None = None
    rule_count: int | None = None
    message: str | None = None
