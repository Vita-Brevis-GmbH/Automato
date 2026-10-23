"""Verbindungen der Plattform pruefen (0.40.0, Dev-Befund 0.39.0: ufw sperrte den Weg vom
Container zu NetBox auf dem Docker-Host) - System-Seite, CLI und deploy/aufbau.sh."""

import json
import socket
import threading

import test_reachability as tr
from app import cli
from app.store import connections

ADMIN = {"X-Dev-Roles": "admin"}
# /proc/net/route eines Containers im Netz 172.20.0.0/16 (Gateway 172.20.0.1)
ROUTES = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
    "eth0\t00000000\t010014AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
    "eth0\t000014AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"
)


def _routes(monkeypatch, tmp_path):
    path = tmp_path / "route"
    path.write_text(ROUTES)
    monkeypatch.setattr(connections, "ROUTES", path)


def _redis_ok(monkeypatch):
    item = connections.Item("redis", "Plattform", "Redis", "redis:6379", "ok", [])
    monkeypatch.setattr(connections, "_redis", lambda url: item)


def _setup(client) -> tuple[int, int]:
    tid = client.post("/tenants", json={"name": "Umgebung P"}, headers=ADMIN).json()["id"]
    body = {"tenant_id": tid, "name": "nb", "platform": "netbox",
            "base_url": "http://host.docker.internal:8080", "token": "geheim-lesen"}  # fmt: skip
    sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
    return tid, sid


def test_netz_des_containers_aus_der_routing_tabelle(monkeypatch, tmp_path):
    _routes(monkeypatch, tmp_path)
    assert str(connections.own_network()) == "172.20.0.0/16"
    assert connections.own_gateway() == "172.20.0.1"
    monkeypatch.setattr(connections, "ROUTES", tmp_path / "fehlt")
    assert connections.own_network() is None and connections.own_gateway() is None


def test_zeitueberschreitung_zum_docker_host_nennt_die_ufw_regel(client, monkeypatch, tmp_path):
    tid, _sid = _setup(client)
    _routes(monkeypatch, tmp_path)
    _redis_ok(monkeypatch)
    tr._dns(monkeypatch)  # host.docker.internal -> 172.17.0.1
    tr._tcp(monkeypatch, TimeoutError())
    # ein Agent-System erscheint nur als Hinweis
    agent = client.post("/agents", json={"tenant_id": tid, "name": "ag-01"}, headers=ADMIN).json()
    body = {"tenant_id": tid, "name": "ddi", "platform": "infoblox", "base_url": "https://ddi",
            "agent_id": agent["id"]}  # fmt: skip
    assert client.post("/systems", json=body, headers=ADMIN).status_code == 201

    r = client.get("/system/connections", headers=ADMIN)
    assert r.status_code == 200, r.text
    out = r.json()
    assert "geheim-lesen" not in r.text  # nie ein Token in der Antwort
    assert out["status"] == "error" and out["container"]["network"] == "172.20.0.0/16"
    items = {i["key"].split(":")[0]: i for i in out["items"]}
    assert items["database"]["status"] == "ok"
    nb = items["source"]
    assert (nb["name"], nb["status"]) == ("nb (netbox)", "error")
    assert nb["ufw"] == {
        "from": "172.20.0.0/16", "to": "172.17.0.1", "port": 8080, "comment": "Automato -> nb",
        "command": "sudo ufw allow proto tcp from 172.20.0.0/16 to 172.17.0.1 port 8080 "
                   "comment 'Automato -> nb'",
    }  # fmt: skip
    assert "ufw" in nb["hint"] and "deploy/aufbau.sh firewall --anwenden" in nb["hint"]
    ag = items["agent"]
    assert (ag["status"], ag["name"]) == ("info", "Agent „ag-01“")
    assert "run-agent.ps1 -Check" in ag["hint"] and "ddi" in ag["hint"]

    # nur Admins; der Mandant schränkt ein
    viewer = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid)}
    assert client.get("/system/connections", headers=viewer).status_code == 403
    other = client.get("/system/connections", params={"tenant_id": tid + 1}, headers=ADMIN)
    assert [i["key"] for i in other.json()["items"]] == ["database", "redis"]


def test_erreichbar_netbox_mit_schreib_token(client, monkeypatch, tmp_path):
    _setup(client)
    _routes(monkeypatch, tmp_path)
    _redis_ok(monkeypatch)
    ok = connections.reachability.Result(True, [connections.reachability.Step("http", True, "x")])
    monkeypatch.setattr(connections.reachability, "check", lambda *a, **k: ok)
    out = client.get("/system/connections", headers=ADMIN).json()
    (nb,) = (i for i in out["items"] if i["key"].startswith("source:"))
    assert nb["status"] == "ok" and nb["ufw"] is None
    sync = nb["steps"][-1]
    assert (sync["step"], sync["ok"]) == ("sync", False)
    assert sync["detail"] == "kein Schreib-Token – NetBox-Sync nicht möglich"
    assert out["status"] == "ok" and out["problems"] == 0


def test_redis_ping():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def answer():
        conn, _ = server.accept()
        conn.recv(64)
        conn.sendall(b"+PONG\r\n")
        conn.close()

    threading.Thread(target=answer, daemon=True).start()
    item = connections._redis(f"redis://127.0.0.1:{port}/0")
    server.close()
    assert item.status == "ok" and [s["step"] for s in item.steps] == ["tcp", "ping"]
    # niemand hört zu
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    free = closed.getsockname()[1]
    closed.close()
    down = connections._redis(f"redis://127.0.0.1:{free}/0")
    assert down.status == "error" and "zentrale Worker" in down.hint


def test_cli_json_und_exit_code(client, monkeypatch, tmp_path, capsys):
    """`automato check-connections --json` für deploy/aufbau.sh: Exit 1 bei einem Fehler."""
    _routes(monkeypatch, tmp_path)
    result = {"status": "error", "items": [{"key": "redis", "status": "error", "name": "Redis",
              "target": "redis:6379", "steps": [], "hint": "h", "ufw": None}]}  # fmt: skip
    monkeypatch.setattr(connections, "run", lambda session, settings: result)
    assert cli.main(["check-connections", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"
    result["status"] = "ok"
    result["items"][0]["status"] = "ok"
    assert cli.main(["check-connections"]) == 0
    assert capsys.readouterr().out.startswith("OK     Redis  redis:6379")
