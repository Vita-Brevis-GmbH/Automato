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
        self._root = f"{base_url.rstrip('/')}/api/v2"
        self._base = f"{self._root}/cmdb"
        self._headers = {"Authorization": f"Bearer {token}"}
        self._client = client or httpx.Client(verify=verify_tls, timeout=20.0)

    def _get(self, path: str) -> list[dict[str, Any]]:
        response = self._client.get(f"{self._base}/{path}", headers=self._headers)
        response.raise_for_status()
        return response.json().get("results", [])

    def interfaces(self) -> list[dict[str, Any]]:
        return self._get("system/interface")

    def zones(self) -> list[dict[str, Any]]:
        return self._get("system/zone")

    def system(self) -> dict[str, Any]:
        """Hostname (cmdb system/global) + Modell/Seriennummer/Version (monitor, optional)."""
        info: dict[str, Any] = {}
        glob = self._get("system/global")
        if isinstance(glob, dict):
            info["hostname"] = glob.get("hostname")
        try:
            response = self._client.get(
                f"{self._root}/monitor/system/status", headers=self._headers
            )
            response.raise_for_status()
            body = response.json()
            results = body.get("results") or {}
            info.update(
                model=results.get("model_name") or results.get("model"),
                serial=body.get("serial"),
                version=body.get("version"),
            )
        except httpx.HTTPError:
            pass  # Monitor-API im Profil nicht freigegeben: ohne Modell/Seriennummer
        return info

    def policies(self) -> list[dict[str, Any]]:
        return self._get("firewall/policy")

    def addresses(self) -> list[dict[str, Any]]:
        return self._get("firewall/address")

    def address_groups(self) -> list[dict[str, Any]]:
        return self._get("firewall/addrgrp")

    def services(self) -> list[dict[str, Any]]:
        # FortiOS-CMDB: Service-Objekte liegen unter dem gepunkteten Pfad
        # "firewall.service/custom" (= config firewall service custom), NICHT
        # "firewall/service/custom" (das liefert 400 Bad Request).
        return self._get("firewall.service/custom")

    def service_groups(self) -> list[dict[str, Any]]:
        return self._get("firewall.service/group")

    def addresses6(self) -> list[dict[str, Any]]:
        return self._get("firewall/address6")

    def address_groups6(self) -> list[dict[str, Any]]:
        return self._get("firewall/addrgrp6")

    def vips(self) -> list[dict[str, Any]]:
        # Virtual IPs (DNAT/Port-Forward): externe IP -> internes Ziel.
        return self._get("firewall/vip")

    def ippools(self) -> list[dict[str, Any]]:
        # IP-Pools fuer Source-NAT (IPP_).
        return self._get("firewall/ippool")

    def ping(self) -> None:
        """Leichter Lese-Zugriff zur Erreichbarkeits-/Auth-Pruefung (kein Ingest)."""
        self._get("system/global")

    def gather(self) -> dict[str, list]:
        """Alles einlesen, was die Normalisierung braucht (IPv4+IPv6, NAT)."""
        return {
            "policies": self.policies(),
            "addresses": self.addresses(),
            "addresses6": self.addresses6(),
            "groups": self.address_groups(),
            "groups6": self.address_groups6(),
            "services": self.services(),
            "service_groups": self.service_groups(),
            "vips": self.vips(),
            "ippools": self.ippools(),
            "interfaces": self.interfaces(),
            "zones": self.zones(),
            "system": self.system(),
        }
