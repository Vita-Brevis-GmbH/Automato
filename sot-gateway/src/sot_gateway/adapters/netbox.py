"""NetBox-Adapter - bewusst leerer Stub.

Belegt die Austauschbarkeit aus ADR 0002: ein Kunde mit NetBox statt Infoblox
bekommt denselben SoTAdapter-Vertrag, der Rest der Plattform bleibt unveraendert.
Implementierung folgt, sobald ein Projekt NetBox als IPAM einsetzt.
"""

from sot_gateway.models import NetworkObject


class NetBoxAdapter:
    name = "netbox"

    def get_by_name(self, name: str) -> NetworkObject | None:
        raise NotImplementedError("NetBox-Adapter folgt mit dem ersten NetBox-Projekt")

    def search_by_ip(self, ip: str) -> list[NetworkObject]:
        raise NotImplementedError("NetBox-Adapter folgt mit dem ersten NetBox-Projekt")
