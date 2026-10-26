"""Kanonisches Datenmodell des SoT Gateway (ADR 0002).

Der Rest der Plattform kennt ausschliesslich diese Typen - nie
NetBox- oder Infoblox-spezifische Formate.
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class NetworkObjectType(StrEnum):
    HOST = "host"
    NETWORK = "network"
    RANGE = "range"


class NetworkObject(BaseModel):
    """Ein Adressobjekt aus dem aktiven IPAM, tool-neutral."""

    name: str
    type: NetworkObjectType
    value: str  # z. B. "10.1.2.3", "10.1.0.0/16" oder "10.1.2.10-10.1.2.20"
    tags: list[str] = Field(default_factory=list)
    source: str = ""  # welcher Adapter das Objekt geliefert hat, z. B. "infoblox"


class ReferenceKind(StrEnum):
    """Art eines Referenz-Eintrags aus dem IPAM (komplett gelesen, ADR 0010)."""

    PREFIX = "prefix"
    RANGE = "range"
    IP_ADDRESS = "ip_address"  # NetBox IP-Adresse
    HOST = "host"  # Infoblox Host-Record (je Adresse ein Eintrag)
    A_RECORD = "a_record"  # Infoblox A/AAAA
    PTR_RECORD = "ptr_record"
    FIXED_ADDRESS = "fixed_address"  # Infoblox DHCP-Reservierung
    VLAN = "vlan"


# Eintragsarten, die eine einzelne IP beschreiben
IP_KINDS = frozenset(
    {
        ReferenceKind.IP_ADDRESS,
        ReferenceKind.HOST,
        ReferenceKind.A_RECORD,
        ReferenceKind.PTR_RECORD,
        ReferenceKind.FIXED_ADDRESS,
    }
)


class ReferenceRecord(BaseModel):
    """Ein Eintrag aus dem IPAM, tool-neutral (Infoblox DDI oder NetBox)."""

    kind: ReferenceKind
    value: str  # IP, Netz (CIDR), Range "a-b" oder VLAN-ID
    prefix_len: int | None = None  # bei IP-Adressen/Ranges: Laenge des Netzes, falls bekannt
    vrf: str | None = None  # NetBox-VRF bzw. Infoblox Network View (ausser "default")
    name: str | None = None  # Anzeigename (VLAN-Name, Range-Name)
    dns_name: str | None = None
    description: str | None = None
    vlan_vid: int | None = None
    vlan_name: str | None = None
    site: str | None = None
    tenant: str | None = None
    status: str | None = None
    tags: list[str] = Field(default_factory=list)
    attrs: dict[str, str] = Field(default_factory=dict)  # Extensible Attributes / Custom Fields
    ref_id: str | None = None  # ID im Quellsystem (NetBox-ID, Infoblox _ref)
    url: str | None = None  # Link in die Oberflaeche des Quellsystems
