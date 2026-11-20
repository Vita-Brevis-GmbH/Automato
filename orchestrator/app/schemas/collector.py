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
    origin: str | None = None  # static (fvStCEp) | vmm | learned (fvCEp, 0.46.0)


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
    # Scope je Subnetz: externe EPG `import-security` = Zuordnung (fehlt = APIC-Standard); EPG
    # `fvSubnet.scope` mit `shared` = zwischen VRFs geteilt (Shared Services, 0.44.0)
    subnet_scopes: dict[str, str] = Field(default_factory=dict)
    bd_subnet_scopes: dict[str, str] = Field(default_factory=dict)  # Scope der BD-Subnetze
    vrf_tenant: str | None = None  # ACI-Tenant der VRF (aufgeloest: eigener bzw. common)
    endpoints: list[NormalizedEpgEndpoint] = Field(default_factory=list)  # fvCEp/fvIp (gelernt)
    selectors: list[str] = Field(default_factory=list)  # ESG: Selektoren (EPG, Tag, IP)
    pref_group: bool = False  # Mitglied der Preferred Group seiner VRF
    # ADR 0071: Intra-EPG-Isolation (`pcEnfPref=enforced` an der EPG) und VRF ohne Contracts
    # (`pcEnfPref=unenforced` an der VRF); None = nicht gelesen (aeltere Collectors)
    isolated: bool | None = None
    vrf_unenforced: bool | None = None


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
    vendor: str | None = None  # Hersteller (1.8.0, z. B. Hardware eines ESXi-Hosts)


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
    # LLDP: Chassis-ID der Gegenstelle (1.23.0) - MAC bzw. Text wie gemeldet; ordnet sie auch
    # ohne passenden Systemnamen zu (z. B. FortiGate: eine MAC fuer alle Ports)
    remote_chassis: str | None = None


class NormalizedTransceiver(BaseModel):
    """Transceiver (SFP, SFP+, QSFP …) an einem Port eines Switches (1.19.0, nur lesend): Typ,
    Hersteller und Teilenummer, soweit das Geraet sie nennt, und die Seriennummer."""

    device: str
    port: str  # Kurzform wie die Ports (Gi1/0/49, Eth1/1, xe-0/0/0, 1/1/49)
    type: str | None = None  # z. B. SFP-10GBase-SR, 10Gbase-SR, SFP+SR
    vendor: str | None = None
    part: str | None = None  # Teilenummer (Cisco-PID bzw. Part Number)
    serial: str | None = None


class NormalizedTransceiverRead(BaseModel):
    """Lesestatus der Transceiver je Switch (1.19.0): gelesen, nicht lesbar (Befehl gescheitert)
    bzw. nicht unterstuetzt (Plattform bzw. Zugang SNMPv3) - gezaehlt statt still weggelassen."""

    device: str
    status: str = "read"  # read | unreadable | unsupported
    count: int = 0
    note: str | None = None


class NormalizedMac(BaseModel):
    """Eintrag der MAC-Adress-Tabelle eines Switches (0.63.0): wo die MAC gelernt ist."""

    device: str = Field(max_length=200)
    mac: str = Field(max_length=32)
    port: str = Field(max_length=200)
    vlan: int | None = None
    kind: str | None = Field(default=None, max_length=16)


class NormalizedArp(BaseModel):
    """ARP-Eintrag eines Switches mit L3-Interface (0.63.0): IP -> MAC; seit 0.94.0 auch
    IPv6-Nachbarn (NDP) mit IPv6-Adresse (Link-local erkennt der Orchestrator an der Adresse)."""

    device: str = Field(max_length=200)
    ip: str = Field(max_length=64)
    mac: str = Field(max_length=32)
    interface: str | None = Field(default=None, max_length=200)
    vrf: str | None = Field(default=None, max_length=100)


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


class NormalizedStpPort(BaseModel):
    """Port einer Spanning-Tree-Instanz (1.19.0): Rolle, Zustand, Kosten, Typ wie gemeldet."""

    port: str  # Kurzform (Gi1/0/1, Eth1/3, Po10)
    role: str | None = None  # root | designated | alternate | backup | master | disabled
    state: str | None = None  # forwarding | blocking | learning | listening | disabled | broken
    cost: int | None = None
    priority: str | None = None  # Prio.Nbr, z. B. 128.48
    type: str | None = None  # P2p, Shr, Edge, Bound(PVST), Peer(STP), (vPC peer-link) …
    inconsistent: str | None = None  # ROOT_Inc, LOOP_Inc, BA_Inc, PVST_Inc, TYPE_Inc …


class NormalizedStp(BaseModel):
    """Spanning Tree eines Switches je VLAN bzw. MST-Instanz (1.19.0, nur lesend)."""

    device: str
    instance: str  # VLAN0010 | MST1
    vlan: int | None = None  # PVST/Rapid-PVST
    mst: int | None = None  # MST-Instanz
    vlans: str | None = None  # MST: VLANs der Instanz als Bereiche
    protocol: str | None = None  # ieee | rstp | mstp (wie gemeldet)
    root_priority: int | None = None
    root_mac: str | None = None
    root_cost: int | None = None
    root_port: str | None = None
    is_root: bool = False
    bridge_priority: int | None = None
    bridge_mac: str | None = None
    tc_count: int | None = None  # Topologieaenderungen seit dem Start der Instanz
    tc_ago: int | None = None  # Sekunden seit der letzten Aenderung (beim Lesen)
    tc_from: str | None = None  # Port, ueber den die letzte Aenderung kam
    ports: list[NormalizedStpPort] = Field(default_factory=list)


class NormalizedStpDevice(BaseModel):
    """Spanning Tree je Switch (1.19.0): Modus, Lesestatus und err-disabled Ports."""

    device: str
    status: str = "read"  # read | off | unsupported | unreadable
    mode: str | None = None  # pvst | rapid-pvst | mst
    region: str | None = None  # MST-Region (Name)
    revision: int | None = None
    errdisabled: list[dict] = Field(default_factory=list)  # {port, reason, vlans}
    note: str | None = None


class NetworkInventory(BaseModel):
    devices: list[NormalizedDevice] = Field(default_factory=list)
    interfaces: list[NormalizedInterface] = Field(default_factory=list)
    vlans: list[NormalizedVlan] = Field(default_factory=list)
    subnets: list[NormalizedSubnet] = Field(default_factory=list)
    vrfs: list[str] = Field(default_factory=list)
    # ACI-Route-Leaking zwischen VRFs (0.44.0): {vrf, prefix, to: [Tenant:VRF], external}
    vrf_leaks: list[dict] = Field(default_factory=list)
    neighbors: list[NormalizedNeighbor] = Field(default_factory=list)  # CDP/LLDP (0.62.0)
    # Transceiver der Switches und Lesestatus je Switch (1.19.0); None = nicht geliefert (andere
    # Quellen, aeltere Agenten)
    transceivers: list[NormalizedTransceiver] | None = None
    transceiver_reads: list[NormalizedTransceiverRead] | None = None
    macs: list[NormalizedMac] = Field(default_factory=list)  # MAC-Tabelle (0.63.0)
    arps: list[NormalizedArp] = Field(default_factory=list)  # ARP (0.63.0)
    # Spanning Tree der Switches (1.19.0); None = nicht geliefert (andere Quellen, aeltere
    # Agenten) - der gespeicherte Stand bleibt (`store/stp.replace`)
    stp: list[NormalizedStp] | None = None
    stp_devices: list[NormalizedStpDevice] | None = None


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


class NormalizedHostNeighbor(BaseModel):
    """Nachbar an einer vmnic, wie der ESXi-Host ihn per CDP bzw. LLDP hoert (1.8.0)."""

    protocol: str = "cdp"  # cdp | lldp
    remote_name: str
    remote_port: str | None = None
    remote_ip: str | None = None
    remote_platform: str | None = None
    remote_caps: list[str] = Field(default_factory=list)


class NormalizedHostNic(BaseModel):
    """Physische Netzwerkkarte (vmnic) eines ESXi-Hosts (1.8.0)."""

    name: str  # vmnic0
    mac: str | None = None
    speed: str | None = None  # wie bei den Switches: Mbit/s bzw. „10G“; ohne Link leer
    duplex: str | None = None  # full | half
    status: str | None = None  # up | down
    neighbor: NormalizedHostNeighbor | None = None


class NormalizedHost(BaseModel):
    """ESXi-Host aus dem vCenter (1.8.0): Hardware, Version, Verwaltungs-Adresse und vmnics -
    landet im Netzinventar des vCenters (`inv_device` Rolle `esxi`, `inv_interface`,
    `inv_neighbor`) und damit in der Verkabelung."""

    name: str
    vendor: str | None = None
    model: str | None = None
    serial: str | None = None
    version: str | None = None
    mgmt_ip: str | None = None
    pnics: list[NormalizedHostNic] = Field(default_factory=list)


class VirtualInventory(BaseModel):
    """vCenter-Inventar eines Laufs: VMs und Portgruppen (ADR 0017), seit 1.8.0 die Hosts."""

    vms: list[NormalizedVm] = Field(default_factory=list)
    portgroups: list[NormalizedPortgroup] = Field(default_factory=list)
    # None = aelterer Collector ohne Hosts: die Hosts im Netzinventar bleiben unangetastet;
    # [] = das vCenter hat keine Hosts (mehr) - sie fallen weg
    hosts: list[NormalizedHost] | None = None


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
    # Route Control (0.113.0, ADR 0041): enforce (export/import) und die Subnetze der externen
    # EPGs mit `export-rtctrl` (angekuendigt) bzw. `import-rtctrl` (erwartet)
    route_control: dict[str, Any] = Field(default_factory=dict)


class NormalizedRoutingContext(BaseModel):
    """Gelesener Kontext; `missing` = was nicht lesbar war (Aussage dann „unbekannt“)."""

    node: str
    context: str
    kind: str = "vdom"  # vdom | vrf | l3 (Router bzw. L3-Switch je VRF, ADR 0032)
    missing: list[str] = Field(default_factory=list)
    # FortiGate (ADR 0070): Einstellungen des VDOMs, die den Weg bestimmen - `central_nat`,
    # `asymroute`, `strict_src_check`, `src_check_off`, `central_snat`; leer = nicht gelesen
    settings: dict[str, Any] = Field(default_factory=dict)


class NormalizedBgpObject(BaseModel):
    """BGP-Konfiguration eines Routing-Kontexts (0.113.0, ADR 0041), nur lesend: `bgp` = der
    BGP-Prozess (AS, Router-ID, network, aggregate, redistribute), `prefix_list`, `route_map`
    und `access_list` = die Filter mit ihren Regeln in der Reihenfolge des Geraets."""

    node: str
    context: str
    kind: Literal["bgp", "prefix_list", "route_map", "access_list"]
    name: str
    family: int | None = None  # 4 | 6 bei Prefix- und Access-Listen
    data: dict[str, Any] = Field(default_factory=dict)


class NormalizedVpnPhase2(BaseModel):
    """Phase 2 eines IPsec-Tunnels (ADR 0063): Selektoren lokal bzw. der Gegenstelle."""

    name: str
    src: list[str] = Field(default_factory=list)
    dst: list[str] = Field(default_factory=list)
    src_names: list[str] = Field(default_factory=list)
    dst_names: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    status: str | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)


class NormalizedVpnTunnel(BaseModel):
    """IPsec-Tunnel (Interface-Modus) einer FortiGate je VDOM (1.19.0, ADR 0063), nur lesend."""

    node: str
    context: str
    name: str
    kind: Literal["static", "dialup", "ddns"] = "static"
    interface: str | None = None
    remote_gw: str | None = None
    local_gw: str | None = None
    ike_version: int | None = None
    status: str | None = None  # up | partial | down | idle; None = unbekannt
    phase2: list[NormalizedVpnPhase2] = Field(default_factory=list)
    instances: list[dict[str, Any]] = Field(default_factory=list)  # Dial-up: Gegenstellen
    attrs: dict[str, Any] = Field(default_factory=dict)


class RoutingInventory(BaseModel):
    """Routing, BGP, HA und L3Out eines Laufs (ADR 0019 Stufe 2) - je Quellsystem ersetzt."""

    contexts: list[NormalizedRoutingContext] = Field(default_factory=list)
    routes: list[NormalizedRoute] = Field(default_factory=list)
    peers: list[NormalizedPeer] = Field(default_factory=list)
    ha: list[NormalizedHa] = Field(default_factory=list)
    policy_routes: list[NormalizedPolicyRoute] = Field(default_factory=list)
    l3outs: list[NormalizedL3Out] = Field(default_factory=list)
    bgp: list[NormalizedBgpObject] = Field(default_factory=list)  # BGP-Filter (0.113.0)
    vpn: list[NormalizedVpnTunnel] = Field(default_factory=list)  # IPsec (1.19.0, ADR 0063)


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
    # AD-DNS (1.23.0): Eintraege je uebertragener Zone, auch 0 (Zone ohne PTR ist gelesen)
    zones: dict[str, int] | None = None
    message: str | None = None
    # Switches (0.62.0): beim ersten Kontakt uebernommene SSH-Host-Schluessel je Adresse
    # (`<typ> <base64>`), der Orchestrator haelt sie fest
    host_keys: dict[str, str] = Field(default_factory=dict)
