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


class NatServerOut(BaseModel):
    """Ziel-Server eines VIP (0.96.0): direkt bzw. ueber einen Load Balancer (`via`)."""

    ip: str | None = None
    port: str | None = None
    status: str | None = None  # active | standby | disable
    via: str | None = None  # Virtual Service des NSX ALB, ueber den der Server erreicht wird
    names: list[str] = []  # Firewall-Objekte mit dieser IP
    dns: list[str] = []  # DNS-Namen aus DDI/NetBox


class NatChainOut(BaseModel):
    """Kette einer VIP der Firewall ueber einen Load Balancer (0.96.0): externe IP:Port →
    interne IP (VIP des Load Balancers) → Virtual Service → Pool → Server."""

    ext_ip: str | None = None
    ext_port: str | None = None
    vip_ip: str | None = None  # VIP des Load Balancers = internes Ziel der VIP der Firewall
    vip_port: str | None = None
    via: str = "ip"  # ip | fqdn (FQDN-VIP auf den FQDN des Virtual Service)
    balancer_id: int
    balancer: str  # Name des Virtual Service
    source: str | None = None  # Quellsystem des Load Balancers
    source_system_id: int | None = None
    pool: str | None = None
    tenant: str | None = None  # Avi-Tenant
    vrf: str | None = None
    floating: bool = False  # Floating IP des Virtual Service
    down: bool = False  # Virtual Service aus bzw. OPER_DOWN
    servers: list[NatServerOut] = []


class NatFrontOut(BaseModel):
    """VIP einer Firewall vor einem Virtual Service (0.96.0, umgekehrte Kette)."""

    id: int
    name: str
    source: str | None = None
    source_system_id: int
    vdom: str | None = None
    ext_ip: str | None = None  # externe Adresse, die zum Virtual Service fuehrt
    ext_port: str | None = None
    via: str = "ip"


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
    source: str | None = None  # Name des Quellsystems (Firewall bzw. Load Balancer)
    # 0.96.0: Kette ueber den Load Balancer, VIPs davor, Ziel-Server (direkt bzw. ueber LB),
    # VIP-Gruppen der Firewall mit diesem VIP
    chain: list[NatChainOut] = []
    fronted_by: list[NatFrontOut] = []
    dest_servers: list[NatServerOut] = []
    groups: list[str] = []


class NatDetailOut(NatOut):
    ext_info: list[dict] = []  # IPAM je Referenzquelle: DNS-Namen, Netz, VLAN, Site
    mapped_info: list[dict] = []
    color: int | None = None
    object_id: int | None = None  # VIP- bzw. Pool-Objekt (Link in die Objekt-Ansicht)
    settings: dict[str, str] = {}  # weitere Einstellungen wie auf dem Geraet (Typ, ARP, ...)
    rules: list[RuleOut] = []  # Firewall-Regeln mit diesem VIP bzw. SNAT-Pool
    vms: list[dict] = []  # VMs hinter dem internen Ziel (vCenter), `for_ip` = welche IP
