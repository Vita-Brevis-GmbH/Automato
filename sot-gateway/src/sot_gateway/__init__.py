"""SoT Gateway: einheitlicher Zugriff auf das jeweils aktive IPAM/SoT-Tool (ADR 0002)."""

from importlib.metadata import PackageNotFoundError, version

from sot_gateway.interface import SoTAdapter
from sot_gateway.models import NetworkObject, NetworkObjectType

try:
    __version__ = version("sot-gateway")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0+unbekannt"

__all__ = ["NetworkObject", "NetworkObjectType", "SoTAdapter", "__version__"]
