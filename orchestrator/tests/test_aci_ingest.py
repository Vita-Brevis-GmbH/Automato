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

    def duplicate(session, run, epgs):
        epg = Epg(tenant_id=run.tenant_id, name="EG_x")
        session.add(epg)
        session.flush()
        session.add_all([EpgEndpoint(epg_id=epg.id, ip="1.1.1.1") for _ in range(2)])
        session.flush()  # IntegrityError (epg_id, ip)

    monkeypatch.setattr("app.store.ingest._apply_epgs", duplicate)
    run_id, _ = _deliver(client, tid, sid, [])
    s = session_factory()
    run = s.get(CollectorRun, run_id)
    # SQLite meldet IntegrityError, Postgres UniqueViolation - beides ohne SQL in der Meldung
    assert run.status == "failed" and "Einspielen fehlgeschlagen" in run.message
    assert "uq_epg_endpoint_epg_ip" in run.message or "UNIQUE" in run.message
    assert "INSERT" not in run.message  # kein SQL in der Meldung
    assert s.query(Epg).count() == 0  # Teil-Ingest verworfen
    s.close()


def _post(client, tid: int, sid: int, result: dict) -> str:
    body = {"platform": "cisco_aci", "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text
    return run_id


A_WEB = "uni/tn-TE_a/ap-AP_1/epg-EG_web"
B_WEB = "uni/tn-TE_b/ap-AP_2/epg-EG_web"
A_EXT = "uni/tn-TE_a/out-L3O_inet/instP-EN_inet"


def _multi(with_b: bool = True) -> dict:
    epgs = [
        {
            "name": "EG_web",
            "dn": A_WEB,
            "tenant": "TE_a",
            "ap": "AP_1",
            "bd": "BD_web",
            "vrf": "VRF_a",
            "subnets": [],
            "bd_subnets": ["10.1.0.1/24"],
            "endpoints": [{"ip": "10.1.0.10", "mac": "aa"}],
        },
        {
            "name": "EN_inet",
            "kind": "external",
            "dn": A_EXT,
            "tenant": "TE_a",
            "l3out": "L3O_inet",
            "vrf": "VRF_a",
            "subnets": ["0.0.0.0/0"],
        },
    ]
    rules = [
        {
            "source_platform": "cisco_aci",
            "external_id": "uni/tn-TE_a/brc-CT_web/SJ_1",
            "name": "CT_web:SJ_1",
            "action": "allow",
            "sources": [f"epg:{A_EXT}"],
            "destinations": [f"epg:{A_WEB}"],
            "services": ["tcp/443"],
        }
    ]
    if with_b:
        epgs.append({"name": "EG_web", "dn": B_WEB, "tenant": "TE_b", "ap": "AP_2"})
        rules.append(
            {
                "source_platform": "cisco_aci",
                "external_id": "uni/tn-TE_b/brc-CT_web/SJ_1",
                "name": "CT_web:SJ_1",
                "action": "allow",
                "sources": [],
                "destinations": [f"epg:{B_WEB}"],
                "services": ["tcp/8443"],
            }
        )
    return {"status": "completed", "epgs": epgs, "rules": rules}


def test_gleichnamige_epgs_mehrerer_aci_tenants_bleiben_getrennt(
    client, session_factory, monkeypatch
):
    from app.models import Epg, EpgSubnet, Rule

    tid, sid = _setup(session_factory, monkeypatch)
    s = session_factory()
    s.add(Epg(tenant_id=tid, name="EG_web", ap="AP_1"))  # Altbestand vor 0.3.1 (ohne DN)
    s.commit()
    s.close()

    _post(client, tid, sid, _multi())
    s = session_factory()
    epgs = {e.dn: e for e in s.query(Epg).all()}
    assert set(epgs) == {A_WEB, B_WEB, A_EXT}  # Altbestand ersetzt, nichts zusammengelegt
    assert epgs[A_WEB].aci_tenant == "TE_a" and epgs[B_WEB].aci_tenant == "TE_b"
    assert epgs[A_EXT].kind == "external" and epgs[A_EXT].l3out == "L3O_inet"
    assert all(e.source_system_id == sid for e in epgs.values())
    origins = {(x.epg_id, x.ip): x.origin for x in s.query(EpgSubnet).all()}
    assert origins == {(epgs[A_WEB].id, "10.1.0.1/24"): "bd", (epgs[A_EXT].id, "0.0.0.0/0"): "ext"}
    links = {r.external_id: {(e.role.value, e.epg_id) for e in r.endpoints} for r in s.query(Rule)}
    assert links["uni/tn-TE_a/brc-CT_web/SJ_1"] == {
        ("src", epgs[A_EXT].id),
        ("dst", epgs[A_WEB].id),
    }
    assert links["uni/tn-TE_b/brc-CT_web/SJ_1"] == {("dst", epgs[B_WEB].id)}
    s.close()

    _post(client, tid, sid, _multi(with_b=False))  # EPG in TE_b nicht mehr gemeldet
    s = session_factory()
    assert {e.dn for e in s.query(Epg).all()} == {A_WEB, A_EXT}
    s.close()

    assert client.delete(f"/systems/{sid}", headers={"X-Dev-Roles": "admin"}).status_code == 200
    s = session_factory()
    assert s.query(Epg).count() == 0 and s.query(EpgSubnet).count() == 0
    s.close()
