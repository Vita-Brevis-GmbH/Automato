"""Der Job-Kreislauf des Grundaufbaus: anstossen -> Queue -> Ergebnis -> Audit."""

RESULT_PAYLOAD = {
    "status": "completed",
    "rules": [
        {
            "source_platform": "fortigate",
            "external_id": "42",
            "name": "allow-web",
            "action": "allow",
            "sources": ["10.1.2.0/24"],
            "destinations": ["10.9.9.10"],
            "services": ["tcp/443"],
            "src_zone": "dmz",
            "dst_zone": "internal",
        }
    ],
}


def test_collect_job_wird_angelegt_und_dispatcht(client):
    response = client.post("/jobs/collect", json={"platform": "fortigate"})
    assert response.status_code == 202
    run_id = response.json()["run_id"]

    assert client.dispatched == [("fortigate", run_id)]
    status = client.get(f"/jobs/{run_id}").json()
    assert status["status"] == "queued"


def test_collector_ergebnis_schliesst_run_ab(client):
    run_id = client.post("/jobs/collect", json={"platform": "fortigate"}).json()["run_id"]

    response = client.post(
        f"/internal/collector-runs/{run_id}/result",
        json=RESULT_PAYLOAD,
        headers={"X-Internal-Token": "dev-only-token"},
    )
    assert response.status_code == 204

    status = client.get(f"/jobs/{run_id}").json()
    assert status["status"] == "completed"
    assert status["rule_count"] == 1


def test_internal_api_verlangt_token(client):
    run_id = client.post("/jobs/collect", json={"platform": "cisco_aci"}).json()["run_id"]
    response = client.post(
        f"/internal/collector-runs/{run_id}/result",
        json=RESULT_PAYLOAD,
        headers={"X-Internal-Token": "falsch"},
    )
    assert response.status_code == 401


def test_unbekannte_plattform_wird_abgelehnt(client):
    response = client.post("/jobs/collect", json={"platform": "checkpoint"})
    assert response.status_code == 422
