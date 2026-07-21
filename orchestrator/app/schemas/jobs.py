from typing import Literal

from pydantic import BaseModel

PlatformName = Literal["fortigate", "cisco_aci"]


class CollectJobRequest(BaseModel):
    platform: PlatformName


class CollectJobResponse(BaseModel):
    run_id: str
    status: str


class CollectorRunOut(BaseModel):
    id: str
    platform: str
    status: str
    rule_count: int | None = None
    message: str | None = None
