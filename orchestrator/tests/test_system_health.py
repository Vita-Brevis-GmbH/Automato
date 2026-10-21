"""Betriebszustand (Dev-Befund 0.12.0: "Monitoring, wo Dienste nicht laufen"): Ampel je
Pruefung fuer Plattform, Agenten, Quellsysteme, NetBox-Sync und Sicherung."""

from datetime import UTC, datetime, timedelta

from app.models import Agent, CollectorRun

ADMIN = {"X-Dev-Roles": "admin"}
TOKEN = {"X-Internal-Token": "dev-only-token"}


def _checks(client, tid, headers=None):
    r = client.get("/system/health", params={"tenant_id": tid}, headers=headers or {})
    assert r.status_code == 200, r.text
    body = r.json()
    return body, {c["key"]: c for c in body["checks"]}


def test_betriebszustand_ampel(client, session_factory):
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    agent = client.post("/agents", json={"name": "agent-a", "tenant_id": tid}, headers=ADMIN)
    agent_id = agent.json()["id"]

    def system(**body):
        return client.post("/systems", json={"tenant_id": tid, **body}, headers=ADMIN).json()["id"]

    fw = system(name="fw-01", platform="fortigate", base_url="https://fw", agent_id=agent_id)
    fw2 = system(name="fw-02", platform="fortigate", base_url="https://fw2")
    body, checks = _checks(client, tid)
    assert checks["database"]["status"] == "ok"
    # Agent mit Geraeten, nie gemeldet -> rot; Quellsysteme nie eingelesen -> gelb
    assert checks[f"agent:{agent_id}"]["status"] == "error"
    assert "meldet sich nicht" in checks[f"agent:{agent_id}"]["title"]
    assert checks[f"source:{fw}"]["title"] == "fw-01: noch nie eingelesen"
    assert body["status"] == "error" and body["problems"] >= 3

    # fw-01 fehlgeschlagen (mit Meldung), fw-02 eingelesen; Agent meldet Update-Problem
    run = {"platform": "fortigate", "tenant_id": tid, "source_system_id": fw}
    run_id = client.post("/jobs/collect", json=run).json()["run_id"]
    failed = {
        "status": "failed",
        "message": "FortiGate antwortet auf firewall/policy nicht mit JSON",
    }
    client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=failed)
    run2 = {"platform": "fortigate", "tenant_id": tid, "source_system_id": fw2}
    run_id2 = client.post("/jobs/collect", json=run2).json()["run_id"]
    client.post(f"/internal/collector-runs/{run_id2}/result", headers=TOKEN,
                json={"status": "completed"})  # fmt: skip
    s = session_factory()
    a = s.get(Agent, agent_id)
    a.last_seen = datetime.now(UTC)
    a.update_note = "Update 0.12.0 nicht installiert: Signatur fehlt"
    # zentraler Lauf wartet seit 20 min: Worker holt nicht ab
    queued = datetime.now(UTC) - timedelta(minutes=20)
    s.add(
        CollectorRun(
            platform="fortigate",
            tenant_id=tid,
            source_system_id=fw2,
            execution="inprocess",
            queued_at=queued,
        )
    )
    s.commit()
    s.close()
    _, checks = _checks(client, tid)
    assert checks[f"source:{fw}"]["status"] == "error"
    assert "nicht mit JSON" in checks[f"source:{fw}"]["detail"]
    assert checks[f"source:{fw2}"]["status"] == "ok"
    assert checks[f"agent:{agent_id}"]["status"] == "warn"
    assert "Signatur fehlt" in checks[f"agent:{agent_id}"]["detail"]
    assert checks["worker"]["status"] == "error" and "1 warten" in checks["worker"]["title"]

    # Viewer sieht nur seinen Mandanten
    other = client.post("/tenants", json={"name": "Kunde B"}, headers=ADMIN).json()["id"]
    viewer = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(other)}
    _, theirs = _checks(client, other, viewer)
    assert not [k for k in theirs if k.startswith("source:")]
    r = client.get("/system/health", params={"tenant_id": tid}, headers=viewer)
    assert r.status_code == 403
