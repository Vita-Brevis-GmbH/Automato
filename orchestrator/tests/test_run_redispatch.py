"""Wartende Laeufe (0.109.1, Befund des Anwenders: der NetBox-Lauf bleibt auf „wartet“): Redis
haelt die Warteschlange ohne Datentraeger - nach einem Neustart sind Auftraege weg. Der Zeitplan
schickt wartende zentrale Laeufe erneut, gelesen wird trotzdem nur einmal (Claim beim Ziel);
die Liste nennt, wer abholen muss, die Verbindungspruefung den zentralen Worker."""

from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.models import CollectorRun
from app.store import connections, runs

ADMIN = {"X-Dev-Roles": "admin"}
INTERNAL = {"X-Internal-Token": "dev-only-token"}


def _netbox(client, agent_id: int | None = None) -> tuple[int, int]:
    tid = client.post("/tenants", json={"name": "Umgebung W"}, headers=ADMIN).json()["id"]
    body = {"tenant_id": tid, "name": "Dev NetBox", "platform": "netbox",
            "base_url": "http://host.docker.internal:8080", "token": "lesen",
            "agent_id": agent_id}  # fmt: skip
    r = client.post("/systems", json=body, headers=ADMIN)
    assert r.status_code == 201, r.text
    return tid, r.json()["id"]


def _collect(client, tid: int, sid: int) -> str:
    body = {"platform": "netbox", "tenant_id": tid, "source_system_id": sid}
    r = client.post("/jobs/collect", json=body, headers=ADMIN)
    assert r.status_code == 202, r.text
    return r.json()["run_id"]


def _age(session_factory, run_id: str, minutes: int) -> None:
    s = session_factory()
    run = s.get(CollectorRun, run_id)
    run.queued_at = datetime.now(UTC) - timedelta(minutes=minutes)
    s.commit()
    s.close()


def test_wartender_lauf_wird_erneut_geschickt_und_nur_einmal_gelesen(client, session_factory):
    tid, sid = _netbox(client)
    run_id = _collect(client, tid, sid)
    sent: list[tuple[str, str]] = []
    dispatch = lambda platform, rid: sent.append((platform, rid))  # noqa: E731

    s = session_factory()
    assert runs.redispatch_waiting(s, dispatch) == []  # eben erst geschickt
    s.close()
    _age(session_factory, run_id, 6)
    s = session_factory()
    assert runs.redispatch_waiting(s, dispatch) == [run_id]
    assert runs.redispatch_waiting(s, dispatch) == []  # hoechstens einmal je Frist
    later = datetime.now(UTC) + timedelta(minutes=6)
    assert runs.redispatch_waiting(s, dispatch, now=later) == [run_id]
    s.close()
    assert sent == [("netbox", run_id), ("netbox", run_id)]

    # die Liste nennt, wer abholen muss
    (row,) = (r for r in client.get("/jobs", params={"tenant_id": tid}, headers=ADMIN).json()
              if r["id"] == run_id)  # fmt: skip
    assert row["status"] == "queued"
    assert row["message"].startswith("wartet auf den zentralen Worker – erneut geschickt")

    # der erste Auftrag liest, ein zweiter (erneut geschickter) nicht
    first = client.get(f"/internal/collector-runs/{run_id}/target",
                       headers={**INTERNAL, "X-Run-Claim": "task-1"})  # fmt: skip
    assert first.status_code == 200 and first.json()["target"]["token"] == "lesen"
    again = client.get(f"/internal/collector-runs/{run_id}/target",
                       headers={**INTERNAL, "X-Run-Claim": "task-1"})  # fmt: skip
    assert again.json()["target"] is not None  # derselbe Auftrag fragt erneut
    second = client.get(f"/internal/collector-runs/{run_id}/target",
                        headers={**INTERNAL, "X-Run-Claim": "task-2"})  # fmt: skip
    assert second.json() == {"target": None, "skip": True}
    s = session_factory()
    assert s.get(CollectorRun, run_id).status == "running"
    assert runs.redispatch_waiting(s, dispatch, now=later + timedelta(hours=1)) == []  # laeuft
    s.close()
    # Worker vor 0.109.1 (ohne Claim): wie bisher
    old = client.get(f"/internal/collector-runs/{run_id}/target", headers=INTERNAL)
    assert old.json()["target"] is not None


def test_aus_und_agent_laeufe_nicht(client, session_factory, monkeypatch):
    agent = client.post("/agents", json={"name": "ag-w"}, headers=ADMIN).json()
    tid, sid = _netbox(client, agent["id"])
    run_id = _collect(client, tid, sid)
    _age(session_factory, run_id, 60)
    s = session_factory()
    assert runs.redispatch_waiting(s, lambda *a: None) == []  # liest der Agent
    s.close()
    (row,) = (r for r in client.get("/jobs", params={"tenant_id": tid}, headers=ADMIN).json()
              if r["id"] == run_id)  # fmt: skip
    assert row["message"] == "wartet auf Agent „ag-w“ (noch nie gesehen)"
    monkeypatch.setattr(get_settings(), "run_redispatch_minutes", 0)
    s = session_factory()
    assert runs.redispatch_waiting(s, lambda *a: None) == []
    s.close()


def test_verbindungen_zeigen_den_zentralen_worker(client, session_factory, monkeypatch):
    tid, sid = _netbox(client)
    ok = connections.Item("redis", "Plattform", "Redis", "redis:6379", "ok", [])
    monkeypatch.setattr(connections, "_redis", lambda url, cafile=None: ok)
    monkeypatch.setattr(connections.db_roles, "describe", lambda session: None)
    monkeypatch.setattr(connections.reachability, "check",
                        lambda *a, **k: connections.reachability.Result(True, []))  # fmt: skip
    monkeypatch.setattr(connections, "_queue_depth", lambda url, cafile: 0)

    def worker():
        out = client.get("/system/connections", headers=ADMIN).json()
        return next(i for i in out["items"] if i["key"] == "worker")

    w = worker()
    assert w["status"] == "ok" and w["steps"][0] == {"step": "ping", "ok": True,
                                                     "detail": "celery@test"}  # fmt: skip
    # Auftraege verloren: Lauf wartet, die Warteschlange ist leer
    run_id = _collect(client, tid, sid)
    _age(session_factory, run_id, 30)
    w = worker()
    assert w["status"] == "warn" and "Redis neu gestartet" in w["hint"]
    assert any(s["step"] == "läufe" and not s["ok"] for s in w["steps"])
    # Worker antwortet nicht: Fehler mit dem Befehl zum Starten
    monkeypatch.setattr(connections, "_worker_ping", lambda settings: [])
    w = worker()
    assert w["status"] == "error" and w["steps"][0]["detail"] == "kein Worker antwortet"
    assert "docker compose --profile collectors up -d collector" in w["hint"]
    # liest alles ein Agent und wartet nichts: nur ein Hinweis
    s = session_factory()
    s.get(CollectorRun, run_id).status = "failed"
    s.commit()
    s.close()
    agent = client.post("/agents", json={"name": "ag-x", "tenant_id": tid}, headers=ADMIN).json()
    client.patch(f"/systems/{sid}", json={"agent_id": agent["id"]}, headers=ADMIN)
    w = worker()
    assert w["status"] == "info" and "nicht gebraucht" in w["hint"]


def test_warteschlange_aus_redis():
    import socket
    import threading

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    seen: list[bytes] = []

    def serve():
        conn, _ = server.accept()
        seen.append(conn.recv(64))
        conn.sendall(b":3\r\n")
        conn.close()

    t = threading.Thread(target=serve)
    t.start()
    assert connections._queue_depth(f"redis://127.0.0.1:{port}/0", None) == 3
    t.join(timeout=5)
    server.close()
    assert seen == [b"LLEN collectors\r\n"]
    assert connections._queue_depth(f"redis://127.0.0.1:{port}/0", None) is None  # zu
