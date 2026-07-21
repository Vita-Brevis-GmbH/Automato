"""SoT Gateway: einheitlicher Zugriff auf das jeweils aktive IPAM/SoT-Tool (ADR 0002)."""

from sot_gateway.interface import SoTAdapter
from sot_gateway.models import NetworkObject, NetworkObjectType

__all__ = ["NetworkObject", "NetworkObjectType", "SoTAdapter"]
