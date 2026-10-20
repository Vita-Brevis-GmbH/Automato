"""ACI-Ergebnis einspielen: Wiederholungslauf, doppelte Endpunkte, Ingest-Fehler (kein 500)."""

from app.models import AuditLog, CollectorRun, EpgEndpoint, IpamType, Platform, SourceSystem, Tenant
from app.models.tenant import DnsType

TOKEN = {"X-Internal-Token": "dev-only-token"}


class _NoSoT:
    name = "netbox"

    def search_by_ip(self, ip):
        return []

    def get_by_name(self, name):
        return None


def _setup(session_factory, monkeypatch) -> tuple[int, int]:
    monkeypatch.setattr("app.api.internal.build_adapter", lambda tenant, settings: _NoSoT())
    s = session_factory()
    t = Tenant(name="kunde-a", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    s.add(t)
    s.flush()
    apic = SourceSystem(tenant_id=t.id, name="apic", platform=Platform.CISCO_ACI, base_url="")
    s.add(apic)
    s.commit()
    ids = t.id, apic.id
    s.close()
    return ids


def _deliver(client, tid: int, sid: int, endpoints: list[dict]) -> tuple[str, dict]:
    body = {"platform": "cisco_aci", "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    epg = {"name": "EG_web", "ap": "AP_1", "bd": "BD_1", "subnets": [], "endpoints": endpoints}
    result = {"status": "completed", "epgs": [epg]}
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200, r.text  # nie 500 - Fehler stehen in der Quittung
    assert r.json()["run_id"] == run_id
    return run_id, r.json()


def test_wiederholungslauf_mit_gleichen_endpunkten(client, session_factory, monkeypatch):
    tid, sid = _setup(session_factory, monkeypatch)
    eps = [{"ip": "10.0.0.1", "mac": "aa"}]
    for _ in range(2):
        run_id, ack = _deliver(client, tid, sid, eps)
        assert ack["status"] == "completed"
    s = session_factory()
    assert s.get(CollectorRun, run_id).status == "completed"
    assert [e.ip for e in s.query(EpgEndpoint).all()] == ["10.0.0.1"]
    s.close()


def test_doppelte_ip_in_einer_epg(client, session_factory, monkeypatch):
    tid, sid = _setup(session_factory, monkeypatch)
    eps = [{"ip": "10.0.0.1", "mac": "aa"}, {"ip": "10.0.0.1", "mac": "bb"}]
    run_id, _ = _deliver(client, tid, sid, eps)
    s = session_factory()
    assert s.get(CollectorRun, run_id).status == "completed"
    assert [(e.ip, e.mac) for e in s.query(EpgEndpoint).all()] == [("10.0.0.1", "aa")]
    s.close()


def test_ingest_fehler_wird_fehlgeschlagener_lauf_statt_500(client, session_factory, monkeypatch):
    tid, sid = _setup(session_factory, monkeypatch)

    def boom(*args, **kwargs):
        raise ValueError("kaputte Daten")

    monkeypatch.setattr("app.api.internal.ingest.persist_result", boom)
    run_id, ack = _deliver(client, tid, sid, [])
    assert ack["status"] == "failed" and "kaputte Daten" in ack["message"]
    s = session_factory()
    run = s.get(CollectorRun, run_id)
    assert run.status == "failed" and "kaputte Daten" in run.message
    assert s.query(AuditLog).filter_by(action="collector_run.failed").count() == 1
    s.close()


def test_db_fehler_im_ingest_wird_zurueckgerollt(client, session_factory, monkeypatch):
    from app.models import Epg

    tid, sid = _setup(session_factory, monkeypatch)

    def duplicate(session, tenant_id, epgs):
        epg = Epg(tenant_id=tenant_id, name="EG_x")
        session.add(epg)
        session.flush()
        session.add_all([EpgEndpoint(epg_id=epg.id, ip="1.1.1.1") for _ in range(2)])
        session.flush()  # IntegrityError (epg_id, ip)

    monkeypatch.setattr("app.store.ingest._apply_epgs", duplicate)
    run_id, _ = _deliver(client, tid, sid, [])
    s = session_factory()
    run = s.get(CollectorRun, run_id)
    assert run.status == "failed" and "IntegrityError" in run.message
    assert "INSERT" not in run.message  # kein SQL in der Meldung
    assert s.query(Epg).count() == 0  # Teil-Ingest verworfen
    s.close()
