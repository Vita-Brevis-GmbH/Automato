from pydantic import BaseModel

from app.schemas.rules import RuleOut


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
    comment: str | None = None
    interface: str | None = None  # VIP: externes Interface (extintf)
    external_name: str | None = None  # aus der Konvention, z. B. V-EXT_www.example.ch
    ext_dns: list[str] = []  # DNS-Namen der externen IP (DDI/NetBox)
    mapped_dns: list[str] = []  # DNS-Namen der internen IP (DDI/NetBox)
    server: str | None = None  # Host-Objekt mit der internen IP (Firewall-Objekt)
    servers: list[str] = []  # alle Objekte mit dieser IP (mehrere = doppelt definiert)
    rule_count: int = 0  # Firewall-Regeln mit diesem VIP bzw. SNAT-Pool


class NatDetailOut(NatOut):
    ext_info: list[dict] = []  # IPAM je Referenzquelle: DNS-Namen, Netz, VLAN, Site
    mapped_info: list[dict] = []
    color: int | None = None
    settings: dict[str, str] = {}  # weitere Einstellungen wie auf dem Geraet (Typ, ARP, ...)
    rules: list[RuleOut] = []  # Firewall-Regeln mit diesem VIP bzw. SNAT-Pool
