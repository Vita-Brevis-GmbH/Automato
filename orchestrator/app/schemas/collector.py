"""Vertrag zwischen Collectors und Orchestrator.

VORLAEUFIG: Dieses normalisierte Regel-DTO ist der Startpunkt fuer das
Datenmodell und wird in Meilenstein M5 finalisiert (Adressobjekt-Referenzen,
kanonische Services, Applikations-Zuordnung). Collectors liefern Regeln in
genau dieser Form an POST /internal/collector-runs/{run_id}/result.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


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
    raw: dict[str, Any] | None = None  # Original-Payload fuer Nachvollziehbarkeit


class NormalizedEpgEndpoint(BaseModel):
    ip: str
    mac: str | None = None


class NormalizedEpg(BaseModel):
    """ACI-EPG (Model 1): Server-Gruppierung mit AP-Klammer (Projekt) + Subnetzen."""

    name: str
    ap: str | None = None  # AP_### (Projekt)
    bd: str | None = None  # BD_...
    subnets: list[str] = Field(default_factory=list)  # fvSubnet (dual-stack)
    endpoints: list[NormalizedEpgEndpoint] = Field(default_factory=list)  # fvCEp/fvIp (gelernt)


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


class CollectorResult(BaseModel):
    status: Literal["completed", "failed"]
    rules: list[NormalizedRule] = Field(default_factory=list)
    nat_rules: list[NormalizedNat] = Field(default_factory=list)
    objects: list[NormalizedObject] = Field(default_factory=list)
    epgs: list[NormalizedEpg] = Field(default_factory=list)
    message: str | None = None
