"""NetBox-Adapter, rein lesend (Model 2, UC1).

Implementiert das SoTAdapter-Protokoll gegen die NetBox-REST-API. Abgefragt werden
IP-Adressen (host) und Prefixe (network); Zonen kommen als Tags am Objekt mit
(Konvention `zone-<name>` am Subnetz, siehe ADR 0004).
"""

from typing import Any

import httpx

from sot_gateway.models import NetworkObject, NetworkObjectType


class NetBoxAdapter:
    """Lesender Zugriff auf NetBox über die REST-API."""

    name = "netbox"

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        verify_tls: bool = True,
        client: httpx.Client | None = None,
    ) -> None:
        self._api = f"{base_url.rstrip('/')}/api"
        self._client = client or httpx.Client(
            headers={"Authorization": f"Token {token}", "Accept": "application/json"},
            verify=verify_tls,
            timeout=15.0,
        )

    # -- SoTAdapter-Protokoll -------------------------------------------------

    def get_by_name(self, name: str) -> NetworkObject | None:
        # NetBox kennt keine generischen benannten Objekte -> Suche über den DNS-Namen
        records = self._get("ipam/ip-addresses/", {"dns_name": name})
        if not records:
            return None
        return self._ip_to_object(records[0])

    def search_by_ip(self, ip: str) -> list[NetworkObject]:
        """Exaktes Host-Match plus umschliessende Prefixe (longest-prefix zuerst)."""
        results: list[NetworkObject] = [
            self._ip_to_object(rec) for rec in self._get("ipam/ip-addresses/", {"address": ip})
        ]
        prefixes = self._get("ipam/prefixes/", {"contains": ip})
        prefixes.sort(key=self._prefix_length, reverse=True)
        for rec in prefixes:
            results.append(
                NetworkObject(
                    name=rec.get("description") or rec["prefix"],
                    type=NetworkObjectType.NETWORK,
                    value=rec["prefix"],
                    tags=self._tags(rec),
                    source=self.name,
                )
            )
        return results

    # -- intern ---------------------------------------------------------------

    def _get(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        response = self._client.get(f"{self._api}/{path}", params=params)
        response.raise_for_status()
        return response.json().get("results", [])

    def _ip_to_object(self, record: dict[str, Any]) -> NetworkObject:
        address = record["address"].split("/")[0]
        return NetworkObject(
            name=record.get("dns_name") or address,
            type=NetworkObjectType.HOST,
            value=address,
            tags=self._tags(record),
            source=self.name,
        )

    @staticmethod
    def _tags(record: dict[str, Any]) -> list[str]:
        return sorted(t["slug"] for t in record.get("tags", []) if "slug" in t)

    @staticmethod
    def _prefix_length(record: dict[str, Any]) -> int:
        try:
            return int(record["prefix"].split("/")[1])
        except (IndexError, ValueError):
            return 0
