"""Verbindung prüfen für Geräte hinter einem Agenten (1.7.0).

Anwender 2026-11-11: „Wenn ich den internen Worker verwende, kann ich die Verbindung prüfen, beim
Collector-Agent nicht, können wir dies mit einbringen?“ - „Prüfen“ legt einen Auftrag an, der
Agent bekommt ihn beim nächsten Abruf (`next-run`) samt Zugangsdaten, prüft read-only und meldet
Name, Verbindung und Anmeldung zurück; ohne Antwort verfällt der Auftrag sichtbar."""

from datetime import UTC, datetime, timedelta

from app.models import AgentCheck, AuditLog
from sqlalchemy import select

ADMIN = {"X-Dev-Roles": "admin"}
GEHEIM = "fgt-lese-token-123"  # Testwert - nie in Antworten an die Oberfläche


def _setup(client):
    tid = client.post("/tenants", json={"name": "Umgebung C"}, headers=ADMIN).json()["id"]
    agent = client.post("/agents", json={"tenant_id": tid, "name": "ag-c"}, headers=ADMIN).json()
    body = {"tenant_id": tid, "name": "fw-c", "platform": "fortigate", "token": GEHEIM,
            "base_url": "https://fw-c.example.ch", "agent_id": agent["id"]}  # fmt: skip
    sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
    return tid, sid, agent


def test_pruefauftrag_ueber_den_agenten(client, session_factory):
    tid, sid, agent = _setup(client)
    h = {"X-Agent-Token": agent["token"]}

    r = client.post(f"/systems/{sid}/check", headers=ADMIN)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["from"] == "agent" and out["agent"] == "ag-c" and out["status"] == "queued"
    assert out["ok"] is False and out["steps"] == [] and GEHEIM not in r.text
    cid = out["check_id"]
    # doppelt klicken prüft nicht doppelt
    assert client.post(f"/systems/{sid}/check", headers=ADMIN).json()["check_id"] == cid

    # Agent holt den Auftrag mit dem nächsten Abruf - samt Zugangsdaten, nur einmal
    nxt = client.get("/internal/agent/next-run", headers=h).json()
    assert nxt["run"] is None
    assert [c["check_id"] for c in nxt["checks"]] == [cid]
    target = nxt["checks"][0]["target"]
    assert target["token"] == GEHEIM and target["base_url"] == "https://fw-c.example.ch"
    assert client.get("/internal/agent/next-run", headers=h).json()["checks"] == []
    running = client.get(f"/systems/{sid}/check/{cid}", headers=ADMIN).json()
    assert running["status"] == "running"

    # fremder Agent darf das Ergebnis nicht setzen
    other = client.post("/agents", json={"tenant_id": tid, "name": "ag-x"}, headers=ADMIN).json()
    result = {"ok": False, "hint": "Anmeldung abgelehnt",
              "steps": [{"step": "dns", "ok": True, "detail": "fw-c.example.ch → 10.0.5.1"},
                        {"step": "tcp", "ok": True, "detail": "10.0.5.1:443 offen (3 ms)"},
                        {"step": "login", "ok": False, "detail": "HTTP 401"},
                        {"step": "fremd", "ok": True, "detail": "wird verworfen"}]}  # fmt: skip
    bad = client.post(f"/internal/agent/checks/{cid}", json=result,
                      headers={"X-Agent-Token": other["token"]})  # fmt: skip
    assert bad.status_code == 404
    done = client.post(f"/internal/agent/checks/{cid}", json=result, headers=h)
    assert done.status_code == 204, done.text
    # zweimal abliefern geht nicht
    assert client.post(f"/internal/agent/checks/{cid}", json=result, headers=h).status_code == 404

    final = client.get(f"/systems/{sid}/check/{cid}", headers=ADMIN).json()
    assert final["status"] == "done" and final["ok"] is False
    assert [s["step"] for s in final["steps"]] == ["dns", "tcp", "login"]
    assert final["hint"] == "Anmeldung abgelehnt"

    with session_factory() as s:
        entry = s.scalars(select(AuditLog).where(AuditLog.action == "agent_check.claimed")).one()
        assert entry.detail["check_id"] == cid and GEHEIM not in str(entry.detail)

    # nach dem Ergebnis neuer Auftrag möglich
    again = client.post(f"/systems/{sid}/check", headers=ADMIN).json()
    assert again["check_id"] != cid and again["status"] == "queued"


def test_ohne_antwort_verfaellt_der_auftrag(client, session_factory):
    _tid, sid, _agent = _setup(client)
    cid = client.post(f"/systems/{sid}/check", headers=ADMIN).json()["check_id"]
    with session_factory() as s:
        check = s.get(AgentCheck, cid)
        check.requested_at = datetime.now(UTC) - timedelta(minutes=10)
        s.commit()
    out = client.get(f"/systems/{sid}/check/{cid}", headers=ADMIN).json()
    assert out["status"] == "expired" and out["ok"] is False
    assert "ag-c" in out["hint"] and "hat sich nicht gemeldet" in out["hint"]
    # ein verfallener Auftrag wird nicht mehr abgeholt, ein neuer Klick legt einen neuen an
    assert client.post(f"/systems/{sid}/check", headers=ADMIN).json()["check_id"] != cid


def test_rechte_deaktivierter_agent_und_loeschen(client, session_factory):
    tid, sid, agent = _setup(client)
    viewer = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid)}
    assert client.post(f"/systems/{sid}/check", headers=viewer).status_code == 403
    cid = client.post(f"/systems/{sid}/check", headers=ADMIN).json()["check_id"]
    assert client.get(f"/systems/{sid}/check/{cid}", headers=viewer).status_code == 403
    assert client.get(f"/systems/{sid}/check/999999", headers=ADMIN).status_code == 404

    client.patch(f"/agents/{agent['id']}", json={"enabled": False}, headers=ADMIN)
    r = client.post(f"/systems/{sid}/check", headers=ADMIN)
    assert r.status_code == 409 and "deaktiviert" in r.text

    assert client.delete(f"/systems/{sid}", headers=ADMIN).status_code == 200
    with session_factory() as s:
        assert s.scalars(select(AgentCheck)).all() == []
