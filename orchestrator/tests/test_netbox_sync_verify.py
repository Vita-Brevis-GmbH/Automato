"""Nachkontrolle des NetBox-Syncs (0.115.0, ADR 0042, Roadmap Version 1 Nr. 5).

Nach „Übernehmen“ liest Automato die Ziel-NetBox über einen eigenen, rein lesenden Client neu
und vergleicht je geschriebenem Objekt: geschrieben = gelesen? Abweichungen stehen am Plan
(`verification`), in der Meldung und als Befund `netbox_sync_mismatch` unter Findings. Der
Lesepfad kann nicht schreiben (`sot_gateway.readonly`); scheitert er, bleibt die Übernahme
gültig.
"""

import httpx
import pytest
from app.api import netbox_sync as api
from app.models import SourceSystem
from sot_gateway.readonly import ReadOnlyViolation
from test_netbox_sync import (
    ADMIN,
    DDI,
    FakeNetBox,
    _apply,
    _plan,
    _reader,
    _references,
    _setup,
    fake_netbox,
)


@pytest.fixture
def netbox(monkeypatch):
    return fake_netbox(monkeypatch)


class StubbornNetBox(FakeNetBox):
    """NetBox, die nach dem Schreiben etwas anders hält: ein Validator schreibt Beschreibungen
    von Netzen um, IP-Bereiche werden still verworfen, Löschen von IPs wirkt nicht."""

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/").strip("/")
        if request.method == "DELETE" and path == "ipam/ip-addresses":
            return httpx.Response(204)
        if request.method == "POST" and path == "ipam/ip-ranges":
            response = super().handler(request)
            for obj in response.json():
                del self.db[path][obj["id"]]
            return response
        response = super().handler(request)
        if request.method in ("POST", "PATCH") and path == "ipam/prefixes":
            for o in self.objects(path):
                o["description"] = "vom Validator"
        return response


@pytest.fixture
def stubborn(monkeypatch):
    fake = StubbornNetBox()

    def make_writer(target):
        token = api.credentials.decrypt(target.credentials).get("write_token")
        client = httpx.Client(transport=httpx.MockTransport(fake.handler))
        return api.NetBoxWriter(target.base_url, token, client=client)

    monkeypatch.setattr(api, "make_writer", make_writer)
    _reader(monkeypatch, fake)
    return fake


def _findings(client, tid) -> list[dict]:
    r = client.get("/findings", params={"tenant_id": tid, "type": "netbox_sync_mismatch"})
    assert r.status_code == 200, r.text
    return r.json()


def test_geschrieben_gleich_gelesen(client, netbox):
    ids = _setup(client)
    plan = _plan(client, ids)
    written = sum(plan["totals"][a] for a in ("create", "update", "delete"))
    done = _apply(client, plan["id"])
    assert done["status"] == "applied", done
    check = done["verification"]
    assert check["checked"] == written and check["ok"] == written
    assert check["deviation_count"] == 0 and check["deviations"] == [] and check["at"]
    assert done["message"] == (
        f"Übernommen und nachkontrolliert: geschrieben = gelesen ({written} Objekte) – NetBox"
        " wird neu eingelesen."
    )
    assert _findings(client, ids["tid"]) == []
    en = client.get(f"/netbox-sync/plans/{plan['id']}", headers={**ADMIN, "Accept-Language": "en"})
    assert en.json()["message"].startswith("Applied and verified: written = read")


def test_abweichungen_am_plan_und_als_befund(client, stubborn):
    ids = _setup(client)
    done = _apply(client, _plan(client, ids)["id"])
    assert done["status"] == "applied", done
    check = done["verification"]
    problems = {(d["kind"], d["key"]): d for d in check["deviations"]}
    # Netz: geschrieben, NetBox hält eine andere Beschreibung
    net = problems[("prefix", "10.10.10.0/24")]
    assert net["problem"] == "differs" and net["action"] == "create"
    assert {c["field"] for c in net["changes"]} == {"description"}
    # IP-Bereich still verworfen
    assert problems[("ip_range", "10.10.10.100-10.10.10.150")]["problem"] == "missing"
    assert check["deviation_count"] == len(check["deviations"]) == len(problems)
    assert check["by_kind"]["prefix"] >= 1 and check["ok"] == check["checked"] - len(problems)
    assert done["message"].startswith(
        f"Übernommen – Nachkontrolle: {check['deviation_count']} Abweichungen von"
    )
    found = {f["subject_id"]: f for f in _findings(client, ids["tid"])}
    assert len(found) == check["deviation_count"]
    f = found["netbox|prefix|10.10.10.0/24"]
    assert f["severity"] == "warning" and f["subject_kind"] == "netbox"
    assert f["message"].startswith("netbox: Netz 10.10.10.0/24 geschrieben, NetBox zeigt danach")
    assert "description" in f["message"] and "vom Validator" in f["message"]
    en = client.get("/findings", params={"tenant_id": ids["tid"], "type": "netbox_sync_mismatch"},
                    headers={"Accept-Language": "en"}).json()  # fmt: skip
    assert any("created, but not in NetBox after writing" in x["message"] for x in en)

    # IP verschwindet aus der DDI: Löschen wirkt in dieser NetBox nicht
    _references(client, ids, [r for r in DDI if r["value"] != "10.10.10.6"])
    plan = _plan(client, ids, allow_delete=True)
    check = _apply(client, plan["id"])["verification"]
    gone = next(d for d in check["deviations"] if d["key"] == "10.10.10.6")
    assert (gone["kind"], gone["action"], gone["problem"]) == ("ip_address", "delete",
                                                               "still_there")  # fmt: skip
    found = {f["subject_id"]: f for f in _findings(client, ids["tid"])}
    assert (
        "gelöscht, steht nach dem Schreiben aber noch in NetBox"
        in (found["netbox|ip_address|10.10.10.6"]["message"])
    )


def test_lesepfad_kann_nicht_schreiben(client, session_factory, monkeypatch):
    fake = fake_netbox(monkeypatch)
    ids = _setup(client)
    s = session_factory()
    target = s.get(SourceSystem, ids["nb"])
    reader = api.make_reader(target)  # Fixture: echter Leser, simulierter Transport
    s.close()
    assert not hasattr(reader, "create") and not hasattr(reader, "update")
    with pytest.raises(ReadOnlyViolation):
        reader._client.create("ipam/prefixes", [{"prefix": "10.99.0.0/24"}])
    assert fake.writes == [] and reader.read("ipam/prefixes") == []
    reader.close()


def test_nachkontrolle_scheitert_uebernahme_bleibt(client, netbox, monkeypatch):
    ids = _setup(client)
    plan = _plan(client, ids)

    def offline(target):
        raise httpx.ConnectTimeout("timed out", request=httpx.Request("GET", target.base_url))

    monkeypatch.setattr(api, "make_reader", offline)
    done = _apply(client, plan["id"])
    assert done["status"] == "applied" and done["result"]["interface"]["create"] == 5
    assert "error" in done["verification"]
    assert done["message"].startswith("Übernommen – Nachkontrolle nicht möglich: NetBox nicht")
    assert client.dispatched[-1][0] == "netbox"  # neu einlesen wie bisher
