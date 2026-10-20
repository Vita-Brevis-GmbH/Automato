"""Normalisiertes Regel-DTO - Vertrag mit dem Orchestrator.

Muss zu orchestrator/app/schemas/collector.py passen (dort getestet).
VORLAEUFIG bis M5; danach ggf. gemeinsames contracts-Package.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


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
    raw: dict[str, Any] | None = None


class NormalizedEpgEndpoint(BaseModel):
    ip: str
    mac: str | None = None


class NormalizedEpg(BaseModel):
    """ACI-EPG (Model 1): Server-Gruppierung mit AP-Klammer (Projekt) + Subnetzen."""

    name: str
    ap: str | None = None
    bd: str | None = None
    subnets: list[str] = Field(default_factory=list)
    endpoints: list[NormalizedEpgEndpoint] = Field(default_factory=list)  # fvCEp/fvIp


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


class CollectorResult(BaseModel):
    status: Literal["completed", "failed"]
    rules: list[NormalizedRule] = Field(default_factory=list)
    nat_rules: list[NormalizedNat] = Field(default_factory=list)
    objects: list[NormalizedObject] = Field(default_factory=list)
    epgs: list[NormalizedEpg] = Field(default_factory=list)
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
