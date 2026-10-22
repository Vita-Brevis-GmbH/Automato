"""Vertrag zwischen Collectors und Orchestrator.

VORLAEUFIG: Dieses normalisierte Regel-DTO ist der Startpunkt fuer das
Datenmodell und wird in Meilenstein M5 finalisiert (Adressobjekt-Referenzen,
kanonische Services, Applikations-Zuordnung). Collectors liefern Regeln in
genau dieser Form an POST /internal/collector-runs/{run_id}/result.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field
from sot_gateway.models import ReferenceRecord


class NormalizedRule(BaseModel):
    source_platform: Literal["fortigate", "cisco_aci"]
    external_id: str  # ID/Ref der Regel im Quellsystem
    name: str = ""
    action: Literal["allow", "deny"]
    sources: list[str] = Field(default_factory=list)  # IPs/Netze/Objektnamen (roh)
    destinations: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)  # z. B. "tcp/443"
    src_zone: str | None = None
    dst_zone: str | None = None
    protections: list[str] = Field(default_factory=list)  # UTM-Profile ("Schutzmechanismen")
    aci_scope: str | None = None  # Contract-Scope (ACI) -> Applikations-Doku-Kategorie
    # Objektnamen wie auf der Firewall (ADR 0010); sources/destinations/services = Werte
    source_refs: list[str] = Field(default_factory=list)
    destination_refs: list[str] = Field(default_factory=list)
    service_refs: list[str] = Field(default_factory=list)
    # FortiGate: IPv6-Objekte getrennt (gleicher Name wie IPv4 = anderes Objekt); aeltere
    # Collectors fuehren alles in source_refs/destination_refs
    source_refs6: list[str] = Field(default_factory=list)
    destination_refs6: list[str] = Field(default_factory=list)
    family: Literal["v4", "v6", "dual"] | None = None
    vdom: str | None = None  # FortiGate: VDOM der Policy (ADR 0019)
    raw: dict[str, Any] | None = None  # Original-Payload fuer Nachvollziehbarkeit


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
    ap: str | None = None  # AP_### (Projekt)
    bd_subnets: list[str] = Field(default_factory=list)  # Subnetze der Bridge Domain
    bd: str | None = None  # BD_...
    subnets: list[str] = Field(default_factory=list)  # fvSubnet (dual-stack)
    endpoints: list[NormalizedEpgEndpoint] = Field(default_factory=list)  # fvCEp/fvIp (gelernt)
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
    vdom: str | None = None  # FortiGate: VDOM des VIP bzw. Pools
    raw: dict[str, Any] | None = None


ObjectCategoryName = Literal[
    "address", "address_group", "vip", "ippool", "service", "service_group"
]


class NormalizedObject(BaseModel):
    """Firewall-Objekt wie auf dem Geraet (Inventar, ADR 0010); Typ + Farbcode als Signal."""

    name: str
    category: ObjectCategoryName = "address"
    obj_type: str | None = None
    color: int | None = None
    value: str | None = None  # einzelnes Adressobjekt: aufgeloester Wert
    values: list[str] = Field(default_factory=list)  # alle Werte / Service-Tokens
    members: list[str] = Field(default_factory=list)  # Gruppen: Mitgliedsnamen
    fqdn: str | None = None
    comment: str | None = None
    family: Literal["v4", "v6"] | None = None
    interface: str | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)  # Services: Protokoll, Ports, Timer ...
    vdom: str | None = None  # FortiGate: gleiche Namen in zwei VDOMs = zwei Objekte


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
    vdom: str | None = None  # FortiGate: VDOM des Interfaces (Zone gilt in diesem VDOM)
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
    vdom: str | None = None  # FortiGate: VDOM des Interfaces


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


class NormalizedNextHop(BaseModel):
    ip: str | None = None  # None = direkt angeschlossen bzw. ohne Gateway
    interface: str | None = None


class NormalizedRoute(BaseModel):
    """Route eines Routing-Kontexts (ADR 0019): FortiGate-VDOM bzw. ACI-VRF auf einem Leaf.

    `table`: rib = wirksame Routing-Tabelle (ECMP = mehrere Next-Hops), bgp = alle BGP-Pfade
    (auch nicht beste, Backup-Wege), static = konfigurierte statische Route (auch inaktive)."""

    node: str  # Geraet bzw. Leaf (fw-01, leaf-101)
    context: str  # VDOM (root) bzw. VRF (Tenant:VRF)
    prefix: str  # CIDR
    table: Literal["rib", "bgp", "static"] = "rib"
    protocol: str | None = None  # connected | static | bgp | ospf | local | ...
    best: bool = True  # wirksam bzw. bester BGP-Pfad bzw. aktive statische Route
    distance: int | None = None
    metric: int | None = None
    next_hops: list[NormalizedNextHop] = Field(default_factory=list)
    attrs: dict[str, Any] = Field(default_factory=dict)  # BGP: as_path, local_pref, origin ...


class NormalizedPeer(BaseModel):
    """BGP-Nachbar mit Zustand (FortiGate je VDOM, ACI je VRF und Border-Leaf)."""

    node: str
    context: str
    peer_ip: str
    local_ip: str | None = None
    remote_as: int | None = None
    state: str | None = None  # Established | Idle | ...
    attrs: dict[str, Any] = Field(default_factory=dict)


class NormalizedHa(BaseModel):
    """HA-Cluster einer FortiGate (Mitglieder, Rolle) und Session-Sync zu anderen Clustern."""

    node: str
    mode: str | None = None  # standalone | a-p | a-a
    group_name: str | None = None
    group_id: int | None = None
    members: list[dict[str, Any]] = Field(default_factory=list)  # hostname, serial, role, prio
    session_pickup: bool | None = None
    sync_peers: list[dict[str, Any]] = Field(default_factory=list)  # FGSP: peer_ip, vdoms ...
    attrs: dict[str, Any] = Field(default_factory=dict)


class NormalizedPolicyRoute(BaseModel):
    """Policy-Route bzw. SD-WAN-Regel: geht fuer passenden Verkehr der Routing-Tabelle vor."""

    node: str
    context: str
    kind: Literal["policy", "sdwan"] = "policy"
    seq: int | None = None
    name: str | None = None
    enabled: bool = True
    match: dict[str, Any] = Field(default_factory=dict)  # src, dst, input_device, protocol ...
    action: str | None = None  # permit | deny | route
    gateway: str | None = None
    output_device: list[str] = Field(default_factory=list)  # SD-WAN: Mitglieder nach Vorrang
    attrs: dict[str, Any] = Field(default_factory=dict)


class NormalizedL3Out(BaseModel):
    """ACI-L3Out: Border-Leafs, Interfaces bzw. SVIs, BGP-Peers und statische Routen."""

    tenant: str
    name: str
    dn: str
    vrf: str | None = None  # Tenant:VRF
    nodes: list[dict[str, Any]] = Field(default_factory=list)  # node, pod, router_id, routes
    paths: list[dict[str, Any]] = Field(default_factory=list)  # path, nodes, kind, encap, ips
    peers: list[dict[str, Any]] = Field(default_factory=list)  # ip, asn, path
    ext_epgs: list[str] = Field(default_factory=list)


class NormalizedRoutingContext(BaseModel):
    """Gelesener Kontext; `missing` = was nicht lesbar war (Aussage dann „unbekannt“)."""

    node: str
    context: str
    kind: str = "vdom"  # vdom | vrf
    missing: list[str] = Field(default_factory=list)


class RoutingInventory(BaseModel):
    """Routing, BGP, HA und L3Out eines Laufs (ADR 0019 Stufe 2) - je Quellsystem ersetzt."""

    contexts: list[NormalizedRoutingContext] = Field(default_factory=list)
    routes: list[NormalizedRoute] = Field(default_factory=list)
    peers: list[NormalizedPeer] = Field(default_factory=list)
    ha: list[NormalizedHa] = Field(default_factory=list)
    policy_routes: list[NormalizedPolicyRoute] = Field(default_factory=list)
    l3outs: list[NormalizedL3Out] = Field(default_factory=list)


class CollectorResult(BaseModel):
    status: Literal["completed", "failed"]
    rules: list[NormalizedRule] = Field(default_factory=list)
    nat_rules: list[NormalizedNat] = Field(default_factory=list)
    objects: list[NormalizedObject] = Field(default_factory=list)
    epgs: list[NormalizedEpg] = Field(default_factory=list)
    inventory: NetworkInventory | None = None  # Netzinventar (ADR 0010)
    virtual: VirtualInventory | None = None  # vCenter: VMs und Portgruppen (ADR 0017)
    routing: RoutingInventory | None = None  # Routing, BGP, HA, L3Out (ADR 0019 Stufe 2)
    # Referenzquellen (Infoblox/NetBox): Eintraege kommen in Teilen ueber .../references,
    # hier nur die Gesamtzahl zur Kontrolle (ADR 0010)
    reference_count: int | None = None
    message: str | None = None
