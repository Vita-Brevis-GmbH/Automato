"""FortiGate-REST-Client (FortiOS cmdb-API), rein lesend (UC1).

Liest Policies, Adress-/Adressgruppen-, Service-Objekte und Zonen. Auth über
Bearer-Token eines read-only API-Users (siehe LAB.md). Testbar über einen
injizierten httpx.Client (MockTransport) - kein Lab nötig.
"""

from typing import Any

import httpx
from sot_gateway.readonly import enforce_read_only


class NotJson(ValueError):
    """FortiGate antwortet mit HTTP 2xx, aber ohne JSON (leer, HTML) - mit Pfad statt des
    nackten "Expecting value: line 1 column 1 (char 0)" (Dev-Befund 0.11.1)."""

    def __init__(self, path: str, response: httpx.Response) -> None:
        kind = response.headers.get("content-type", "").split(";")[0].strip() or "ohne Typ"
        size = len(response.content)
        body = "leer" if not size else f"{kind}, {size} Bytes"
        super().__init__(
            f"FortiGate antwortet auf {path} nicht mit JSON (HTTP {response.status_code}, {body})"
        )
        self.path = path


def _json(path: str, response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise NotJson(path, response) from exc


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
        # Nur lesend (ADR 0010): jede schreibende Methode bricht vor dem Senden ab
        self._client = enforce_read_only(client or httpx.Client(verify=verify_tls, timeout=20.0))

    def _get(self, path: str) -> list[dict[str, Any]]:
        response = self._client.get(f"{self._base}/{path}", headers=self._headers)
        response.raise_for_status()
        body = _json(path, response)
        return body.get("results", []) if isinstance(body, dict) else []

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
            body = _json("monitor/system/status", response)
            results = body.get("results") or {}
            info.update(
                model=results.get("model_name") or results.get("model"),
                serial=body.get("serial"),
                version=body.get("version"),
            )
        except (httpx.HTTPError, NotJson, AttributeError):
            pass  # Monitor-API im Profil nicht freigegeben/anders: ohne Modell/Seriennummer
        return info

    def policies(self) -> list[dict[str, Any]]:
        return self._get("firewall/policy")

    def policies6(self) -> list[dict[str, Any]]:
        """IPv6-Policies in eigener Tabelle (FortiOS 6.x). Ab FortiOS 7.0 stehen sie in
        firewall/policy (srcaddr6/dstaddr6) - die Tabelle fehlt dann (404/405/400)."""
        return self._optional("firewall/policy6")

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

    def vips6(self) -> list[dict[str, Any]]:
        """IPv6-VIPs inkl. Virtual Server (server-load-balance)."""
        return self._optional("firewall/vip6")

    def ldb_monitors(self) -> list[dict[str, Any]]:
        """Health-Checks der Virtual Server (Typ, Port, Intervall) - ohne Lese-Recht leer."""
        return self._optional("firewall/ldb-monitor")

    def _optional(self, path: str) -> list[dict[str, Any]]:
        """Tabelle, die je nach FortiOS-Version, Modell bzw. Profil fehlen darf (400/403/404/405
        oder eine Antwort ohne JSON)."""
        try:
            return self._get(path)
        except NotJson:
            return []
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (400, 403, 404, 405):
                return []
            raise

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
            "policies6": self.policies6(),
            "addresses": self.addresses(),
            "addresses6": self.addresses6(),
            "groups": self.address_groups(),
            "groups6": self.address_groups6(),
            "services": self.services(),
            "service_groups": self.service_groups(),
            "vips": self.vips(),
            "vips6": self.vips6(),
            "ldb_monitors": self.ldb_monitors(),
            "ippools": self.ippools(),
            "interfaces": self.interfaces(),
            "zones": self.zones(),
            "system": self.system(),
        }
