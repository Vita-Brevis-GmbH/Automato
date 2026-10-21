"""FortiGate-REST-Client gegen aufgezeichnete FortiOS-Antworten (kein Lab noetig).

Beweist den echten Abruf-Pfad: MockTransport liefert je cmdb-Endpunkt die
Beispieldaten, der Client liest sie, die Normalisierung loest Adressen/Services auf.
"""

import json
from importlib import resources

import httpx

from collectors.fortigate import normalize as norm
from collectors.fortigate.client import FortiGateClient


def _sample(name: str):
    return json.loads(resources.files("collectors.fortigate").joinpath(name).read_text())


def _client_with_mock() -> FortiGateClient:
    routes = {
        "/api/v2/cmdb/firewall/policy": "sample_policies.json",
        "/api/v2/cmdb/firewall/address": "sample_addresses.json",
        "/api/v2/cmdb/firewall/address6": "sample_addresses6.json",
        "/api/v2/cmdb/firewall/addrgrp": "sample_addrgrps.json",
        "/api/v2/cmdb/firewall/addrgrp6": "sample_addrgrps6.json",
        "/api/v2/cmdb/firewall.service/custom": "sample_services.json",
        "/api/v2/cmdb/firewall.service/group": "sample_service_groups.json",
        "/api/v2/cmdb/firewall/vip": "sample_vips.json",
        "/api/v2/cmdb/firewall/ippool": "sample_ippools.json",
        "/api/v2/cmdb/system/interface": "sample_interfaces.json",
        "/api/v2/cmdb/system/zone": "sample_zones.json",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"  # nur lesend
        if request.url.path == "/api/v2/cmdb/system/global":
            return httpx.Response(200, json={"results": {"hostname": "fw-01"}})
        if request.url.path == "/api/v2/monitor/system/status":
            return httpx.Response(403, json={})  # Monitor im Profil gesperrt: kein Abbruch
        fixture = routes.get(request.url.path)
        if fixture is None:
            return httpx.Response(404, json={"results": []})
        return httpx.Response(200, json={"results": _sample(fixture)})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return FortiGateClient("https://fw-01.lab", "token", client=client)


def test_client_gather_und_auth_header():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"results": []})

    client = FortiGateClient(
        "https://fw-01.lab", "geheim", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    client.policies()
    assert captured["auth"] == "Bearer geheim"


def test_abruf_und_normalisierung_ueber_client():
    d = _client_with_mock().gather()
    rules = {
        r.external_id: r
        for r in norm.normalize(
            d["policies"],
            d["addresses"],
            d["groups"],
            d["services"],
            d["vips"],
            d["addresses6"],
            d["groups6"],
        )
    }
    assert rules["1"].destinations == ["10.10.10.5"]
    assert rules["1"].services == ["tcp/443"]
    assert rules["3"].destinations == ["fqdn:api.partner.example"]


def test_gather_liefert_v6_und_ippools():
    d = _client_with_mock().gather()
    assert {a["name"] for a in d["addresses6"]} == {
        "H_adf04-001-ssgch.secn.ch",
        "N_share-000-v6",
    }
    assert d["ippools"][0]["name"] == "IPP_share-000-inet01"
    assert d["system"] == {"hostname": "fw-01"}
    assert {i["name"] for i in d["interfaces"]} >= {"port2", "srv-vl10"}


def test_ping_ok_und_fehler():
    import pytest

    c = FortiGateClient(
        "https://fg",
        "t",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"results": {"hostname": "fg"}})
            )
        ),
    )
    c.ping()  # kein Fehler bei 200

    c2 = FortiGateClient(
        "https://fg",
        "t",
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={}))),
    )
    with pytest.raises(httpx.HTTPStatusError):
        c2.ping()


def test_policy6_fehlt_ab_fortios7_und_wird_gelesen_wo_vorhanden():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if request.url.path.endswith("/firewall/policy6"):
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"results": []})

    client = FortiGateClient(
        "https://fw", "t", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert client.policies6() == []

    def handler6(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"policyid": 1}]})

    client6 = FortiGateClient(
        "https://fw", "t", client=httpx.Client(transport=httpx.MockTransport(handler6))
    )
    assert client6.policies6() == [{"policyid": 1}]
