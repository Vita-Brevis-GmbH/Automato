"""NetBox-Schreiber: enger Rahmen (Host/Pfad/Objekttyp), Bulk, Fehlermeldungen (ADR 0010)."""

import json

import httpx
import pytest
from sot_gateway.adapters.netbox import auth_header
from sot_gateway.adapters.netbox_writer import NetBoxWriteError, NetBoxWriter, WriteNotAllowed


def _writer(handler):
    sent: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(record))
    return NetBoxWriter("https://nb.kunde.lan", "t", client=client), sent


def test_bulk_anlegen_aendern_loeschen():
    def handler(request):
        body = json.loads(request.content or b"[]")
        if request.method == "POST":
            return httpx.Response(201, json=[{**o, "id": i + 1} for i, o in enumerate(body)])
        return httpx.Response(200 if request.method == "PATCH" else 204, json=[])

    writer, sent = _writer(handler)
    created = writer.create("ipam/vlans", [{"vid": v, "name": f"v{v}"} for v in range(450)])
    assert len(created) == 450 and created[0]["id"] == 1
    writer.update("ipam/vlans", [{"id": 1, "name": "neu"}])
    writer.delete("ipam/vlans", [1, 2])
    assert [r.method for r in sent] == ["POST", "POST", "POST", "PATCH", "DELETE"]  # Bloecke à 200
    assert json.loads(sent[-1].content) == [{"id": 1}, {"id": 2}]


def test_nur_erlaubte_objekttypen_und_eigener_host():
    writer, sent = _writer(lambda r: httpx.Response(200, json={"count": 0, "results": []}))
    writer.read("users/tokens")  # Lesen ist unterhalb der API erlaubt
    for call in (
        lambda: writer.create("users/tokens", [{}]),
        lambda: writer.create("extras/scripts", [{}]),
        lambda: writer.delete("core/data-sources", [1]),
        lambda: writer._client.post("https://anderes-ziel.lan/api/ipam/vlans/", json=[]),
        lambda: writer._client.get("https://nb.kunde.lan/admin/"),
    ):
        with pytest.raises(WriteNotAllowed):
            call()
    assert len(sent) == 1  # Blockiertes wurde nie gesendet


def test_kabel_schreibbar_fuer_die_verkabelung():
    """ADR 0031: Abschnitt Verkabelung legt Kabel an - nur `dcim/cables`, nicht die Enden."""
    writer, sent = _writer(lambda r: httpx.Response(201, json=[{"id": 1}]))
    writer.create("dcim/cables", [{"a_terminations": [], "b_terminations": []}])
    with pytest.raises(WriteNotAllowed):
        writer.create("dcim/cable-terminations", [{}])
    assert [r.url.path for r in sent] == ["/api/dcim/cables/"]


def test_fehler_von_netbox_mit_meldung():
    writer, _ = _writer(lambda r: httpx.Response(400, json=[{"vid": ["exists"]}]))
    with pytest.raises(NetBoxWriteError) as exc:
        writer.create("ipam/vlans", [{"vid": 10}])
    assert exc.value.status == 400 and "exists" in str(exc.value)


def test_token_header_klassisch_und_v2():
    assert auth_header("abc") == {"Authorization": "Token abc"}
    assert auth_header("nbt_x.y") == {"Authorization": "Bearer nbt_x.y"}
