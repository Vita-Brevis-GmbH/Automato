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
    (d,) = _client_with_mock().gather()["vdoms"]  # ohne VDOM-Liste: Standard-VDOM
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
    (v,) = d["vdoms"]
    assert {a["name"] for a in v["addresses6"]} == {
        "H_adf04-001-ssgch.secn.ch",
        "N_share-000-v6",
    }
    assert v["ippools"][0]["name"] == "IPP_share-000-inet01"
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


def test_antwort_ohne_json_nennt_den_pfad_optionale_tabellen_fehlen_still():
    """Dev-Befund 0.11.1: eine FortiGate brach mit "Expecting value: line 1 column 1" ab -
    HTTP 200 ohne JSON. Optionale Tabellen gelten dann als nicht vorhanden; fehlt eine
    Pflicht-Tabelle, nennt die Meldung Pfad, Status und Inhalt."""
    import pytest

    from collectors.fortigate.client import NotJson

    empty = {"/api/v2/cmdb/firewall/ldb-monitor", "/api/v2/cmdb/firewall/vip6",
             "/api/v2/monitor/system/status"}  # fmt: skip
    broken = {"path": None}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in empty:
            return httpx.Response(200, content=b"")
        if request.url.path == broken["path"]:
            return httpx.Response(200, html="<html><title> Web Filter\n Block </title>x</html>")
        if request.url.path == "/api/v2/cmdb/system/global":
            return httpx.Response(200, json={"results": {"hostname": "fw-01"}})
        return httpx.Response(200, json={"results": []})

    client = FortiGateClient(
        "https://fw-01.lab", "t", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    data = client.gather()
    assert data["vdoms"][0]["vips6"] == [] and data["vdoms"][0]["ldb_monitors"] == []
    assert data["system"] == {"hostname": "fw-01"}  # ohne Modell/Seriennummer

    broken["path"] = "/api/v2/cmdb/firewall/policy"
    with pytest.raises(NotJson) as err:
        client.gather()
    assert str(err.value) == (
        "FortiGate antwortet auf firewall/policy nicht mit JSON (HTTP 200, text/html, 48 Bytes,"
        " Seite „Web Filter Block“)"
    )


def _vdom_mock(seen: list, denied: set[str] = frozenset()) -> FortiGateClient:
    """FortiGate mit zwei VDOMs (+ internem vsys_ha): je VDOM eigene Policies/Objekte mit
    gleichen IDs und Namen; Interfaces global mit VDOM-Feld."""
    tables = {
        "root": {
            "firewall/policy": [{"policyid": 1, "name": "mgmt", "action": "accept",
                                 "srcaddr": [{"name": "N_a"}], "dstaddr": [{"name": "all"}],
                                 "service": [{"name": "ALL"}]}],
            "firewall/address": [{"name": "N_a", "type": "ipmask",
                                  "subnet": "10.0.0.0 255.255.255.0"}],
            "system/zone": [{"name": "lan", "interface": [{"interface-name": "port1"}]}],
        },
        "kunde-a": {
            "firewall/policy": [{"policyid": 1, "name": "web", "action": "accept",
                                 "srcaddr": [{"name": "N_a"}], "dstaddr": [{"name": "all"}],
                                 "service": [{"name": "ALL"}]}],
            "firewall/address": [{"name": "N_a", "type": "ipmask",
                                  "subnet": "10.50.0.0 255.255.255.0"}],
            "firewall/vip": [{"name": "VIP_web", "extip": "203.0.113.10",
                              "mappedip": [{"range": "10.50.0.10"}]}],
            "system/zone": [{"name": "lan", "interface": [{"interface-name": "port2"}]}],
        },
    }  # fmt: skip
    interfaces = [
        {"name": "port1", "type": "physical", "vdom": "root", "ip": "10.0.0.1 255.255.255.0"},
        {"name": "port2", "type": "physical", "vdom": "kunde-a", "ip": "10.50.0.1 255.255.255.0"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"  # nur lesend
        path = request.url.path.removeprefix("/api/v2/cmdb/")
        vdom = request.url.params.get("vdom")
        seen.append((path, vdom))
        if path == "system/vdom":
            names = ["root", "kunde-a", "vsys_ha"]
            return httpx.Response(200, json={"results": [{"name": n} for n in names]})
        if path == "system/global":
            return httpx.Response(200, json={"results": {"hostname": "fw-01"}})
        if vdom in denied:
            return httpx.Response(403, json={})
        if path == "system/interface":
            return httpx.Response(200, json={"results": interfaces})
        rows = tables.get(vdom or "root", {}).get(path, [])
        return httpx.Response(200, json={"vdom": vdom or "root", "results": rows})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return FortiGateClient("https://fw-01.lab", "token", client=client)


def test_alle_vdoms_einzeln_gelesen_gleiche_namen_getrennt(monkeypatch):
    """ADR 0019 Stufe 1: VDOM-Liste, dann jeder VDOM mit `vdom=`; interne vsys_-VDOMs nicht.
    Gleiche Policy-ID und gleicher Objektname in zwei VDOMs bleiben zwei Eintraege."""
    import collectors.fortigate.tasks as t
    from collectors.common.dto import Target

    seen: list = []
    client = _vdom_mock(seen)
    d = client.gather()
    assert [v["vdom"] for v in d["vdoms"]] == ["root", "kunde-a"]
    assert not any(v == "vsys_ha" for _, v in seen)
    assert ("firewall/policy", "kunde-a") in seen and ("firewall/policy", None) not in seen
    assert {(i["name"], i["vdom"]) for i in d["interfaces"]} == {
        ("port1", "root"),
        ("port2", "kunde-a"),
    }

    sent = {}
    monkeypatch.setattr(t, "gather", lambda target=None: d)
    monkeypatch.setattr(
        t, "submit_result", lambda run_id, result, via_agent=False: sent.update(result=result)
    )
    t.collect_once("r1", Target(name="fw-01"))
    result = sent["result"]
    assert result.status == "completed", result.message
    assert sorted((r.vdom, r.external_id, r.name) for r in result.rules) == [
        ("kunde-a", "1", "web"),
        ("root", "1", "mgmt"),
    ]
    by_vdom = {(o.vdom, o.name): o for o in result.objects if o.category == "address"}
    assert by_vdom[("root", "N_a")].values == ["10.0.0.0/24"]
    assert by_vdom[("kunde-a", "N_a")].values == ["10.50.0.0/24"]
    assert [(n.vdom, n.name) for n in result.nat_rules] == [("kunde-a", "VIP_web")]
    ports = {i.name: (i.vdom, i.zone) for i in result.inventory.interfaces}
    assert ports == {"port1": ("root", "lan"), "port2": ("kunde-a", "lan")}
    assert {s.vdom for s in result.inventory.subnets} == {"root", "kunde-a"}
    assert result.message == "VDOMs: root, kunde-a"


def test_vdom_ohne_leserecht_fehlt_mit_meldung(monkeypatch):
    """Ein VDOM mit HTTP 403 fehlt (Lauf bleibt vollstaendig fuer die anderen) und steht in der
    Lauf-Meldung; ohne VDOM-Liste (VDOM-Admin) gilt der Standard-VDOM aus der Antwort."""
    import collectors.fortigate.tasks as t

    d = _vdom_mock([], denied={"kunde-a"}).gather()
    assert [v["vdom"] for v in d["vdoms"]] == ["root"]
    assert d["skipped"] == [{"vdom": "kunde-a", "error": "HTTP 403"}]
    assert t._vdom_message(d) == (
        "VDOM nicht lesbar: kunde-a (HTTP 403) - das Profil braucht Lesezugriff auf alle VDOMs;"
        " gelesen: root"
    )

    def admin(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/system/vdom"):
            return httpx.Response(403, json={})
        assert "vdom" not in request.url.params  # Standard-VDOM des Kontos
        return httpx.Response(200, json={"vdom": "kunde-a", "results": []})

    client = FortiGateClient(
        "https://fw", "t", client=httpx.Client(transport=httpx.MockTransport(admin))
    )
    assert [v["vdom"] for v in client.gather()["vdoms"]] == ["kunde-a"]
