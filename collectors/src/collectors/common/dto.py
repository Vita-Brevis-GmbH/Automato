"""Normalisiertes Regel-DTO - Vertrag mit dem Orchestrator.

Muss zu orchestrator/app/schemas/collector.py passen (dort getestet).
VORLAEUFIG bis M5; danach ggf. gemeinsames contracts-Package.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field
from sot_gateway.models import ReferenceRecord


class NormalizedRule(BaseModel):
    source_platform: Literal["fortigate", "cisco_aci"]
    external_id: str
    name: str = ""
    action: Literal["allow", "deny"]
    sources: list[str] = Field(default_factory=list)
    destinations: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    src_zone: str | None = None
    dst_zone: str | None = None
    # Schutzmechanismen (FortiGate-UTM-Profile an der Policy): WEB/WF, APP/AC, IPS,
    # WAF, SSL, AV, DNS-Filter - fuer die Applikations-Doku-Spalte "Schutzmechanismen".
    protections: list[str] = Field(default_factory=list)
    aci_scope: str | None = None  # Contract-Scope (ACI) -> Applikations-Doku-Kategorie
    # Objektnamen wie auf der Firewall (ADR 0010); sources/destinations/services = Werte
    source_refs: list[str] = Field(default_factory=list)
    destination_refs: list[str] = Field(default_factory=list)
    service_refs: list[str] = Field(default_factory=list)
    # FortiGate: IPv6-Objekte (srcaddr6/dstaddr6, policy6) getrennt - gleiche Namen wie IPv4
    # sind andere Objekte; family = v4 | v6 | dual nach den Adressfeldern der Policy
    source_refs6: list[str] = Field(default_factory=list)
    destination_refs6: list[str] = Field(default_factory=list)
    family: str | None = None
    raw: dict[str, Any] | None = None


class NormalizedEpgEndpoint(BaseModel):
    ip: str
    mac: str | None = None


class NormalizedEpg(BaseModel):
    """ACI-EPG (Model 1): Server-Gruppierung mit AP-Klammer (Projekt) + Subnetzen."""

    name: str
    kind: str = "epg"  # epg | esg | external (L3Out-EPG) | vzany (alle EPGs der VRF)
    tenant: str | None = None  # ACI-Tenant
    dn: str | None = None  # eindeutig ueber alle ACI-Tenants (aeltere Collectors: ohne)
    vrf: str | None = None
    l3out: str | None = None  # externe EPG: L3Out
    ap: str | None = None
    bd_subnets: list[str] = Field(default_factory=list)  # Subnetze der Bridge Domain
    bd: str | None = None
    subnets: list[str] = Field(default_factory=list)
    endpoints: list[NormalizedEpgEndpoint] = Field(default_factory=list)  # fvCEp/fvIp
    selectors: list[str] = Field(default_factory=list)  # ESG: Selektoren (EPG, Tag, IP)
    pref_group: bool = False  # Mitglied der Preferred Group seiner VRF


class NormalizedNat(BaseModel):
    """NAT: VIP/DNAT (extip -> mappedip) oder SNAT-IP-Pool (externe Quell-IP)."""

    source_platform: Literal["fortigate", "cisco_aci"]
    external_id: str
    name: str = ""
    nat_type: Literal["dnat_vip", "snat_pool"] = "dnat_vip"
    ext_ip: str | None = None
    ext_port: str | None = None
    mapped_ip: str | None = None
    mapped_port: str | None = None
    protocol: str | None = None
    portforward: bool = False
    # VIP-Typ (static-nat | server-load-balance = Virtual Server | fqdn | ...) und Familie
    vip_type: str | None = None
    family: str | None = None
    # Virtual Server: Real Server (ip/address, port, status, weight, Health-Check ...)
    targets: list[dict[str, Any]] = Field(default_factory=list)
    # Virtual Server: Server-Typ, Methode, Persistenz, Health-Checks, Zertifikat (nur Name)
    attrs: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] | None = None


ObjectCategory = Literal["address", "address_group", "vip", "ippool", "service", "service_group"]


class NormalizedObject(BaseModel):
    """Firewall-Objekt, wie es auf dem Geraet steht (Inventar, ADR 0010).

    Typ + Farbcode sind zugleich Signal fuer die Einordnung (ADR 0006)."""

    name: str
    category: ObjectCategory = "address"
    obj_type: str | None = None  # ipmask | interface-subnet | iprange | fqdn | geography | ...
    color: int | None = None  # FortiGate-Farbcode (optional)
    value: str | None = None  # aufgeloeste IP/CIDR, falls vorhanden (einzelnes Adressobjekt)
    values: list[str] = Field(default_factory=list)  # alle aufgeloesten Werte/Service-Tokens
    members: list[str] = Field(default_factory=list)  # Gruppen: Mitgliedsnamen
    fqdn: str | None = None
    comment: str | None = None
    family: Literal["v4", "v6"] | None = None
    interface: str | None = None  # associated-interface / extintf
    # zusaetzliche Attribute wie auf dem Geraet (Services: Protokoll, Ports inkl. Quellports,
    # ICMP-Typ, Kategorie, Timer; ACI-Filter: Eintraege)
    attrs: dict[str, Any] = Field(default_factory=dict)


class NormalizedDevice(BaseModel):
    """Geraet des Netzinventars: Firewall, ACI-Leaf/-Spine/-Controller (ADR 0010)."""

    name: str
    role: str  # firewall | leaf | spine | controller
    model: str | None = None
    serial: str | None = None
    node_id: str | None = None
    mgmt_ip: str | None = None
    version: str | None = None


class NormalizedInterface(BaseModel):
    """Port/Interface mit VLAN-Konfiguration (FortiGate-Interface, ACI-Leaf-Port, PC/vPC)."""

    device: str
    name: str
    kind: str = "physical"  # physical | vlan | lag | loopback | tunnel | virtual
    enabled: bool | None = None
    mode: Literal["access", "tagged"] | None = None
    untagged_vlan: int | None = None
    tagged_vlans: list[int] = Field(default_factory=list)
    lag: str | None = None  # Bundle (aggregate / PC / vPC), zu dem der Port gehoert
    parent: str | None = None  # VLAN-Interface: physischer Port
    ips: list[str] = Field(default_factory=list)  # Interface-Adressen mit Laenge
    description: str | None = None
    zone: str | None = None
    neighbor: str | None = None  # LLDP/CDP: Systemname der Gegenstelle
    neighbor_port: str | None = None
    policy_group: str | None = None  # ACI Access Policy Group
    epgs: list[str] = Field(default_factory=list)  # ACI: EPGs mit statischem Pfad


class NormalizedVlan(BaseModel):
    vid: int
    name: str
    group: str | None = None  # VLAN-Gruppe (None = Quellsystem)
    description: str | None = None
    epgs: list[str] = Field(default_factory=list)  # ACI: Tenant/AP/EPG mit diesem VLAN


class NormalizedSubnet(BaseModel):
    """Reales Subnetz: FortiGate-Interface-Netz, ACI-BD- oder EPG-Subnetz."""

    prefix: str  # Netzadresse, z. B. 10.1.1.0/24
    gateway: str | None = None  # Interface-/BD-Adresse mit Laenge, z. B. 10.1.1.1/24
    vrf: str | None = None
    vlan: int | None = None
    context: Literal["interface", "bd", "epg"] = "interface"
    context_name: str = ""
    aci_tenant: str | None = None
    ap: str | None = None
    epg: str | None = None
    bd: str | None = None
    description: str | None = None


class NetworkInventory(BaseModel):
    devices: list[NormalizedDevice] = Field(default_factory=list)
    interfaces: list[NormalizedInterface] = Field(default_factory=list)
    vlans: list[NormalizedVlan] = Field(default_factory=list)
    subnets: list[NormalizedSubnet] = Field(default_factory=list)
    vrfs: list[str] = Field(default_factory=list)


class ReferenceBatch(BaseModel):
    """Teil-Upload einer Referenzquelle (Infoblox DDI / NetBox komplett gelesen)."""

    records: list[ReferenceRecord] = Field(default_factory=list)


class NormalizedVmNic(BaseModel):
    """Netzwerkkarte einer VM: Portgruppe (VMware) mit VLAN, MAC und IPs aus den VMware Tools."""

    name: str  # "Network adapter 1"
    mac: str | None = None
    portgroup: str | None = None
    switch: str | None = None  # verteilter bzw. Standard-Switch
    vlan: int | None = None
    connected: bool | None = None
    ips: list[str] = Field(default_factory=list)


class NormalizedVm(BaseModel):
    """VM aus dem vCenter (ADR 0017) - nur Inventar, keine Gast-Inhalte."""

    name: str
    uuid: str | None = None  # instanceUuid
    power_state: str | None = None  # poweredOn | poweredOff | suspended
    template: bool = False
    guest_os: str | None = None
    hostname: str | None = None  # Gast-Hostname (VMware Tools)
    datacenter: str | None = None
    cluster: str | None = None
    host: str | None = None
    vcpus: int | None = None
    memory_mb: int | None = None
    disk_gb: float | None = None
    annotation: str | None = None
    nics: list[NormalizedVmNic] = Field(default_factory=list)


class NormalizedPortgroup(BaseModel):
    """Portgruppe: verteilt (dvs) bzw. Standard je Host; VLAN, Trunk-Bereiche oder PVLAN."""

    name: str
    kind: str = "dvs"  # dvs | standard
    switch: str | None = None
    key: str | None = None  # dvPortgroup-Key
    vlan: int | None = None
    vlan_ranges: str | None = None  # Trunk, z. B. "100-199,300"
    vm_count: int = 0


class VirtualInventory(BaseModel):
    """vCenter-Inventar eines Laufs: VMs und Portgruppen (ADR 0017)."""

    vms: list[NormalizedVm] = Field(default_factory=list)
    portgroups: list[NormalizedPortgroup] = Field(default_factory=list)


class CollectorResult(BaseModel):
    status: Literal["completed", "failed"]
    rules: list[NormalizedRule] = Field(default_factory=list)
    nat_rules: list[NormalizedNat] = Field(default_factory=list)
    objects: list[NormalizedObject] = Field(default_factory=list)
    epgs: list[NormalizedEpg] = Field(default_factory=list)
    inventory: NetworkInventory | None = None  # Netzinventar (ADR 0010)
    virtual: VirtualInventory | None = None  # vCenter: VMs und Portgruppen (ADR 0017)
    # Referenzquellen (Infoblox/NetBox): Eintraege kommen in Teilen ueber .../references,
    # hier nur die Gesamtzahl zur Kontrolle (ADR 0010)
    reference_count: int | None = None
    message: str | None = None


class Target(BaseModel):
    """Zu lesendes Geraet inkl. Zugangsdaten vom Orchestrator (ADR 0009).

    Der zugewiesene Agent bekommt es beim Abholen eines Laufs. Zugangsdaten je Plattform:
    FortiGate `token`, Cisco ACI `username`/`password`; sie fehlen, wenn am Geraet keine
    hinterlegt sind (dann gilt die lokale Konfiguration).
    """

    source_system_id: int | None = None
    name: str = ""
    platform: str = "fortigate"
    base_url: str = ""
    verify_tls: bool = True
    read_scope: str = ""  # ACI: zu lesende Tenants (kommagetrennt, leer = alle)
    token: str | None = None
    username: str | None = None
    password: str | None = None
