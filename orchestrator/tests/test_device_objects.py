"""Firewall-Objekte mit Namen, Regel-Verweise und Abgleich-Findings (ADR 0010)."""

from app.models import Finding, FindingStatus, IpamType, Platform, SourceSystem, Tenant
from app.models.tenant import DnsType
from sot_gateway.models import NetworkObject, NetworkObjectType

TOKEN = {"X-Internal-Token": "dev-only-token"}


class _SoT:
    name = "netbox"

    def search_by_ip(self, ip):
        if ip == "10.10.10.5":
            return [
                NetworkObject(name="web01", type=NetworkObjectType.HOST, value=ip, source="netbox")
            ]
        return []

    def get_by_name(self, name):
        return None


def _setup(session_factory, monkeypatch, names=("fw-01",)) -> tuple[int, list[int]]:
    monkeypatch.setattr("app.api.internal.build_adapter", lambda tenant, settings: _SoT())
    s = session_factory()
    t = Tenant(name="kunde-a", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    s.add(t)
    s.flush()
    systems = [
        SourceSystem(tenant_id=t.id, name=n, platform=Platform.FORTIGATE, base_url="")
        for n in names
    ]
    s.add_all(systems)
    s.commit()
    ids = t.id, [x.id for x in systems]
    s.close()
    return ids


OBJECTS = [
    {"name": "N_dmz-net", "category": "address", "obj_type": "ipmask", "values": ["10.10.20.0/24"]},
    {
        "name": "H_web01",
        "category": "address",
        "obj_type": "ipmask",
        "values": ["10.10.10.5"],
        "comment": "Webserver",
    },
    {
        "name": "N_server-net",
        "category": "address",
        "obj_type": "ipmask",
        "values": ["10.10.10.0/24"],
    },
    {
        "name": "G_web",
        "category": "address_group",
        "members": ["H_web01", "N_server-net"],
        "values": ["10.10.10.5", "10.10.10.0/24"],
    },
    {"name": "H_web01-alt", "category": "address", "obj_type": "ipmask", "values": ["10.10.10.5"]},
    {"name": "H_netz", "category": "address", "obj_type": "ipmask", "values": ["10.10.30.0/24"]},
    {"name": "webserver", "category": "address", "obj_type": "ipmask", "values": ["10.10.40.7"]},
    {"name": "HTTPS", "category": "service", "values": ["tcp/443"]},
    {"name": "SG_web", "category": "service_group", "members": ["HTTPS"], "values": ["tcp/443"]},
]
RULE = {
    "source_platform": "fortigate",
    "external_id": "4",
    "name": "dmz-to-web",
    "action": "allow",
    "sources": ["10.10.20.0/24"],
    "destinations": ["10.10.10.5", "10.10.10.0/24"],
    "services": ["tcp/443"],
    "source_refs": ["N_dmz-net"],
    "destination_refs": ["G_web"],
    "service_refs": ["SG_web"],
}


def _deliver(client, tid: int, sid: int, objects=OBJECTS, rules=(RULE,)) -> None:
    body = {"platform": "fortigate", "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    result = {"status": "completed", "rules": list(rules), "objects": objects}
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text


def test_regel_zeigt_firewall_namen_und_gruppen(client, session_factory, monkeypatch):
    tid, (sid,) = _setup(session_factory, monkeypatch)
    _deliver(client, tid, sid)

    (rule,) = client.get(f"/rules?tenant_id={tid}").json()
    assert rule["sources"] == ["N_dmz-net"] and rule["destinations"] == ["G_web"]
    assert rule["service_names"] == ["SG_web"]

    detail = client.get(f"/rules/{rule['id']}").json()
    refs = {r["name"]: r for r in detail["refs"]}
    grp = refs["G_web"]
    assert grp["role"] == "dst" and grp["category"] == "address_group"
    assert [m["name"] for m in grp["members"]] == ["H_web01", "N_server-net"]
    web = grp["members"][0]
    assert web["comment"] == "Webserver"
    assert web["values"][0] == {
        "value": "10.10.10.5",
        "match_status": "matched",
        "zone": None,
        "source_of_record": "netbox",
        "reference": None,
        "enrichment": None,
    }
    assert grp["match_status"] == "unmatched"  # N_server-net ist nicht im IPAM
    assert refs["SG_web"]["members"][0]["values"] == [
        {
            "value": "tcp/443",
            "match_status": None,
            "zone": None,
            "source_of_record": None,
            "reference": None,
            "enrichment": None,
        }
    ]
    # Suche findet die Regel ueber den Gruppennamen
    assert len(client.get(f"/rules?tenant_id={tid}&q=g_web").json()) == 1


def test_abgleich_findings_aus_firewall_objekten(client, session_factory, monkeypatch):
    tid, (sid,) = _setup(session_factory, monkeypatch)
    _deliver(client, tid, sid)
    findings = client.get(f"/findings?tenant_id={tid}").json()
    by_type: dict[str, set] = {}
    for f in findings:
        by_type.setdefault(f["type"], set()).add(f["subject_id"])
    assert by_type["duplicate_value"] == {"10.10.10.5"}  # H_web01 + H_web01-alt
    assert by_type["name_type_mismatch"] == {"H_netz"}  # H_ (Host) mit Netz-Wert
    assert by_type["invalid_name"] == {"webserver"}  # kein Konventions-Praefix
    assert {"H_web01-alt", "H_netz", "webserver"} <= by_type["unused_object"]
    assert "H_web01" not in by_type["unused_object"]  # Mitglied einer genutzten Gruppe
    assert "HTTPS" not in by_type["unused_object"]
    assert by_type["unmatched_object"] == {"10.10.20.0/24", "10.10.10.0/24"}


def test_triage_bleibt_und_quellen_ueberschreiben_sich_nicht(client, session_factory, monkeypatch):
    tid, (sid_a, sid_b) = _setup(session_factory, monkeypatch, names=("fw-01", "fw-02"))
    _deliver(client, tid, sid_a)
    s = session_factory()
    f = s.query(Finding).filter_by(subject_id="H_netz", type="name_type_mismatch").one()
    f.status = FindingStatus.ACKNOWLEDGED
    f.note = "bekannt"
    s.commit()
    s.close()

    # Lauf einer zweiten Firewall loescht die Befunde der ersten nicht
    _deliver(
        client,
        tid,
        sid_b,
        objects=[OBJECTS[0]],
        rules=[{**RULE, "destination_refs": [], "service_refs": []}],
    )
    _deliver(client, tid, sid_a)  # erneuter Lauf: Triage bleibt erhalten
    s = session_factory()
    f = s.query(Finding).filter_by(subject_id="H_netz", type="name_type_mismatch").one()
    assert f.status is FindingStatus.ACKNOWLEDGED and f.note == "bekannt"
    assert f.source_system_id == sid_a
    s.close()


def test_quellsystem_loeschen_entfernt_seine_befunde(client, session_factory, monkeypatch):
    tid, (sid,) = _setup(session_factory, monkeypatch)
    _deliver(client, tid, sid)
    assert client.get(f"/findings?tenant_id={tid}").json()
    r = client.delete(f"/systems/{sid}", headers={"X-Dev-Roles": "admin"})
    assert r.status_code == 200 and r.json()["findings"] > 0
    assert client.get(f"/findings?tenant_id={tid}").json() == []
