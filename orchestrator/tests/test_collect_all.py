"""Alle Quellsysteme eines Mandanten auf einmal einlesen (Dev-Befund: nicht jedes einzeln)."""

from test_reference_sources import ADMIN, _agent, _ddi, _system, _tenant


def test_alle_einlesen(client):
    tid = _tenant(client)
    agent_id, _ = _agent(client, tid)
    ddi = _ddi(client, tid, agent_id=agent_id)
    fw = _system(client, tid, name="fw-01", platform="fortigate", base_url="https://fw")
    nb = _system(client, tid, name="nb", platform="netbox", base_url="http://nb")  # ohne Token
    off = _system(client, tid, name="fw-alt", platform="fortigate", base_url="https://alt")
    client.patch(f"/systems/{off['id']}", json={"enabled": False}, headers=ADMIN)

    # viewer darf nicht, fremder Mandant auch nicht
    viewer = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid)}
    assert (
        client.post("/jobs/collect-all", json={"tenant_id": tid}, headers=viewer).status_code == 403
    )
    other = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(tid + 1000)}
    assert (
        client.post("/jobs/collect-all", json={"tenant_id": tid}, headers=other).status_code == 403
    )

    operator = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(tid)}
    r = client.post("/jobs/collect-all", json={"tenant_id": tid}, headers=operator)
    assert r.status_code == 202, r.text
    body = r.json()
    items = {i["name"]: i for i in body["items"]}
    assert list(items) == ["ddi-01", "nb", "fw-01"]  # Referenzquellen zuerst, deaktivierte nicht
    assert items["ddi-01"]["execution"] == "external"  # Agent zugewiesen
    assert items["fw-01"]["run_id"] and items["fw-01"]["execution"] == "inprocess"
    assert items["nb"]["run_id"] is None and "read token" in items["nb"]["skipped"]
    assert body["queued"] == 2
    assert (
        items["fw-01"]["source_system_id"] == fw["id"]
        and items["nb"]["source_system_id"] == nb["id"]
    )
    assert items["ddi-01"]["source_system_id"] == ddi["id"]

    # zweiter Klick: wartende Laeufe nicht doppelt anlegen; der Grund sagt, worauf sie warten
    again = client.post("/jobs/collect-all", json={"tenant_id": tid}, headers=operator).json()
    assert again["queued"] == 0
    skipped = {i["name"]: i for i in again["items"]}
    assert skipped["ddi-01"]["skipped"].startswith("Lauf wartet seit ")
    assert skipped["ddi-01"]["skipped"].endswith("auf Agent „agent-a“")
    assert skipped["fw-01"]["skipped"].endswith("auf den zentralen Worker")
    assert skipped["ddi-01"]["blocking_run_id"] == items["ddi-01"]["run_id"]


def _set(session_factory, run_id, **values):
    from app.models import CollectorRun

    s = session_factory()
    run = s.get(CollectorRun, run_id)
    for k, v in values.items():
        setattr(run, k, v)
    s.commit()
    s.close()


def test_haengender_lauf_blockiert_nicht_mehr(client, session_factory):
    """Dev-Befund 0.11.3: ACI und DDI wurden bei "Alle einlesen" uebersprungen, obwohl nichts
    lief - ein abgeholter Lauf ohne Ergebnis (Agent beendet) blieb fuer immer "laeuft"."""
    from datetime import UTC, datetime, timedelta

    tid = _tenant(client)
    agent_id, token = _agent(client, tid)
    ddi = _ddi(client, tid, agent_id=agent_id)
    operator = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(tid)}
    first = client.post("/jobs/collect-all", json={"tenant_id": tid}, headers=operator).json()
    run_id = first["items"][0]["run_id"]
    claimed = client.get("/internal/agent/next-run", headers={"X-Agent-Token": token}).json()
    assert claimed["run"]["run_id"] == run_id
    run = client.get(f"/jobs/{run_id}").json()
    assert run["status"] == "running" and run["started_at"]
    item = client.post("/jobs/collect-all", json={"tenant_id": tid}, headers=operator).json()
    assert item["items"][0]["skipped"].startswith("Lauf läuft seit ")
    assert item["items"][0]["skipped"].endswith("(Agent „agent-a“)")

    # Agent beendet, kein Ergebnis: nach der Frist (Standard 120 min) zaehlt der Lauf nicht mehr
    _set(session_factory, run_id, started_at=datetime.now(UTC) - timedelta(minutes=121))
    again = client.post("/jobs/collect-all", json={"tenant_id": tid}, headers=operator).json()
    assert again["queued"] == 1 and again["items"][0]["source_system_id"] == ddi["id"]
    old = client.get(f"/jobs/{run_id}").json()
    assert old["status"] == "failed" and "kein Ergebnis nach 120 min" in old["message"]

    # nie abgeholt (Agent offline): nach 24 h ebenfalls beendet
    waiting = again["items"][0]["run_id"]
    _set(session_factory, waiting, queued_at=datetime.now(UTC) - timedelta(hours=25))
    listed = {r["id"]: r for r in client.get("/jobs", params={"tenant_id": tid}).json()}
    assert listed[waiting]["status"] == "failed" and "nicht abgeholt" in listed[waiting]["message"]


def test_lauf_abbrechen_und_spaetes_ergebnis(client):
    tid = _tenant(client)
    fw = _system(client, tid, name="fw-01", platform="fortigate", base_url="https://fw")
    body = {"platform": "fortigate", "tenant_id": tid, "source_system_id": fw["id"]}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]

    viewer = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid)}
    assert client.post(f"/jobs/{run_id}/cancel", headers=viewer).status_code == 403
    operator = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(tid)}
    r = client.post(f"/jobs/{run_id}/cancel", headers=operator)
    assert r.status_code == 200 and r.json()["status"] == "failed"
    assert r.json()["message"].startswith("abgebrochen von ")
    assert client.post(f"/jobs/{run_id}/cancel", headers=operator).status_code == 409
    assert client.post("/jobs/missing/cancel", headers=operator).status_code == 404

    # liefert der Collector doch noch ab, wird das Ergebnis eingespielt
    rule = {
        "source_platform": "fortigate",
        "external_id": "1",
        "name": "late",
        "action": "allow",
        "sources": ["10.0.0.1"],
        "destinations": ["10.0.0.2"],
        "services": ["tcp/22"],
    }
    ack = client.post(
        f"/internal/collector-runs/{run_id}/result",
        headers={"X-Internal-Token": "dev-only-token"},
        json={"status": "completed", "rules": [rule]},
    ).json()
    assert ack["status"] == "completed"
    assert [x["name"] for x in client.get("/rules", params={"tenant_id": tid}).json()] == ["late"]


def test_alle_laeufe_seitenweise_mit_filter(client):
    """Dev-Befund 0.13.0: die Übersicht zeigte nur die letzten Läufe - offene Läufe dahinter
    waren unsichtbar. Jetzt seitenweise mit Gesamtzahl und Filter."""
    tid = _tenant(client)
    fws = [
        _system(client, tid, name=f"fw-{i:02d}", platform="fortigate", base_url="https://fw")
        for i in range(12)
    ]
    ids = []
    for fw in fws:
        body = {"platform": "fortigate", "tenant_id": tid, "source_system_id": fw["id"]}
        ids.append(client.post("/jobs/collect", json=body).json()["run_id"])
    done = {"status": "completed"}
    for run_id in ids[:3]:
        client.post(
            f"/internal/collector-runs/{run_id}/result",
            headers={"X-Internal-Token": "dev-only-token"},
            json=done,
        )
    r = client.get("/jobs", params={"tenant_id": tid, "limit": 5})
    assert r.headers["X-Total-Count"] == "12" and len(r.json()) == 5
    page2 = client.get("/jobs", params={"tenant_id": tid, "limit": 5, "offset": 10}).json()
    assert len(page2) == 2
    open_runs = client.get("/jobs", params={"tenant_id": tid, "status": "open", "limit": 100})
    assert open_runs.headers["X-Total-Count"] == "9"
    assert {r["status"] for r in open_runs.json()} == {"queued"}
    one = client.get("/jobs", params={"tenant_id": tid, "source_system_id": fws[0]["id"]}).json()
    assert [r["id"] for r in one] == [ids[0]] and one[0]["status"] == "completed"
    assert client.get("/jobs", params={"tenant_id": tid, "trigger": "schedule"}).json() == []
