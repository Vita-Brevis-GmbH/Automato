"""NetBox-Adapter, rein lesend (Model 2, UC1).

Implementiert das SoTAdapter-Protokoll gegen die NetBox-REST-API. Abgefragt werden
IP-Adressen (host) und Prefixe (network); Zonen kommen als Tags am Objekt mit
(Konvention `zone-<name>` am Subnetz, siehe ADR 0004).

`export()` liest Prefixe, IP-Adressen, IP-Ranges und VLANs komplett (seitenweise) als
neutrale `ReferenceRecord`s (Referenzquelle, ADR 0010). Der Client ist per
`enforce_read_only` auf GET beschraenkt - geschrieben wird nur ueber `netbox_writer`.
"""

from collections.abc import Iterator
from typing import Any

import httpx

from sot_gateway.models import NetworkObject, NetworkObjectType, ReferenceKind, ReferenceRecord
from sot_gateway.readonly import enforce_read_only
from sot_gateway.readonly import get as read_get

PAGE_SIZE = 1000


def auth_header(token: str) -> dict[str, str]:
    """NetBox-Token-Header: v2-Tokens (`nbt_…`, ab NetBox 4.6) als Bearer, sonst klassisch."""
    token = token.strip()
    if token.startswith("nbt_"):
        return {"Authorization": f"Bearer {token}"}
    return {"Authorization": f"Token {token}"}


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
        self._client = enforce_read_only(
            client
            or httpx.Client(
                headers={**auth_header(token), "Accept": "application/json"},
                verify=verify_tls,
                # Verbindungsaufbau kurz: ein nicht erreichbares Ziel meldet sich nach
                # 4 x 10 s statt 4 x 30 s (Wiederholungen in readonly.get)
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
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
        # IP-Ranges, die die IP enthalten (z. B. FortiGate-iprange-Objekte). Lokal
        # gekapselt: faellt der contains-Filter weg, bleiben Host-/Prefix-Treffer erhalten.
        try:
            for rec in self._get("ipam/ip-ranges/", {"contains": ip}):
                start = str(rec.get("start_address", "")).split("/")[0]
                end = str(rec.get("end_address", "")).split("/")[0]
                results.append(
                    NetworkObject(
                        name=rec.get("description") or f"{start}-{end}",
                        type=NetworkObjectType.NETWORK,
                        value=f"{start}-{end}",
                        tags=self._tags(rec),
                        source=self.name,
                    )
                )
        except httpx.HTTPError:
            pass
        return results

    # -- Referenzquelle (komplett lesen) ---------------------------------------

    def ping(self) -> str:
        """Leichter Lese-Zugriff (Status-API); gibt die NetBox-Version zurueck."""
        response = read_get(self._client, f"{self._api}/status/")
        response.raise_for_status()
        return str(response.json().get("netbox-version", "?"))

    def export(self) -> Iterator[ReferenceRecord]:
        for rec in self._paged("ipam/prefixes/"):
            yield self._record(ReferenceKind.PREFIX, rec, value=rec["prefix"])
        for rec in self._paged("ipam/ip-addresses/"):
            ip, _, plen = str(rec["address"]).partition("/")
            yield self._record(
                ReferenceKind.IP_ADDRESS,
                rec,
                value=ip,
                prefix_len=_int(plen),
                dns_name=rec.get("dns_name") or None,
            )
        for rec in self._paged("ipam/ip-ranges/"):
            start, _, plen = str(rec.get("start_address", "")).partition("/")
            end = str(rec.get("end_address", "")).split("/")[0]
            yield self._record(
                ReferenceKind.RANGE, rec, value=f"{start}-{end}", prefix_len=_int(plen)
            )
        for rec in self._paged("ipam/vlans/"):
            group = rec.get("group") or {}
            yield self._record(
                ReferenceKind.VLAN,
                rec,
                value=str(rec.get("vid")),
                name=rec.get("name"),
                vlan_vid=_int(rec.get("vid")),
                vlan_name=rec.get("name"),
                extra={"vlan_group": group.get("slug") or group.get("name")} if group else {},
            )

    def _paged(self, path: str) -> Iterator[dict[str, Any]]:
        offset = 0
        while True:
            response = read_get(
                self._client, f"{self._api}/{path}", params={"limit": PAGE_SIZE, "offset": offset}
            )
            response.raise_for_status()
            data = response.json()
            results = data.get("results", [])
            yield from results
            offset += len(results)
            if not results or offset >= int(data.get("count", 0)):
                return

    def _record(self, kind: ReferenceKind, rec: dict[str, Any], *, value: str, extra=None, **kw):
        vlan = rec.get("vlan") or {}
        attrs = {k: str(v) for k, v in (rec.get("custom_fields") or {}).items() if v is not None}
        attrs.update(extra or {})
        fields = {
            "vrf": _name(rec.get("vrf")),
            "description": rec.get("description") or None,
            "vlan_vid": _int(vlan.get("vid")) if vlan else None,
            "vlan_name": vlan.get("name") if vlan else None,
            "site": _name(rec.get("scope")) or _name(rec.get("site")),
            "tenant": _name(rec.get("tenant")),
            "status": _value(rec.get("status")),
            "tags": self._tags(rec),
            "attrs": attrs,
            "ref_id": str(rec["id"]) if rec.get("id") is not None else None,
            "url": _ui_url(rec.get("url")),
        }
        fields.update(kw)
        return ReferenceRecord(kind=kind, value=value, **fields)

    # -- intern ---------------------------------------------------------------

    def _get(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        response = read_get(self._client, f"{self._api}/{path}", params=params)
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


def _int(value) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _name(obj) -> str | None:
    if isinstance(obj, dict):
        return obj.get("name") or obj.get("display") or None
    return None


def _value(obj) -> str | None:
    if isinstance(obj, dict):
        return obj.get("value") or obj.get("label")
    return obj or None


def _ui_url(api_url: str | None) -> str | None:
    """API-Link -> Oberflaeche (NetBox: gleicher Pfad ohne /api)."""
    return api_url.replace("/api/", "/", 1) if api_url else None
