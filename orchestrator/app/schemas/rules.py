from datetime import datetime

from pydantic import BaseModel


class AciContractOut(BaseModel):
    """ACI-Contract-Subject (domain=aci_contract): Quelle = Consumer-, Ziel = Provider-EPGs."""

    tenant: str | None = None  # ACI-Tenant aus dem Contract-DN (uni/tn-<TE>/brc-<CT>)
    contract: str | None = None
    subject: str | None = None
    scope: str | None = None  # application-profile | context | tenant | global
    dn: str | None = None
    kind: str = "contract"  # contract | taboo (deny) | preferred_group (VRF, alle Ports)


class RuleNatOut(BaseModel):
    """NAT einer Firewall-Policy: SNAT (Pool bzw. Interface-IP) und DNAT (VIPs im Ziel)."""

    snat: bool = False  # Source-NAT an der Policy aktiv
    pools: list[str] = []  # SNAT-IP-Pools; leer bei snat = NAT auf die Interface-IP
    vips: list[str] = []  # VIP-Objekte im Ziel (auch ueber Gruppen)


class RulePairOut(BaseModel):
    """IPv4/IPv6-Gegenstueck derselben Firewall - eigene Regel, nur zur Orientierung verbunden."""

    rule_id: int
    name: str
    family: str | None = None
    # same (gleiche Objektnamen, Services, Interfaces) | similar (Abweichungen) | confirmed
    # (von Hand bestaetigt, Abweichungen in diff)
    match: str
    diff: list[str] = []  # src | dst | service | name | zone


class RuleSummaryOut(BaseModel):
    """Kennzahlen fuer die Uebersicht (alle Regeln des Mandanten, nicht nur eine Seite)."""

    total: int
    by_domain: dict[str, int]  # firewall | aci_contract
    source_system_ids: list[int]
    endpoints: int
    unmatched: int
    by_dst_zone: list[tuple[str, int]]
    by_src_zone: list[tuple[str, int]] = []
    by_action: dict[str, int] = {}
    by_vdom: list[tuple[str, int]] = []  # FortiGate-VDOMs mit Anzahl Regeln (ADR 0019)


class RuleOut(BaseModel):
    id: int
    tenant_id: int
    source_system_id: int
    domain: str
    name: str  # Anzeigename (Fallback auf "#<external_id>", wenn FortiGate-Policy ohne Namen)
    action: str
    section_label: str | None = None
    src_zone: str | None = None
    dst_zone: str | None = None
    num_src: int = 0
    num_dst: int = 0
    num_services: int = 0
    num_unmatched: int = 0  # Endpunkte mit Objekt, das nicht im SoT aufgeloest wurde
    sources: list[str] = []  # Quell-Objektnamen wie auf der Firewall (sonst Werte)
    destinations: list[str] = []  # Ziel-Objektnamen wie auf der Firewall (sonst Werte)
    service_names: list[str] = []  # Service-Namen wie auf der Firewall (sonst kanonisch)
    aci: AciContractOut | None = None  # nur ACI-Contracts
    nat: RuleNatOut | None = None  # nur Firewall-Policies mit SNAT oder VIP
    missing: list[str] = []  # src | dst ohne Endpunkt (ACI: kein Consumer/Provider)
    negate: list[str] = []  # FortiGate: negierte Seiten src | dst | service
    family: str | None = None  # v4 | v6 | dual (Adressfamilie der Policy)
    vdom: str | None = None  # FortiGate-VDOM der Policy (ADR 0019)
    pair: RulePairOut | None = None  # IPv4/IPv6-Gegenstueck (nur Markierung)


class EpgSubnetOut(BaseModel):
    ip: str  # Gateway/Prefix wie im APIC (z. B. 10.1.0.1/24)
    origin: str  # epg | bd | ext
    ipam: dict | None = (
        None  # passendes Netz der fuehrenden Referenzquelle (Beschreibung, VLAN, Site)
    )


class EpgEndpointOut(BaseModel):
    ip: str
    mac: str | None = None
    dns_name: str | None = None  # aus DDI/NetBox (Referenzquelle)


class EpgPortOut(BaseModel):
    device: str  # Leaf (bzw. Leaf-Paar bei vPC je Leaf eine Zeile)
    name: str  # eth1/10, vPC-Policy-Group, fex101/eth1/1
    kind: str
    lag: str | None = None
    mode: str | None = None  # access | tagged
    neighbor: str | None = None  # LLDP/CDP
    neighbor_port: str | None = None


class EpgOut(BaseModel):
    """Lage eines Contract-Consumers/-Providers: wo liegt die EPG?"""

    kind: str  # epg | esg | external (L3Out) | vzany (alle EPGs der VRF)
    name: str
    label: str  # AP/EPG, L3Out/EPG bzw. vzAny VRF
    dn: str | None = None
    aci_tenant: str | None = None
    ap: str | None = None  # Application Profile (Projekt-/Applikationsklammer)
    bd: str | None = None
    vrf: str | None = None
    l3out: str | None = None
    subnets: list[EpgSubnetOut] = []
    endpoint_count: int = 0
    endpoints: list[EpgEndpointOut] = []  # gelernte Endpunkte (begrenzt, siehe endpoint_count)
    server: dict | None = None  # verknuepfter Server (Host-Objekt: name, ip, site)
    selectors: list[str] = []  # ESG: Selektoren (EPG, Tag, IP)
    pref_group: bool = False  # Mitglied der Preferred Group seiner VRF
    vlans: list[int] = []  # Encap-VLANs der EPG (Netzinventar des APICs)
    vms: list[dict] = []  # VMs an Portgruppen dieser EPG (vCenter, ADR 0017)
    port_count: int = 0
    ports: list[EpgPortOut] = []  # statische Pfade (begrenzt, siehe port_count)


class EndpointOut(BaseModel):
    role: str  # src | dst
    kind: str  # host | network | zone | any | ...
    name: str | None = None  # Objektname (None bei ANY)
    value: str | None = None  # aufgeloeste IP/CIDR/Range oder inline-Wert ("all")
    match_status: str | None = None  # matched | unmatched | expected_external | ...
    zone: str | None = None
    object_class: str | None = None  # aus der Namenskonvention (H_/N_/VIP_/...)
    fqdn: str | None = None
    nat_target: str | None = None  # bei VIPs: internes Ziel (mapped_ip[:port][/proto])
    vm: dict | None = None  # VM hinter der IP (vCenter, ADR 0017)
    epg: EpgOut | None = None  # ACI: Lage der EPG (Tenant, AP, BD, VRF, Subnetze, Endpunkte)


class RefValueOut(BaseModel):
    """Aufgeloester Wert eines Firewall-Objekts mit Abgleich gegen das IPAM."""

    value: str
    match_status: str | None = None
    zone: str | None = None
    source_of_record: str | None = None  # netbox | infoblox
    reference: dict | None = None  # Referenzdaten (DNS-Name, Prefix, VLAN, Site, Link)
    enrichment: dict | None = None  # Anreicherung (z. B. NetBox neben der DDI)
    vm: dict | None = None  # VM hinter der IP (vCenter): Name, Status, Portgruppe, EPG


class RefMemberOut(BaseModel):
    name: str
    category: str | None = None
    obj_type: str | None = None
    comment: str | None = None
    values: list[RefValueOut] = []


class RefOut(BaseModel):
    """Verweis der Regel auf ein Firewall-Objekt - so, wie es auf dem Geraet steht."""

    role: str  # src | dst | service
    name: str
    family: str | None = None  # v4 | v6 (FortiGate: gleichnamige Objekte je Familie)
    category: str | None = None  # None = nicht als Objekt gelesen (Built-in, "all")
    obj_type: str | None = None
    comment: str | None = None
    color: int | None = None
    values: list[RefValueOut] = []
    members: list[RefMemberOut] = []
    match_status: str | None = None  # zusammengefasst ueber alle Werte


class PairDecisionOut(BaseModel):
    """Von Hand entschiedenes IPv4/IPv6-Paar (bleibt ueber neue Laeufe erhalten)."""

    id: int
    decision: str  # confirmed | rejected
    partner_id: int | None = None  # Gegenstueck im aktuellen Stand (None = nicht mehr da)
    partner_name: str | None = None
    partner_external_id: str
    decided_by: str
    decided_at: datetime


class RuleDetailOut(RuleOut):
    external_id: str
    sequence: int | None = None
    refs: list[RefOut] = []
    endpoints: list[EndpointOut] = []
    services: list[str] = []  # kanonische Service-Namen (tcp/443, ...)
    protections: list[str] = []  # UTM-Profile ("Schutzmechanismen")
    comment: str | None = None  # Kommentar der Policy
    # VIPs (DNAT) und SNAT-Pools der Regel mit IPs, Ports und DNS-Namen (Link in die NAT-Sicht)
    nat_entries: list[dict] = []
    pair_decisions: list[PairDecisionOut] = []  # von Hand bestaetigt/abgelehnt
