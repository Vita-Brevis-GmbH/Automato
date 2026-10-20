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


class NormalizedObject(BaseModel):
    """Adressobjekt-Metadaten (Katalog): Typ + Farbcode als Signal (ADR 0006)."""

    name: str
    obj_type: str | None = None  # ipmask | interface-subnet | iprange | fqdn | geography | ...
    color: int | None = None  # FortiGate-Farbcode (optional)
    value: str | None = None  # aufgeloeste IP/CIDR, falls vorhanden
    fqdn: str | None = None


class CollectorResult(BaseModel):
    status: Literal["completed", "failed"]
    rules: list[NormalizedRule] = Field(default_factory=list)
    nat_rules: list[NormalizedNat] = Field(default_factory=list)
    objects: list[NormalizedObject] = Field(default_factory=list)
    epgs: list[NormalizedEpg] = Field(default_factory=list)
    message: str | None = None


class Target(BaseModel):
    """Zu lesendes Geraet inkl. Zugangsdaten vom Orchestrator (ADR 0009).

    Der zugewiesene Agent bekommt es beim Abholen eines Laufs; `token` fehlt, wenn am
    Geraet keine Zugangsdaten hinterlegt sind (dann gilt die lokale Konfiguration).
    """

    source_system_id: int | None = None
    name: str = ""
    platform: str = "fortigate"
    base_url: str = ""
    verify_tls: bool = True
    token: str | None = None
