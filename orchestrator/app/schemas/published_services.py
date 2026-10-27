from pydantic import BaseModel


class AppRule(BaseModel):
    id: int
    name: str
    action: str
    src_zone: str | None = None
    sources: list[str] = []
    services: list[str] = []


class PublishedServiceOut(BaseModel):
    name: str  # VIP-/Dienstname
    ext_ip: str | None = None
    ext_port: str | None = None
    mapped_ip: str | None = None
    mapped_port: str | None = None
    protocol: str | None = None
    portforward: bool = False
    rule_count: int = 0
    source: str | None = None  # Quellsystem (FortiGate bzw. NSX ALB)
    platform: str | None = None  # fortigate | avi (0.77.0, ADR 0027)
    servers: int = 0  # Server des Pools bzw. Real Server
    pool: str | None = None  # NSX ALB: Pool bzw. Pool Group


class PublishedServiceDetailOut(PublishedServiceOut):
    rules: list[AppRule] = []
