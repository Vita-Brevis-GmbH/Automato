"""Tests des NetBox-Adapters gegen aufgezeichnete REST-Antworten (kein Lab noetig)."""

import json
from pathlib import Path

import httpx
from sot_gateway import NetworkObjectType, SoTAdapter
from sot_gateway.adapters import NetBoxAdapter

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


def _adapter_with_mock() -> NetBoxAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/ipam/ip-addresses/"):
            return httpx.Response(200, json={"results": _fixture("netbox_ip_addresses.json")})
        if request.url.path.endswith("/ipam/prefixes/"):
            return httpx.Response(200, json={"results": _fixture("netbox_prefixes.json")})
        return httpx.Response(200, json={"results": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return NetBoxAdapter("https://netbox.example.local", "token", client=client)


def test_adapter_erfuellt_protokoll():
    assert isinstance(_adapter_with_mock(), SoTAdapter)


def test_get_by_name_ueber_dns_name():
    obj = _adapter_with_mock().get_by_name("web01.lab.local")
    assert obj is not None
    assert obj.type is NetworkObjectType.HOST
    assert obj.value == "10.10.10.5"
    assert obj.source == "netbox"
    assert "zone-server" in obj.tags


def test_search_by_ip_longest_prefix_zuerst():
    results = _adapter_with_mock().search_by_ip("10.10.10.5")
    networks = [r for r in results if r.type is NetworkObjectType.NETWORK]
    prefixes = [int(n.value.split("/")[1]) for n in networks]
    assert prefixes == sorted(prefixes, reverse=True)  # /24 vor /16
    assert any(r.type is NetworkObjectType.HOST for r in results)
