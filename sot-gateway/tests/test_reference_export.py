"""Komplettes Lesen der Referenzquellen + Nur-Lese-Schutz (ADR 0010), ohne Lab."""

import httpx
import pytest
from sot_gateway.adapters import InfobloxAdapter, NetBoxAdapter
from sot_gateway.models import ReferenceKind
from sot_gateway.readonly import ReadOnlyViolation, enforce_read_only


def test_read_only_blockiert_schreibende_methoden():
    sent = []
    client = enforce_read_only(
        httpx.Client(
            transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200))
        ),
        allow_post=("/api/aaaLogin.json",),
    )
    client.get("https://x/api/thing")
    client.post("https://x/api/aaaLogin.json", json={})
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        with pytest.raises(ReadOnlyViolation):
            client.request(method, "https://x/api/thing")
    assert [r.method for r in sent] == ["GET", "POST"]  # Blockiertes wurde nie gesendet


def test_adapter_clients_sind_nur_lesend():
    mock = httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    nb = NetBoxAdapter("https://nb", "t", client=httpx.Client(transport=mock))
    ib = InfobloxAdapter("https://ib", "u", "p", client=httpx.Client(transport=mock))
    for adapter in (nb, ib):
        with pytest.raises(ReadOnlyViolation):
            adapter._client.post("https://nb/api/ipam/prefixes/", json={})
        with pytest.raises(ReadOnlyViolation):
            adapter._client.delete("https://nb/api/ipam/prefixes/1/")


def _netbox_handler(request: httpx.Request) -> httpx.Response:
    assert request.method == "GET"
    path, offset = request.url.path, int(request.url.params.get("offset", 0))
    if path == "/api/status/":
        return httpx.Response(200, json={"netbox-version": "4.6.10"})
    if path == "/api/ipam/prefixes/":
        rows = [
            {
                "id": 1,
                "prefix": "10.20.0.0/24",
                "vrf": {"name": "VRF_share"},
                "vlan": {"vid": 110, "name": "EG_web"},
                "scope": {"name": "RZ1"},
                "tenant": {"name": "Kunde A"},
                "status": {"value": "active"},
                "description": "Web",
                "tags": [{"slug": "zone-server"}],
                "custom_fields": {"owner": "team-web", "leer": None},
                "url": "https://nb/api/ipam/prefixes/1/",
            },
            {"id": 2, "prefix": "10.30.0.0/24", "url": "https://nb/api/ipam/prefixes/2/"},
        ]
        page = rows[offset : offset + 1]  # 1 Eintrag je Seite -> Paging wird genutzt
        return httpx.Response(200, json={"count": len(rows), "results": page})
    if path == "/api/ipam/ip-addresses/":
        rows = [{"id": 7, "address": "10.20.0.5/24", "dns_name": "web01.example.ch"}]
        return httpx.Response(200, json={"count": 1, "results": rows[offset:]})
    if path == "/api/ipam/ip-ranges/":
        rows = [{"id": 9, "start_address": "10.20.0.100/24", "end_address": "10.20.0.150/24"}]
        return httpx.Response(200, json={"count": 1, "results": rows[offset:]})
    if path == "/api/ipam/vlans/":
        rows = [{"id": 3, "vid": 110, "name": "EG_web", "group": {"slug": "fabric-1"}}]
        return httpx.Response(200, json={"count": 1, "results": rows[offset:]})
    return httpx.Response(404)


def test_netbox_export_komplett_mit_paging(monkeypatch):
    import sot_gateway.adapters.netbox as nb_mod

    monkeypatch.setattr(nb_mod, "PAGE_SIZE", 1)
    nb = NetBoxAdapter(
        "https://nb", "t", client=httpx.Client(transport=httpx.MockTransport(_netbox_handler))
    )
    assert nb.ping() == "4.6.10"
    records = list(nb.export())
    kinds = [r.kind for r in records]
    assert kinds.count(ReferenceKind.PREFIX) == 2
    p = records[0]
    assert (p.value, p.vrf, p.vlan_vid, p.site, p.tenant, p.status) == (
        "10.20.0.0/24",
        "VRF_share",
        110,
        "RZ1",
        "Kunde A",
        "active",
    )
    assert p.tags == ["zone-server"] and p.attrs == {"owner": "team-web"}
    assert p.url == "https://nb/ipam/prefixes/1/" and p.ref_id == "1"
    ip = next(r for r in records if r.kind is ReferenceKind.IP_ADDRESS)
    assert (ip.value, ip.prefix_len, ip.dns_name) == ("10.20.0.5", 24, "web01.example.ch")
    rng = next(r for r in records if r.kind is ReferenceKind.RANGE)
    assert rng.value == "10.20.0.100-10.20.0.150" and rng.prefix_len == 24
    vlan = next(r for r in records if r.kind is ReferenceKind.VLAN)
    assert (vlan.value, vlan.vlan_vid, vlan.attrs["vlan_group"]) == ("110", 110, "fabric-1")


def _wapi_handler(request: httpx.Request) -> httpx.Response:
    assert request.method == "GET"
    obj = request.url.path.rsplit("/", 1)[-1]
    page = request.url.params.get("_page_id")
    ext = {"Site": {"value": "RZ1"}, "VLAN": {"value": "110"}}
    if obj == "network":
        if page is None:
            return httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "network": "10.20.0.0/24",
                            "network_view": "default",
                            "comment": "Web",
                            "extattrs": ext,
                            "_ref": "network/a",
                        }
                    ],
                    "next_page_id": "p2",
                },
            )
        return httpx.Response(
            200,
            json={
                "result": [
                    {"network": "10.30.0.0/24", "network_view": "intern", "_ref": "network/b"}
                ]
            },
        )
    if obj == "ipv6network":
        return httpx.Response(400, json={"Error": "not licensed"})  # nicht verfuegbar -> skip
    if obj == "range":
        return httpx.Response(
            200,
            json={
                "result": [
                    {"start_addr": "10.20.0.100", "end_addr": "10.20.0.150", "_ref": "range/c"}
                ]
            },
        )
    if obj == "record:host":
        return httpx.Response(
            200,
            json={
                "result": [
                    {
                        "name": "web01.example.ch",
                        "ipv4addrs": [{"ipv4addr": "10.20.0.5"}],
                        "ipv6addrs": [{"ipv6addr": "2001:db8::5"}],
                        "_ref": "record:host/d",
                    }
                ]
            },
        )
    if obj == "record:a":
        return httpx.Response(
            200,
            json={
                "result": [
                    {"name": "app.example.ch", "ipv4addr": "10.20.0.6", "_ref": "record:a/e"}
                ]
            },
        )
    if obj == "record:ptr":
        return httpx.Response(
            200,
            json={
                "result": [
                    {
                        "ptrdname": "web01.example.ch",
                        "ipv4addr": "10.20.0.5",
                        "_ref": "record:ptr/f",
                    }
                ]
            },
        )
    if obj == "fixedaddress":
        return httpx.Response(
            200,
            json={
                "result": [{"ipv4addr": "10.20.0.7", "name": "printer", "_ref": "fixedaddress/g"}]
            },
        )
    return httpx.Response(200, json={"result": []})


def test_infoblox_export_komplett_mit_paging():
    ib = InfobloxAdapter(
        "https://ib", "u", "p", client=httpx.Client(transport=httpx.MockTransport(_wapi_handler))
    )
    records = list(ib.export())
    by_value = {(r.kind, r.value): r for r in records}
    net = by_value[(ReferenceKind.PREFIX, "10.20.0.0/24")]
    assert (net.vrf, net.site, net.vlan_vid, net.description) == (None, "RZ1", 110, "Web")
    assert net.attrs == {"Site": "RZ1", "VLAN": "110"} and net.ref_id == "network/a"
    assert by_value[(ReferenceKind.PREFIX, "10.30.0.0/24")].vrf == "intern"  # zweite Seite
    assert by_value[(ReferenceKind.HOST, "10.20.0.5")].dns_name == "web01.example.ch"
    assert (ReferenceKind.HOST, "2001:db8::5") in by_value
    assert by_value[(ReferenceKind.A_RECORD, "10.20.0.6")].dns_name == "app.example.ch"
    assert by_value[(ReferenceKind.PTR_RECORD, "10.20.0.5")].dns_name == "web01.example.ch"
    assert by_value[(ReferenceKind.FIXED_ADDRESS, "10.20.0.7")].name == "printer"
    assert (ReferenceKind.RANGE, "10.20.0.100-10.20.0.150") in by_value
