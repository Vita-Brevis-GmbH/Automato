from pydantic import BaseModel

from app.schemas.rules import RuleOut


class NatTargetOut(BaseModel):
    """Real Server eines Virtual Servers (FortiGate server-load-balance)."""

    ip: str | None = None
    address: str | None = None  # statt IP ein Adressobjekt (z. B. FQDN)
    port: int | str | None = None
    status: str | None = None  # active | standby | disable
    weight: int | str | None = None
    healthcheck: str | None = None  # enable | disable | vip (Health-Check des VIPs)
    monitor: list[str] = []
    holddown: int | str | None = None
    max_connections: int | str | None = None
    http_host: str | None = None
    servers: list[str] = []  # Firewall-Objekte mit dieser IP
    dns: list[str] = []  # DNS-Namen aus DDI/NetBox


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
    vip_type: str | None = None  # static-nat | server-load-balance (Virtual Server) | ...
    family: str | None = None  # v4 | v6
    vdom: str | None = None  # FortiGate-VDOM (ADR 0019)
    targets: list[NatTargetOut] = []  # Virtual Server: Real Server
    attrs: dict = {}  # Virtual Server: Server-Typ, Methode, Persistenz, Health-Checks
    # Korrektheitspruefung (0.14.0): Befunde des VIP-/Pool-Objekts, internes Ziel ohne IPAM
    issues: list[str] = []


class NatDetailOut(NatOut):
    ext_info: list[dict] = []  # IPAM je Referenzquelle: DNS-Namen, Netz, VLAN, Site
    mapped_info: list[dict] = []
    color: int | None = None
    object_id: int | None = None  # VIP- bzw. Pool-Objekt (Link in die Objekt-Ansicht)
    settings: dict[str, str] = {}  # weitere Einstellungen wie auf dem Geraet (Typ, ARP, ...)
    rules: list[RuleOut] = []  # Firewall-Regeln mit diesem VIP bzw. SNAT-Pool
    vms: list[dict] = []  # VMs hinter dem internen Ziel (vCenter), `for_ip` = welche IP
