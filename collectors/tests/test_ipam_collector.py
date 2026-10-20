"""Referenzquellen-Collector (Infoblox/NetBox), Poll-Schleife, Nur-Lese-Schutz (ADR 0010)."""

import httpx
import pytest
from sot_gateway.models import ReferenceKind, ReferenceRecord
from sot_gateway.readonly import ReadOnlyViolation

from collectors.cisco_aci.client import ApicClient
from collectors.common.dto import Target
from collectors.fortigate.client import FortiGateClient


def _target(**kw) -> Target:
    base = {
        "source_system_id": 9,
        "name": "ddi-01",
        "platform": "infoblox",
        "base_url": "https://ddi",
    }
    return Target(**(base | kw))


class _FakeAdapter:
    def __init__(self, n):
        self.n = n

    def export(self):
        for i in range(self.n):
            yield ReferenceRecord(kind=ReferenceKind.HOST, value=f"10.0.{i // 250}.{i % 250}")

    def ping(self):
        return "v2.12"


def test_infoblox_lauf_liefert_in_teilen(monkeypatch):
    import collectors.ipam.tasks as t

    seen = {}
    monkeypatch.setattr(
        t,
        "InfobloxAdapter",
        lambda url, user, pw, verify_tls=True: seen.update(url=url, user=user)
        or _FakeAdapter(4500),
    )
    batches, final = [], {}
    monkeypatch.setattr(
        t,
        "submit_references",
        lambda run_id, batch, via_agent=False: batches.append(len(batch.records)),
    )
    monkeypatch.setattr(
        t,
        "submit_result",
        lambda run_id, result, via_agent=False: final.update(result.model_dump(), via=via_agent),
    )
    result = t.collect_infoblox("r1", _target(username="ro", password="pw"), via_agent=True)
    assert result.status == "completed" and result.reference_count == 4500
    assert (
        batches == [2000, 2000, 500] and final["reference_count"] == 4500 and final["via"] is True
    )
    assert seen == {"url": "https://ddi", "user": "ro"}


def test_ipam_ohne_zugangsdaten_schlaegt_fehl(monkeypatch):
    import collectors.ipam.tasks as t

    monkeypatch.setattr(t, "submit_result", lambda run_id, result, via_agent=False: None)
    result = t.collect_infoblox("r1", _target(), via_agent=True)
    assert result.status == "failed" and "ddi-01" in result.message
    result = t.collect_netbox("r1", _target(platform="netbox", name="nb", base_url=""))
    assert result.status == "failed" and "keine Adresse" in result.message


def test_ipam_check(monkeypatch):
    import collectors.ipam.tasks as t

    monkeypatch.setattr(t, "NetBoxAdapter", lambda url, token, verify_tls=True: _FakeAdapter(0))
    ok, msg = t.check_netbox(_target(platform="netbox", token="tok"))
    assert ok and "NetBox v2.12" in msg


def test_poll_arbeitet_alle_offenen_laeufe_ab(monkeypatch, capsys):
    import collectors.run as run

    queue = [
        {"platform": "fortigate", "run_id": "a", "target": None},
        {
            "platform": "infoblox",
            "run_id": "b",
            "target": {"name": "ddi-01", "platform": "infoblox"},
        },
    ]
    monkeypatch.setattr(
        run.agent_api, "next_run", lambda platform=None: queue.pop(0) if queue else None
    )
    done = []

    class R:
        def __init__(self, status):
            self.status, self.message = status, None

    monkeypatch.setitem(
        run.RUNNERS, "fortigate", lambda rid, target, via_agent: done.append(rid) or R("completed")
    )
    monkeypatch.setitem(
        run.RUNNERS, "infoblox", lambda rid, target, via_agent: done.append(rid) or R("failed")
    )
    assert run.main(["--poll"]) == 1  # ein Lauf fehlgeschlagen
    assert done == ["a", "b"]
    assert "infoblox run b (ddi-01): failed" in capsys.readouterr().out


def test_geraete_clients_sind_nur_lesend():
    mock = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    fgt = FortiGateClient("https://fw", "t", client=mock)
    with pytest.raises(ReadOnlyViolation):
        fgt._client.post("https://fw/api/v2/cmdb/firewall/policy", json={})
    apic = ApicClient(
        "https://apic",
        "u",
        "p",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200, json={"imdata": [{"aaaLogin": {"attributes": {"token": "x"}}}]}
                )
            )
        ),
    )
    apic.login()  # Login-POST ist erlaubt
    with pytest.raises(ReadOnlyViolation):
        apic._client.post("https://apic/api/mo/uni/tn-x.json", json={})
    with pytest.raises(ReadOnlyViolation):
        apic._client.delete("https://apic/api/mo/uni/tn-x.json")
