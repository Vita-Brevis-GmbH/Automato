from pydantic import BaseModel


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
    sources: list[str] = []  # Quell-Objektnamen (mehrere moeglich)
    destinations: list[str] = []  # Ziel-Objektnamen (mehrere moeglich)


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


class RuleDetailOut(RuleOut):
    external_id: str
    sequence: int | None = None
    endpoints: list[EndpointOut] = []
    services: list[str] = []  # kanonische Service-Namen (tcp/443, ...)
