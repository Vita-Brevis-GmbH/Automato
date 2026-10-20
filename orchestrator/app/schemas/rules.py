from pydantic import BaseModel


class AciContractOut(BaseModel):
    """ACI-Contract-Subject (domain=aci_contract): Quelle = Consumer-, Ziel = Provider-EPGs."""

    tenant: str | None = None  # ACI-Tenant aus dem Contract-DN (uni/tn-<TE>/brc-<CT>)
    contract: str | None = None
    subject: str | None = None
    scope: str | None = None  # application-profile | context | tenant | global
    dn: str | None = None


class RuleSummaryOut(BaseModel):
    """Kennzahlen fuer die Uebersicht (alle Regeln des Mandanten, nicht nur eine Seite)."""

    total: int
    by_domain: dict[str, int]  # firewall | aci_contract
    source_system_ids: list[int]
    endpoints: int
    unmatched: int
    by_dst_zone: list[tuple[str, int]]


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


class EpgOut(BaseModel):
    """Lage eines Contract-Consumers/-Providers: wo liegt die EPG?"""

    kind: str  # epg | external (L3Out) | vzany (alle EPGs der VRF)
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
    epg: EpgOut | None = None  # ACI: Lage der EPG (Tenant, AP, BD, VRF, Subnetze, Endpunkte)


class RefValueOut(BaseModel):
    """Aufgeloester Wert eines Firewall-Objekts mit Abgleich gegen das IPAM."""

    value: str
    match_status: str | None = None
    zone: str | None = None
    source_of_record: str | None = None  # netbox | infoblox
    reference: dict | None = None  # Referenzdaten (DNS-Name, Prefix, VLAN, Site, Link)
    enrichment: dict | None = None  # Anreicherung (z. B. NetBox neben der DDI)


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
    category: str | None = None  # None = nicht als Objekt gelesen (Built-in, "all")
    obj_type: str | None = None
    comment: str | None = None
    color: int | None = None
    values: list[RefValueOut] = []
    members: list[RefMemberOut] = []
    match_status: str | None = None  # zusammengefasst ueber alle Werte


class RuleDetailOut(RuleOut):
    external_id: str
    sequence: int | None = None
    refs: list[RefOut] = []
    endpoints: list[EndpointOut] = []
    services: list[str] = []  # kanonische Service-Namen (tcp/443, ...)
    protections: list[str] = []  # UTM-Profile ("Schutzmechanismen")
