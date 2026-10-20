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

    # zweiter Klick: wartende Laeufe nicht doppelt anlegen
    again = client.post("/jobs/collect-all", json={"tenant_id": tid}, headers=operator).json()
    assert again["queued"] == 0
    assert {i["skipped"] for i in again["items"] if i["name"] != "nb"} == {
        "Lauf läuft bzw. wartet bereits"
    }
