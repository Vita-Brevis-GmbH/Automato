from pydantic import BaseModel


class NatOut(BaseModel):
    id: int
    tenant_id: int
    source_system_id: int
    name: str
    external_id: str
    nat_type: str = "dnat_vip"  # dnat_vip | snat_pool
    ext_ip: str | None = None
    ext_port: str | None = None
    mapped_ip: str | None = None
    mapped_port: str | None = None
    protocol: str | None = None
    portforward: bool = False
