from pydantic import BaseModel


class RuleOut(BaseModel):
    id: int
    tenant_id: int
    source_system_id: int
    domain: str
    name: str
    action: str
    section_label: str | None = None
