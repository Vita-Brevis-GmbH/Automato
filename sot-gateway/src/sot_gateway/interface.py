"""Adapter-Interface des SoT Gateway (ADR 0002).

Jedes IPAM-/SoT-Backend implementiert dieses Protokoll. Fuer UC1 wird nur
lesend zugegriffen; die Schreiboperation (create_address_object) folgt mit
dem ersten schreibenden Use Case.
"""

from typing import Protocol, runtime_checkable

from sot_gateway.models import NetworkObject


@runtime_checkable
class SoTAdapter(Protocol):
    """Einheitliche, tool-neutrale Sicht auf ein IPAM/SoT-Backend."""

    name: str

    def get_by_name(self, name: str) -> NetworkObject | None:
        """Adressobjekt anhand seines Namens holen."""
        ...

    def search_by_ip(self, ip: str) -> list[NetworkObject]:
        """Alle Objekte finden, die eine IP abdecken (exakt und longest-prefix)."""
        ...
