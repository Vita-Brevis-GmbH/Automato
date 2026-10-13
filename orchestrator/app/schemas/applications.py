from pydantic import BaseModel


class AppRule(BaseModel):
    id: int
    name: str
    action: str
    src_zone: str | None = None
    sources: list[str] = []
    services: list[str] = []


class ApplicationOut(BaseModel):
    name: str  # VIP-/Dienstname
    ext_ip: str | None = None
    ext_port: str | None = None
    mapped_ip: str | None = None
    mapped_port: str | None = None
    protocol: str | None = None
    portforward: bool = False
    rule_count: int = 0


class ApplicationDetailOut(ApplicationOut):
    rules: list[AppRule] = []
