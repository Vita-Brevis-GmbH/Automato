"""FortiGate-REST-Client (FortiOS cmdb-API), rein lesend (UC1).

Liest Policies, Adress-/Adressgruppen-, Service-Objekte und Zonen. Auth über
Bearer-Token eines read-only API-Users (siehe LAB.md). Testbar über einen
injizierten httpx.Client (MockTransport) - kein Lab nötig.
"""

from typing import Any

import httpx


class FortiGateClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        verify_tls: bool = True,
        client: httpx.Client | None = None,
    ) -> None:
        self._base = f"{base_url.rstrip('/')}/api/v2/cmdb"
        self._headers = {"Authorization": f"Bearer {token}"}
        self._client = client or httpx.Client(verify=verify_tls, timeout=20.0)

    def _get(self, path: str) -> list[dict[str, Any]]:
        response = self._client.get(f"{self._base}/{path}", headers=self._headers)
        response.raise_for_status()
        return response.json().get("results", [])

    def policies(self) -> list[dict[str, Any]]:
        return self._get("firewall/policy")

    def addresses(self) -> list[dict[str, Any]]:
        return self._get("firewall/address")

    def address_groups(self) -> list[dict[str, Any]]:
        return self._get("firewall/addrgrp")

    def services(self) -> list[dict[str, Any]]:
        return self._get("firewall/service/custom")

    def gather(self) -> tuple[list, list, list, list]:
        """Alles einlesen, was die Normalisierung braucht."""
        return self.policies(), self.addresses(), self.address_groups(), self.services()
