"""Tests des Infoblox-Adapters gegen aufgezeichnete WAPI-Antworten (kein Lab noetig)."""

import json
from pathlib import Path

import httpx
from sot_gateway import NetworkObjectType, SoTAdapter
from sot_gateway.adapters import InfobloxAdapter

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


def _adapter_with_mock() -> InfobloxAdapter:
    """Adapter mit MockTransport, der WAPI-Antworten aus Fixtures liefert."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/record:host"):
            return httpx.Response(200, json=_fixture("wapi_host_records.json"))
        if request.url.path.endswith("/network"):
            return httpx.Response(200, json=_fixture("wapi_networks.json"))
        return httpx.Response(404, json=[])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return InfobloxAdapter("https://infoblox.example.local", "user", "pass", client=client)


def test_adapter_erfuellt_protokoll():
    assert isinstance(_adapter_with_mock(), SoTAdapter)


def test_get_by_name_liefert_kanonisches_objekt():
    obj = _adapter_with_mock().get_by_name("web01.example.local")
    assert obj is not None
    assert obj.type is NetworkObjectType.HOST
    assert obj.value == "10.1.2.3"
    assert obj.source == "infoblox"
    assert "Application=Webshop" in obj.tags


def test_search_by_ip_sortiert_longest_prefix_zuerst():
    results = _adapter_with_mock().search_by_ip("10.1.2.3")
    hosts = [r for r in results if r.type is NetworkObjectType.HOST]
    networks = [r for r in results if r.type is NetworkObjectType.NETWORK]
    assert len(hosts) == 1
    prefixes = [int(n.value.split("/")[1]) for n in networks]
    assert prefixes == sorted(prefixes, reverse=True)
