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
    fw = SourceSystem(
        name="fw-01", platform=Platform.FORTIGATE, base_url="https://fw", enabled=True
    )
    session.add_all([tenant, fw])
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

    run = client.get(f"/jobs/{run_id}").json()
    assert run["status"] == "completed"
    assert run["rule_count"] == 1
