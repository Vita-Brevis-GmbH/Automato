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
    # FortiGate: VDOM der Policy (ADR 0019) - Policy-ID und Objektnamen gelten je VDOM
    vdom: str | None = None
    raw: dict[str, Any] | None = None


class NormalizedEpgEndpoint(BaseModel):
    ip: str
    mac: str | None = None
    origin: str | None = None  # static (fvStCEp) | vmm | learned (fvCEp, 0.46.0)


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
    # Scope je Subnetz: externe EPG `import-security` = Zuordnung (fehlt = APIC-Standard); EPG
    # `fvSubnet.scope` mit `shared` = zwischen VRFs geteilt (Shared Services, 0.44.0)
    subnet_scopes: dict[str, str] = Field(default_factory=dict)
    bd_subnet_scopes: dict[str, str] = Field(default_factory=dict)  # Scope der BD-Subnetze
    vrf_tenant: str | None = None  # ACI-Tenant der VRF (aufgeloest: eigener bzw. common)
    endpoints: list[NormalizedEpgEndpoint] = Field(default_factory=list)  # fvCEp/fvIp
    selectors: list[str] = Field(default_factory=list)  # ESG: Selektoren (EPG, Tag, IP)
    pref_group: bool = False  # Mitglied der Preferred Group seiner VRF


class NormalizedNat(BaseModel):
    """NAT: VIP/DNAT (extip -> mappedip) oder SNAT-IP-Pool (externe Quell-IP)."""

    source_platform: Literal["fortigate", "cisco_aci", "avi"]  # avi: NSX ALB (ADR 0027)
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
    kind: str = "physical"  # physical | vlan | lag | loopback | tunnel | vdom-link | virtual
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
    role: str | None = None  # FortiGate: lan | wan | dmz | management (0.54.0)
    neighbor: str | None = None  # LLDP/CDP: Systemname der Gegenstelle
    neighbor_port: str | None = None
    policy_group: str | None = None  # ACI Access Policy Group
    epgs: list[str] = Field(default_factory=list)  # ACI: EPGs mit statischem Pfad
    # Switches (0.62.0, ADR 0025): Betriebszustand, Speed/Duplex wie vom Switch gemeldet, Native
    # VLAN des Trunks, erlaubte VLANs als Bereiche (`10,20,30-40`), Medium und vPC in `attrs`
    status: str | None = None
    speed: str | None = None
    duplex: str | None = None
    native_vlan: int | None = None
    trunk_vlans: str | None = None
    attrs: dict | None = None


class NormalizedNeighbor(BaseModel):
    """Nachbar an einem Port aus CDP bzw. LLDP (0.62.0, ADR 0025) - Grundlage der Verkabelung."""

    device: str
    port: str
    protocol: str = "lldp"  # cdp | lldp
    remote_name: str
    remote_port: str | None = None
    remote_ip: str | None = None
    remote_platform: str | None = None
    remote_caps: list[str] = Field(default_factory=list)
    remote_description: str | None = None


class NormalizedMac(BaseModel):
    """Eintrag der MAC-Adress-Tabelle eines Switches (0.63.0): wo die MAC gelernt ist."""

    device: str
    mac: str  # aa:bb:cc:dd:ee:ff
    port: str  # Kurzform (Gi1/0/1, Eth1/3, Po10)
    vlan: int | None = None
    kind: str | None = None  # dynamic | static


class NormalizedArp(BaseModel):
    """ARP-Eintrag eines Switches mit L3-Interface (0.63.0): IP -> MAC; seit 0.94.0 auch
    IPv6-Nachbarn (NDP) mit IPv6-Adresse ohne Zone (Link-local bleibt, `cisco_switch/nd.py`)."""

    device: str
    ip: str
    mac: str
    interface: str | None = None  # Vl10
    vrf: str | None = None


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
    # ACI-Route-Leaking zwischen VRFs (0.44.0): {vrf, prefix, to: [Tenant:VRF], external}
    vrf_leaks: list[dict] = Field(default_factory=list)
    neighbors: list[NormalizedNeighbor] = Field(default_factory=list)  # CDP/LLDP (0.62.0)
    macs: list[NormalizedMac] = Field(default_factory=list)  # MAC-Tabelle (0.63.0)
    arps: list[NormalizedArp] = Field(default_factory=list)  # ARP (0.63.0)


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
    message: str | None = None  # deutsch - der Orchestrator speichert sie (ADR 0028)
    # Meldung fuer die Konsole des Agenten in `AUTOMATO_LANG` (0.90.0): die Uebersetzung aus
    # der Quittung des Orchestrators; geht nicht mit dem Ergebnis an den Orchestrator
    shown: str | None = Field(default=None, exclude=True)
    # Switches (0.62.0): beim ersten Kontakt uebernommene SSH-Host-Schluessel je Adresse
    # (`<typ> <base64>`), der Orchestrator haelt sie fest
    host_keys: dict[str, str] = Field(default_factory=dict)


class Target(BaseModel):
    """Zu lesendes Geraet inkl. Zugangsdaten vom Orchestrator (ADR 0009).

    Der zugewiesene Agent bekommt es beim Abholen eines Laufs. Zugangsdaten je Plattform:
    FortiGate `token`, Cisco ACI `username`/`password`; sie fehlen, wenn am Geraet keine
    hinterlegt sind (dann gilt die lokale Konfiguration). Switches & Router mit Zugang SNMPv3
    (`access` = `snmp`, ADR 0033): `username` = SNMPv3-Benutzer, `auth_protocol`/`auth_password`,
    `priv_protocol`/`priv_password` (nur authPriv), optional `context`.
    """

    source_system_id: int | None = None
    name: str = ""
    platform: str = "fortigate"
    base_url: str = ""
    verify_tls: bool = True
    read_scope: str = ""  # ACI: zu lesende Tenants (kommagetrennt, leer = alle); Switches: Adressen
    known_hosts: dict[str, str] = Field(default_factory=dict)  # Switches: gespeicherte Schluessel
    token: str | None = None
    username: str | None = None
    password: str | None = None
    # Switches & Router: Zugang ssh (Standard) | snmp (ADR 0033) mit SNMPv3 authPriv
    access: str | None = None
    auth_protocol: str | None = None  # sha | sha224 | sha256 | sha384 | sha512
    auth_password: str | None = Field(default=None, repr=False)
    priv_protocol: str | None = None  # aes128 | aes192 | aes256
    priv_password: str | None = Field(default=None, repr=False)
    context: str | None = None
