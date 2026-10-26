"""SoT-Adapter-Fabrik: baut pro Mandant den richtigen Adapter (ADR 0004).

Die SoT-Bindung haengt am Mandanten (tenant.ipam_type); die Zugangsdaten kommen
aus der Konfiguration. So laufen Model 1 (Infoblox) und Model 2 (NetBox) in einer
Instanz. Rein lesend fuer UC1.
"""

from sot_gateway.adapters import InfobloxAdapter, NetBoxAdapter

from app.config import Settings
from app.models import IpamType, Tenant


def build_adapter(tenant: Tenant, settings: Settings):
    """Gibt den zum Mandanten passenden, konfigurierten SoT-Adapter zurueck."""
    if tenant.ipam_type is IpamType.NETBOX:
        return NetBoxAdapter(
            settings.netbox_url,
            settings.netbox_token,
            verify_tls=settings.netbox_verify_tls,
        )
    if tenant.ipam_type is IpamType.INFOBLOX:
        return InfobloxAdapter(
            settings.infoblox_url,
            settings.infoblox_username,
            settings.infoblox_password,
            verify_tls=settings.infoblox_verify_tls,
        )
    raise ValueError(f"kein SoT-Adapter fuer ipam_type={tenant.ipam_type}")
