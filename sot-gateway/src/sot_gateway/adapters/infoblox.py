"""Infoblox-DDI-Adapter (WAPI), rein lesend (UC1).

Implementiert das SoTAdapter-Protokoll gegen die Infoblox WAPI.
Abgefragt werden host records, networks und ranges; die Antworten werden auf
das kanonische NetworkObject-Modell abgebildet.

`export()` liest die DDI komplett (WAPI-Paging): Netze und Network Container (v4/v6), Ranges,
Host-, A/AAAA-, PTR-Records und DHCP-Reservierungen inkl. Extensible Attributes - als neutrale
`ReferenceRecord`s (Referenzquelle, ADR 0010). Der Client ist per `enforce_read_only` auf
GET beschraenkt: aus Automato wird in die DDI nie geschrieben.
"""

import logging
from collections.abc import Iterator
from typing import Any

import httpx

from sot_gateway.models import NetworkObject, NetworkObjectType, ReferenceKind, ReferenceRecord
from sot_gateway.readonly import enforce_read_only
from sot_gateway.readonly import get as read_get

log = logging.getLogger(__name__)

PAGE_SIZE = 1000
# Extensible Attributes, die als Standort bzw. VLAN gelesen werden (Gross-/Kleinschreibung egal)
_SITE_KEYS = ("site", "standort", "location")
_VLAN_KEYS = ("vlan", "vlan id", "vlan_id", "vlanid")

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
        self._client = enforce_read_only(
            client or httpx.Client(auth=(username, password), verify=verify_tls, timeout=60.0)
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

    # -- Referenzquelle (komplett lesen) ---------------------------------------

    def ping(self) -> str:
        """Leichter Lese-Zugriff (ein Netz); gibt die WAPI-Version zurueck."""
        response = read_get(self._client, f"{self._wapi_url}/network", params={"_max_results": "1"})
        response.raise_for_status()
        return self._wapi_url.rsplit("/", 1)[-1]

    def export(self) -> Iterator[ReferenceRecord]:
        for obj in ("network", "ipv6network"):
            for rec in self._paged(obj, "comment,extattrs,network_view"):
                yield self._record(ReferenceKind.PREFIX, rec, value=rec["network"])
        # Network Container (0.74.1): Netze, die weiter unterteilt sind - ebenfalls Netze im
        # IPAM, Status „container“ wie in NetBox (sonst fehlten sie im Abgleich)
        for obj in ("networkcontainer", "ipv6networkcontainer"):
            for rec in self._paged(obj, "comment,extattrs,network_view"):
                yield self._record(
                    ReferenceKind.PREFIX, rec, value=rec["network"], status="container"
                )
        for rec in self._paged("range", "comment,extattrs,network_view,name"):
            start, end = rec.get("start_addr", ""), rec.get("end_addr", "")
            yield self._record(
                ReferenceKind.RANGE, rec, value=f"{start}-{end}", name=rec.get("name") or None
            )
        for rec in self._paged("record:host", "comment,extattrs,view,ipv4addrs,ipv6addrs"):
            addrs = [a.get("ipv4addr") for a in rec.get("ipv4addrs") or []]
            addrs += [a.get("ipv6addr") for a in rec.get("ipv6addrs") or []]
            for ip in filter(None, addrs):
                yield self._record(ReferenceKind.HOST, rec, value=ip, dns_name=rec.get("name"))
        for obj, field in (("record:a", "ipv4addr"), ("record:aaaa", "ipv6addr")):
            for rec in self._paged(obj, "comment,extattrs,view"):
                if rec.get(field):
                    yield self._record(
                        ReferenceKind.A_RECORD, rec, value=rec[field], dns_name=rec.get("name")
                    )
        for rec in self._paged("record:ptr", "comment,extattrs,view,ipv4addr,ipv6addr"):
            ip = rec.get("ipv4addr") or rec.get("ipv6addr")
            if ip:
                yield self._record(
                    ReferenceKind.PTR_RECORD, rec, value=ip, dns_name=rec.get("ptrdname")
                )
        for rec in self._paged("fixedaddress", "comment,extattrs,network_view,name,mac"):
            if rec.get("ipv4addr"):
                yield self._record(
                    ReferenceKind.FIXED_ADDRESS,
                    rec,
                    value=rec["ipv4addr"],
                    name=rec.get("name") or None,
                )

    def _paged(self, obj_type: str, fields: str) -> Iterator[dict[str, Any]]:
        """WAPI-Paging (_paging/_page_id); nicht verfuegbare Objekttypen werden uebersprungen."""
        params: dict[str, str] = {
            "_paging": "1",
            "_return_as_object": "1",
            "_max_results": str(PAGE_SIZE),
            "_return_fields+": fields,
        }
        while True:
            response = read_get(self._client, f"{self._wapi_url}/{obj_type}", params=params)
            if response.status_code in (400, 404) and "_page_id" not in params:
                log.warning("WAPI-Objekt %s nicht verfuegbar (%s)", obj_type, response.status_code)
                return
            response.raise_for_status()
            data = response.json()
            yield from data.get("result", []) if isinstance(data, dict) else data
            page = data.get("next_page_id") if isinstance(data, dict) else None
            if not page:
                return
            params = {**params, "_page_id": page}

    def _record(self, kind: ReferenceKind, rec: dict[str, Any], *, value: str, **kw):
        attrs = {
            key: str((attr or {}).get("value")) for key, attr in (rec.get("extattrs") or {}).items()
        }
        lower = {k.lower(): v for k, v in attrs.items()}
        view = rec.get("network_view") or rec.get("view")
        vlan = next((lower[k] for k in _VLAN_KEYS if k in lower), None)
        fields = {
            "vrf": view if view and view != "default" else None,
            "description": rec.get("comment") or None,
            "vlan_vid": int(vlan) if vlan and vlan.isdigit() else None,
            "site": next((lower[k] for k in _SITE_KEYS if k in lower), None),
            "tags": sorted(f"{k}={v}" for k, v in attrs.items()),
            "attrs": attrs,
            "ref_id": rec.get("_ref"),
        }
        fields.update(kw)
        return ReferenceRecord(kind=kind, value=value, **fields)

    # -- intern ---------------------------------------------------------------

    def _get(self, obj_type: str, params: dict[str, str]) -> list[dict[str, Any]]:
        response = read_get(
            self._client,
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
