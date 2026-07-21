"""Infoblox-DDI-Adapter (WAPI), rein lesend (UC1).

Implementiert das SoTAdapter-Protokoll gegen die Infoblox WAPI.
Abgefragt werden host records, networks und ranges; die Antworten werden auf
das kanonische NetworkObject-Modell abgebildet.
"""

from typing import Any

import httpx

from sot_gateway.models import NetworkObject, NetworkObjectType

_DEFAULT_WAPI_VERSION = "v2.12"


class InfobloxAdapter:
    """Lesender Zugriff auf Infoblox DDI ueber die WAPI."""

    name = "infoblox"

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        wapi_version: str = _DEFAULT_WAPI_VERSION,
        verify_tls: bool = True,
        client: httpx.Client | None = None,
    ) -> None:
        self._wapi_url = f"{base_url.rstrip('/')}/wapi/{wapi_version}"
        self._client = client or httpx.Client(
            auth=(username, password),
            verify=verify_tls,
            timeout=15.0,
        )

    # -- SoTAdapter-Protokoll -------------------------------------------------

    def get_by_name(self, name: str) -> NetworkObject | None:
        records = self._get("record:host", params={"name": name})
        if not records:
            return None
        return self._host_to_object(records[0])

    def search_by_ip(self, ip: str) -> list[NetworkObject]:
        """Exaktes Host-Match plus umschliessende Netze (longest-prefix zuerst)."""
        results: list[NetworkObject] = []

        for record in self._get("record:host", params={"ipv4addr": ip}):
            results.append(self._host_to_object(record))

        networks = self._get("network", params={"contains_address": ip})
        networks.sort(key=self._prefix_length, reverse=True)  # longest-prefix zuerst
        for net in networks:
            results.append(
                NetworkObject(
                    name=net.get("comment") or net["network"],
                    type=NetworkObjectType.NETWORK,
                    value=net["network"],
                    tags=self._extattrs_to_tags(net),
                    source=self.name,
                )
            )
        return results

    # -- intern ---------------------------------------------------------------

    def _get(self, obj_type: str, params: dict[str, str]) -> list[dict[str, Any]]:
        response = self._client.get(
            f"{self._wapi_url}/{obj_type}",
            params={**params, "_return_fields+": "extattrs,comment"},
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []

    def _host_to_object(self, record: dict[str, Any]) -> NetworkObject:
        addrs = record.get("ipv4addrs") or []
        value = addrs[0]["ipv4addr"] if addrs else ""
        return NetworkObject(
            name=record["name"],
            type=NetworkObjectType.HOST,
            value=value,
            tags=self._extattrs_to_tags(record),
            source=self.name,
        )

    @staticmethod
    def _extattrs_to_tags(record: dict[str, Any]) -> list[str]:
        """Infoblox Extensible Attributes -> flache "key=value"-Tags."""
        extattrs = record.get("extattrs") or {}
        return sorted(f"{key}={attr.get('value')}" for key, attr in extattrs.items())

    @staticmethod
    def _prefix_length(net: dict[str, Any]) -> int:
        network = net.get("network", "")
        try:
            return int(network.split("/")[1])
        except (IndexError, ValueError):
            return 0
