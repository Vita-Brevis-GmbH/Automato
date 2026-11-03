"""Referenzquellen-Collector (Infoblox/NetBox/AD-DNS), Poll-Schleife, Nur-Lese-Schutz (ADR 0010,
ADR 0045)."""

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


def test_ad_dns_lauf_mit_zonen_aus_dem_lesebereich(monkeypatch):
    """AD-DNS (ADR 0045): Server aus der Adresse, Zonen aus dem Lesebereich, ohne Zugangsdaten."""
    from sot_gateway.adapters import dns_zone

    import collectors.ipam.tasks as t

    seen = {}

    def transfer(address, zone, port, timeout, lifetime):
        seen.setdefault("calls", []).append((address, zone, port))
        raise dns_zone.ZoneTransferError(zone, "dc01.example.ch", "refused", "REFUSED")

    real = dns_zone.DnsZoneAdapter.from_url
    monkeypatch.setattr(
        t.DnsZoneAdapter,
        "from_url",
        lambda url, zones: real(url, zones, transfer=transfer, resolve=lambda h, p: "10.0.0.10"),
    )
    monkeypatch.setattr(t, "submit_result", lambda run_id, result, via_agent=False: None)
    target = _target(platform="ad_dns", name="ad", base_url="dns://dc01.example.ch",
                     read_scope="example.ch\n10.in-addr.arpa")  # fmt: skip
    result = t.collect_ad_dns("r1", target)
    assert result.status == "failed"
    assert result.message.startswith("Zonentransfer von example.ch abgelehnt (REFUSED). Hinweis:")
    assert seen["calls"] == [("10.0.0.10", "example.ch", 53)]
    ok, msg = t.check_ad_dns(target)
    assert not ok and "Zonenuebertragungen" in msg
    # ohne Zonen bzw. Adresse: klare Meldung
    result = t.collect_ad_dns("r1", _target(platform="ad_dns", name="ad", base_url="dns://dc01"))
    assert result.status == "failed" and "keine Zonen" in result.message
    result = t.collect_ad_dns("r1", _target(platform="ad_dns", name="ad", base_url=""))
    assert result.status == "failed" and "keine Adresse" in result.message


def test_ad_dns_check(monkeypatch):
    import collectors.ipam.tasks as t

    class _Zones:
        def ping(self):
            return {"example.ch": 42, "10.in-addr.arpa": 7}

    monkeypatch.setattr(t.DnsZoneAdapter, "from_url", lambda url, zones: _Zones())
    ok, msg = t.check_ad_dns(_target(platform="ad_dns", base_url="dns://dc01",
                                     read_scope="example.ch,10.in-addr.arpa"))  # fmt: skip
    assert ok and msg == "erreichbar: dns://dc01 (Zonentransfer erlaubt fuer 2 Zone(n))"


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
