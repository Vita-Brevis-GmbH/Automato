"""Erreichbarkeit zentral gelesener Quellsysteme prüfen (Dev-Befund 0.33.0: NetBox meldete
nach drei Minuten nur „ConnectTimeout“)."""

import socket

import httpx
from app.store import reachability

ADMIN = {"X-Dev-Roles": "admin"}


def _dns(monkeypatch, addrs=("172.17.0.1",), error=None):
    def fake(host, port, type=0):
        if error:
            raise error
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addrs]

    monkeypatch.setattr(reachability.socket, "getaddrinfo", fake)


def _tcp(monkeypatch, exc=None):
    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(addr, timeout):
        if exc:
            raise exc
        return Conn()

    monkeypatch.setattr(reachability.socket, "create_connection", fake)


def test_schritte_name_tcp_und_netbox_status(monkeypatch):
    url = "http://host.docker.internal:8080"
    _dns(monkeypatch, error=socket.gaierror(-2, "Name or service not known"))
    r = reachability.check(url, "netbox", token="t")
    assert not r.ok and r.steps[0].step == "dns" and "extra_hosts" in r.hint

    _dns(monkeypatch)
    _tcp(monkeypatch, TimeoutError())
    r = reachability.check(url, "netbox", token="t")
    assert [s.step for s in r.steps] == ["dns", "tcp"]
    assert "keine Antwort nach 5 s" in r.steps[1].detail and "Docker-Netz" in r.hint

    _tcp(monkeypatch, ConnectionRefusedError())
    r = reachability.check(url, "netbox", token="t")
    assert "abgelehnt" in r.steps[1].detail and "NETBOX_PORT" in r.hint

    _tcp(monkeypatch)
    seen = {}

    def status(request):
        seen["auth"] = request.headers.get("authorization")
        if request.headers.get("authorization") != "Token t":
            return httpx.Response(403, json={"detail": "Invalid token"})
        return httpx.Response(200, json={"netbox-version": "4.6.10"})

    ok = reachability.check(url, "netbox", token="t", transport=httpx.MockTransport(status))
    assert ok.ok and ok.steps[-1].detail.startswith("/api/status/: NetBox 4.6.10")
    bad = reachability.check(url, "netbox", token="x", transport=httpx.MockTransport(status))
    assert not bad.ok and "Lese-Token" in bad.hint
    # andere Plattformen: Name und TCP genügen
    fw = reachability.check("https://fw.example.ch", "fortigate")
    assert fw.ok and [s.step for s in fw.steps] == ["dns", "tcp"] and "443" in fw.steps[1].detail


def test_api_prueft_nur_zentral_gelesene_systeme(client, monkeypatch):
    tid = client.post("/tenants", json={"name": "Umgebung P"}, headers=ADMIN).json()["id"]
    body = {"tenant_id": tid, "name": "nb", "platform": "netbox",
            "base_url": "http://host.docker.internal:8080", "token": "geheim-lesen"}  # fmt: skip
    sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
    _dns(monkeypatch)
    _tcp(monkeypatch, TimeoutError())
    r = client.post(f"/systems/{sid}/check", headers=ADMIN)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] is False and out["from"] == "orchestrator" and out["steps"][1]["ok"] is False
    assert "geheim-lesen" not in r.text  # Token nie in der Antwort
    viewer = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid)}
    assert client.post(f"/systems/{sid}/check", headers=viewer).status_code == 403
    agent = client.post("/agents", json={"tenant_id": tid, "name": "ag"}, headers=ADMIN).json()
    client.patch(f"/systems/{sid}", json={"agent_id": agent["id"]}, headers=ADMIN)
    assert client.post(f"/systems/{sid}/check", headers=ADMIN).status_code == 409
    assert client.post("/systems/99999/check", headers=ADMIN).status_code == 404
