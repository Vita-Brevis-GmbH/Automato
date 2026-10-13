"""FortiGate-REST-Client gegen aufgezeichnete FortiOS-Antworten (kein Lab noetig).

Beweist den echten Abruf-Pfad: MockTransport liefert je cmdb-Endpunkt die
Beispieldaten, der Client liest sie, die Normalisierung loest Adressen/Services auf.
"""

import json
from importlib import resources

import httpx
from collectors.fortigate.client import FortiGateClient

from collectors.fortigate import normalize as norm


def _sample(name: str):
    return json.loads(resources.files("collectors.fortigate").joinpath(name).read_text())


def _client_with_mock() -> FortiGateClient:
    routes = {
        "/api/v2/cmdb/firewall/policy": "sample_policies.json",
        "/api/v2/cmdb/firewall/address": "sample_addresses.json",
        "/api/v2/cmdb/firewall/addrgrp": "sample_addrgrps.json",
        "/api/v2/cmdb/firewall.service/custom": "sample_services.json",
        "/api/v2/cmdb/firewall/vip": "sample_vips.json",
    }

    def handler(request: httpx.Request) -> httpx.Response:
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
    policies, addresses, groups, services, vips = _client_with_mock().gather()
    rules = {r.external_id: r for r in norm.normalize(policies, addresses, groups, services, vips)}
    assert rules["1"].destinations == ["10.10.10.5"]
    assert rules["1"].services == ["tcp/443"]
    assert rules["3"].destinations == ["fqdn:api.partner.example"]
