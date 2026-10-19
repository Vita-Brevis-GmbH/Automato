"""End-to-end (fixture-basiert): Collector-Ergebnis -> interne API -> Store -> /rules.

Beweist den M7-Durchstichpfad ohne Lab: der SoT-Adapter wird durch einen Fake
ersetzt (monkeypatch), Persistenz und Matching laufen echt gegen SQLite.
"""

from app.models import IpamType, Platform, SourceSystem, Tenant
from app.models.tenant import DnsType
from sot_gateway.models import NetworkObject, NetworkObjectType

TOKEN = {"X-Internal-Token": "dev-only-token"}


class _FakeSoT:
    name = "netbox"

    def search_by_ip(self, ip):
        if ip == "10.10.10.5":
            return [
                NetworkObject(
                    name="web01",
                    type=NetworkObjectType.HOST,
                    value="10.10.10.5",
                    tags=["zone-server"],
                    source="netbox",
                )
            ]
        return []

    def get_by_name(self, name):
        return None


def test_durchstich_collector_result_landet_in_rules(client, session_factory, monkeypatch):
    monkeypatch.setattr("app.api.internal.build_adapter", lambda tenant, settings: _FakeSoT())

    # Mandant + Quellsystem anlegen
    session = session_factory()
    tenant = Tenant(name="kunde-a", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    session.add(tenant)
    session.flush()
    fw = SourceSystem(
        tenant_id=tenant.id,
        name="fw-01",
        platform=Platform.FORTIGATE,
        base_url="https://fw",
        enabled=True,
    )
    session.add(fw)
    session.commit()
    tenant_id, ss_id = tenant.id, fw.id
    session.close()

    # Lauf anstossen (mit Ziel) -> Ergebnis liefern
    run_id = client.post(
        "/jobs/collect",
        json={"platform": "fortigate", "tenant_id": tenant_id, "source_system_id": ss_id},
    ).json()["run_id"]

    resp = client.post(
        f"/internal/collector-runs/{run_id}/result",
        headers=TOKEN,
        json={
            "status": "completed",
            "rules": [
                {
                    "source_platform": "fortigate",
                    "external_id": "1",
                    "name": "allow-web",
                    "action": "allow",
                    "sources": ["10.10.20.0/24"],
                    "destinations": ["10.10.10.5"],
                    "services": ["tcp/443"],
                    "dst_zone": "server",
                }
            ],
        },
    )
    assert resp.status_code == 204

    rules = client.get("/rules").json()
    assert len(rules) == 1
    assert rules[0]["name"] == "allow-web"
    assert rules[0]["tenant_id"] == tenant_id
    # Angereicherte Liste: Zonen + Zaehler
    assert rules[0]["dst_zone"] == "server"
    assert rules[0]["num_src"] == 1
    assert rules[0]["num_dst"] == 1
    assert rules[0]["num_services"] == 1
    assert rules[0]["num_unmatched"] == 1  # 10.10.20.0/24 nicht im Fake-SoT

    run = client.get(f"/jobs/{run_id}").json()
    assert run["status"] == "completed"
    assert run["rule_count"] == 1

    # Detail: aufgeloeste Endpunkte + Services
    detail = client.get(f"/rules/{rules[0]['id']}").json()
    assert detail["external_id"] == "1"
    assert detail["services"] == ["tcp/443"]
    by_role = {e["role"]: e for e in detail["endpoints"]}
    assert by_role["dst"]["value"] == "10.10.10.5"
    assert by_role["dst"]["match_status"] == "matched"
    assert by_role["dst"]["zone"] == "server"
    assert by_role["src"]["value"] == "10.10.20.0/24"
    assert by_role["src"]["match_status"] == "unmatched"

    # Findings: nicht aufgeloestes Objekt wird ausgewiesen
    findings = client.get("/findings").json()
    unmatched = [f for f in findings if f["type"] == "unmatched_object"]
    assert any(f["subject_id"] == "10.10.20.0/24" for f in unmatched)

    # /systems fuer den UI-Filter
    systems = client.get("/systems").json()
    assert any(s["name"] == "fw-01" and s["platform"] == "fortigate" for s in systems)

    # Freitext-/IP-Suche (q): nach Name und nach Endpunkt-Wert
    assert len(client.get("/rules", params={"q": "allow-web"}).json()) == 1
    assert len(client.get("/rules", params={"q": "10.10.10.5"}).json()) == 1  # Endpunkt-Wert
    assert client.get("/rules", params={"q": "gibtsnicht"}).json() == []


def test_rule_detail_404_fuer_unbekannte_regel(client):
    assert client.get("/rules/999999").status_code == 404


def test_applications_aus_vips(client, session_factory):
    session = session_factory()
    tenant = Tenant(name="k", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    session.add(tenant)
    session.flush()
    fw = SourceSystem(
        tenant_id=tenant.id,
        name="fw-01",
        platform=Platform.FORTIGATE,
        base_url="https://fw",
        enabled=True,
    )
    session.add(fw)
    session.commit()
    tid, sid = tenant.id, fw.id
    session.close()

    run_id = client.post(
        "/jobs/collect",
        json={"platform": "fortigate", "tenant_id": tid, "source_system_id": sid},
    ).json()["run_id"]
    resp = client.post(
        f"/internal/collector-runs/{run_id}/result",
        headers=TOKEN,
        json={
            "status": "completed",
            "rules": [
                {
                    "source_platform": "fortigate",
                    "external_id": "5",
                    "name": "allow-to-web",
                    "action": "allow",
                    "sources": ["10.10.20.0/24"],
                    "destinations": ["vip:VIP_web=203.0.113.10"],
                    "services": ["tcp/443"],
                }
            ],
            "nat_rules": [
                {
                    "source_platform": "fortigate",
                    "external_id": "VIP_web",
                    "name": "VIP_web",
                    "ext_ip": "203.0.113.10",
                    "ext_port": "443",
                    "mapped_ip": "10.10.10.5",
                    "mapped_port": "8443",
                    "protocol": "tcp",
                    "portforward": True,
                }
            ],
        },
    )
    assert resp.status_code == 204

    apps = client.get("/applications").json()
    assert any(a["name"] == "VIP_web" and a["rule_count"] == 1 for a in apps)

    detail = client.get("/applications/VIP_web").json()
    assert detail["mapped_ip"] == "10.10.10.5"
    assert detail["rules"][0]["services"] == ["tcp/443"]
    assert detail["rules"][0]["sources"] == ["10.10.20.0/24"]

    assert client.get("/applications/nicht-da").status_code == 404
