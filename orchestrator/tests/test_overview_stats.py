"""Kennzahlen der Übersicht (0.117.0, Anwender: „Dashboard erweitern mit Schnellansichten,
Grafen, Auswahl an Content“): Läufe je Tag, Änderungen des Routings je Art, Regeln je
Quellsystem."""

from datetime import UTC, datetime, timedelta

from app.models import (
    CollectorRun,
    IpamType,
    Platform,
    RunStatus,
    SourceSystem,
    Tenant,
    TopoChange,
)
from app.models.tenant import DnsType


def _seed(session_factory) -> tuple[int, int]:
    s = session_factory()
    t = Tenant(name="kunde-a", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    s.add(t)
    s.flush()
    src = SourceSystem(tenant_id=t.id, name="fw-01", platform=Platform.FORTIGATE, base_url="")
    s.add(src)
    s.flush()
    now = datetime.now(UTC)
    for i, (status, ago) in enumerate([(RunStatus.COMPLETED, 0), (RunStatus.FAILED, 0),
                                       (RunStatus.COMPLETED, 2), (RunStatus.QUEUED, 0),
                                       (RunStatus.COMPLETED, 40)]):  # fmt: skip
        s.add(CollectorRun(id=f"run-{i}", tenant_id=t.id, source_system_id=src.id,
                           platform="fortigate", status=status,
                           queued_at=now - timedelta(days=ago)))  # fmt: skip
    for kind, change, ago in [("route", "added", 1), ("route", "removed", 1),
                              ("bgp_peer", "changed", 2), ("route", "baseline", 1),
                              ("route", "added", 20)]:  # fmt: skip
        s.add(TopoChange(tenant_id=t.id, source_system_id=src.id, kind=kind, change=change,
                         key="10.0.0.0/24", at=now - timedelta(days=ago)))  # fmt: skip
    s.commit()
    ids = t.id, src.id
    s.close()
    return ids


def test_laeufe_je_tag(client, session_factory):
    tid, _ = _seed(session_factory)
    r = client.get("/jobs/daily", params={"tenant_id": tid, "days": 14})
    assert r.status_code == 200, r.text
    days = r.json()
    assert len(days) == 14 and days[-1]["day"] >= days[0]["day"]
    assert (days[-1]["ok"], days[-1]["failed"], days[-1]["open"]) == (1, 1, 1)
    assert sum(d["ok"] for d in days) == 2  # der Lauf vor 40 Tagen fehlt


def test_aenderungen_des_routings(client, session_factory):
    tid, _ = _seed(session_factory)
    r = client.get("/routing/changes/summary", params={"tenant_id": tid, "days": 7})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["total"] == 3  # ohne Ausgangsstand, ohne den älteren Eintrag
    assert got["by_kind"] == {"route": {"added": 1, "removed": 1}, "bgp_peer": {"changed": 1}}
