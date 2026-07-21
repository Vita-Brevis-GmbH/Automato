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
