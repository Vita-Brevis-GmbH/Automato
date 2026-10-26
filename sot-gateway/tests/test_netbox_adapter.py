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


def test_lesen_wiederholt_bei_timeout_und_503(monkeypatch):
    """Dev-Befund 0.17.0: ein ConnectTimeout bzw. 503 liess den ganzen Lauf scheitern - Lesen
    wird wiederholt (nur GET, also gefahrlos); bleibt der Fehler, kommt er an."""
    from sot_gateway import readonly

    waits: list[float] = []
    monkeypatch.setattr(readonly, "_sleep", waits.append)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectTimeout("timed out", request=request)
        if calls["n"] == 2:
            return httpx.Response(503, request=request)
        return httpx.Response(200, json={"netbox-version": "4.2.0"})

    nb = NetBoxAdapter(
        "https://nb.example.ch", "t", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert nb.ping() == "4.2.0"
    assert (calls["n"], waits) == (3, list(readonly.RETRY_DELAYS[:2]))

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    nb = NetBoxAdapter(
        "https://nb.example.ch", "t", client=httpx.Client(transport=httpx.MockTransport(down))
    )
    waits.clear()
    try:
        nb.ping()
    except httpx.ReadTimeout:
        pass
    else:  # pragma: no cover
        raise AssertionError("ReadTimeout erwartet")
    assert waits == list(readonly.RETRY_DELAYS)  # 4 Versuche, dann der Fehler
