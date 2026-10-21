"""NetBox-Schreiber fuer den NetBox-Sync - der einzige Schreibweg der Plattform (ADR 0010).

Wird nur vom Orchestrator benutzt (nie vom Agenten) und nur mit dem eigenen Schreib-Token des
NetBox-Quellsystems. Geschrieben werden ausschliesslich die Objekttypen in `WRITABLE`, und nur
unterhalb der NetBox-API dieses Quellsystems: jeder andere Pfad oder Host bricht **vor** dem
Senden mit `WriteNotAllowed` ab. Welche Objekte geaendert werden duerfen (nur Tag `automato`),
entscheidet der Plan im Orchestrator; der Schreiber sorgt nur fuer den engen Rahmen.

Lesen (GET) geht ueberall unterhalb der API - der Plan liest damit den Ist-Stand.
"""

from collections.abc import Iterator
from typing import Any

import httpx

from sot_gateway.adapters.netbox import auth_header

PAGE_SIZE = 1000
CHUNK = 200  # Objekte je Bulk-Request

# Objekttypen, die der Sync anlegen/aendern/loeschen darf (API-Pfad unter /api/)
WRITABLE = frozenset(
    {
        "extras/tags",
        "tenancy/tenants",
        "dcim/site-groups",
        "dcim/sites",
        "dcim/manufacturers",
        "dcim/device-roles",
        "dcim/device-types",
        "dcim/devices",
        "dcim/interfaces",
        "ipam/vrfs",
        "ipam/vlan-groups",
        "ipam/vlans",
        "ipam/prefixes",
        "ipam/ip-ranges",
        "ipam/ip-addresses",
        "virtualization/cluster-types",
        "virtualization/cluster-groups",
        "virtualization/clusters",
        "virtualization/virtual-machines",
        "virtualization/interfaces",
    }
)


class WriteNotAllowed(PermissionError):
    """Schreibzugriff ausserhalb des erlaubten Rahmens (Host/Pfad/Objekttyp) - blockiert."""


class NetBoxWriteError(RuntimeError):
    """NetBox hat einen Schreib-Request abgelehnt (Meldung von NetBox, ohne Token)."""

    def __init__(self, method: str, endpoint: str, status: int, detail: str) -> None:
        super().__init__(f"{method} {endpoint}: HTTP {status} {detail}".strip())
        self.status = status


def restrict_writes(client: httpx.Client, api_base: str) -> httpx.Client:
    """Request-Hook: Schreib-Methoden nur auf `WRITABLE`-Endpunkte unter `api_base`."""
    base = httpx.URL(api_base.rstrip("/") + "/")

    def guard(request: httpx.Request) -> None:
        url = request.url
        if (url.scheme, url.host, url.port) != (base.scheme, base.host, base.port) or not (
            url.path.startswith(base.path)
        ):
            raise WriteNotAllowed(f"fremdes Ziel blockiert: {url.host}{url.path}")
        if request.method.upper() in ("GET", "HEAD", "OPTIONS"):
            return
        endpoint = url.path[len(base.path) :].strip("/")
        if endpoint not in WRITABLE:
            raise WriteNotAllowed(f"nicht erlaubt: {request.method} {endpoint}")

    hooks = client.event_hooks
    hooks["request"] = [*hooks.get("request", []), guard]
    client.event_hooks = hooks
    return client


class NetBoxWriter:
    """Bulk-Lesen/-Schreiben gegen NetBox fuer den Sync (nur Orchestrator)."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        verify_tls: bool = True,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api = f"{self.base_url}/api"
        self._client = restrict_writes(
            client
            or httpx.Client(
                headers={
                    **auth_header(token),
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                verify=verify_tls,
                timeout=60.0,
            ),
            self._api,
        )

    def close(self) -> None:
        self._client.close()

    # -- Lesen ------------------------------------------------------------------

    def version(self) -> str:
        response = self._client.get(f"{self._api}/status/")
        self._check(response, "GET", "status")
        return str(response.json().get("netbox-version", "?"))

    def read(self, endpoint: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return list(self._paged(endpoint, params or {}))

    def _paged(self, endpoint: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        offset = 0
        while True:
            response = self._client.get(
                f"{self._api}/{endpoint}/", params={**params, "limit": PAGE_SIZE, "offset": offset}
            )
            self._check(response, "GET", endpoint)
            data = response.json()
            results = data.get("results", [])
            yield from results
            offset += len(results)
            if not results or offset >= int(data.get("count", 0)):
                return

    # -- Schreiben (Bulk) ---------------------------------------------------------

    def create(self, endpoint: str, objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Legt Objekte an; Rueckgabe in derselben Reihenfolge (mit `id`)."""
        out: list[dict[str, Any]] = []
        for chunk in _chunks(objects):
            response = self._client.post(f"{self._api}/{endpoint}/", json=chunk)
            self._check(response, "POST", endpoint)
            out += response.json()
        return out

    def update(self, endpoint: str, objects: list[dict[str, Any]]) -> None:
        """Aendert Objekte (je Objekt `id` + geaenderte Felder)."""
        for chunk in _chunks(objects):
            response = self._client.patch(f"{self._api}/{endpoint}/", json=chunk)
            self._check(response, "PATCH", endpoint)

    def delete(self, endpoint: str, ids: list[int]) -> None:
        for chunk in _chunks([{"id": i} for i in ids]):
            response = self._client.request("DELETE", f"{self._api}/{endpoint}/", json=chunk)
            self._check(response, "DELETE", endpoint)

    @staticmethod
    def _check(response: httpx.Response, method: str, endpoint: str) -> None:
        if response.is_success:
            return
        try:
            body = response.json()
            detail = body.get("detail") if isinstance(body, dict) and "detail" in body else body
        except ValueError:
            detail = response.text[:300]
        raise NetBoxWriteError(method, endpoint, response.status_code, str(detail)[:500])


def _chunks(items: list, size: int = CHUNK) -> Iterator[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
