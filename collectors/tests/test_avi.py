"""NSX ALB (Avi) lesend (ADR 0027): REST-Client gegen einen gespielten Controller und Auswertung
der Beispieldaten zu publizierten Diensten - ohne Lab."""

import httpx
import pytest
from sot_gateway.readonly import ReadOnlyViolation

from collectors.avi import tasks as avi_tasks
from collectors.avi.client import OBJECTS, AviClient, parse_scope
from collectors.avi.normalize import normalize, ref_name, ref_uuid
from collectors.common.dto import Target

AVI_PW = "pw"  # noqa: S105 - Testwert


def _controller(calls: list, *, login_status: int = 200, forbidden: tuple = ()) -> httpx.Client:
    sample = avi_tasks._sample()["tenants"]["admin"]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        tenant = request.headers.get("X-Avi-Tenant")
        calls.append((request.method, path, tenant, dict(request.url.params)))
        if path == "/api/initial-data":
            return httpx.Response(200, json={"version": {"Version": "22.1.6"}})
        if path == "/login":
            if login_status != 200:
                return httpx.Response(login_status, json={"error": "invalid"})
            cookies = [("set-cookie", "sessionid=s1; Path=/"),
                       ("set-cookie", "csrftoken=c1; Path=/")]  # fmt: skip
            return httpx.Response(200, headers=cookies, json={"user": {"username": "ro"}})
        if path == "/logout":
            assert request.headers.get("X-CSRFToken") == "c1"
            return httpx.Response(200, json={})
        assert "sessionid=s1" in (request.headers.get("cookie") or "")
        assert request.headers.get("X-Avi-Version") == "22.1.6"
        kind = path.removeprefix("/api/")
        if kind in forbidden:
            return httpx.Response(403, json={"error": "no access"})
        if kind == "tenant":
            names = [{"name": "admin"}, {"name": "kunde-a"}]
            return httpx.Response(200, json={"count": 2, "results": names})
        rows = sample.get(kind, []) if tenant == "admin" else []
        page = int(request.url.params.get("page", "1"))
        if kind == "pool":  # zweite Seite: Seitenweise lesen
            body = {"count": len(rows), "results": rows[:1] if page == 1 else rows[1:]}
            return httpx.Response(200, json={**body, **({"next": "p2"} if page == 1 else {})})
        return httpx.Response(200, json={"count": len(rows), "results": rows})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_scope_und_verweise():
    assert parse_scope(" admin, ,kunde-a ") == ["admin", "kunde-a"] and parse_scope("") == []
    ref = "https://avi.example.ch/api/pool/pool-web#pool-web"
    assert ref_uuid(ref) == "pool-web" and ref_name(ref) == "pool-web"
    assert ref_uuid(None) is None and ref_name("https://x/api/pool/p1") is None


def test_alle_tenants_seitenweise_nur_get_und_abmelden():
    calls: list = []
    with AviClient("https://avi", "ro", AVI_PW, client=_controller(calls)) as avi:
        data = avi.gather()
    assert set(data["tenants"]) == {"admin", "kunde-a"} and data["missing"] == []
    assert len(data["tenants"]["admin"]["pool"]) == 3  # zwei Seiten zusammen
    methods = {(m, p) for m, p, _, _ in calls if m != "GET"}
    assert methods == {("POST", "/login"), ("POST", "/logout")}
    read = {p.removeprefix("/api/") for m, p, t, _ in calls if m == "GET" and t == "kunde-a"}
    assert read == {*OBJECTS, "virtualservice-inventory"}  # nur die Positivliste
    assert calls[-1][1] == "/logout"


def test_gewaehlter_tenant_teilfehler_und_schreibschutz():
    calls: list = []
    client = _controller(calls, forbidden=("poolgroup",))
    with AviClient("https://avi", "ro", AVI_PW, client=client) as avi:
        data = avi.gather("admin")
        with pytest.raises(ReadOnlyViolation):
            client.put("https://avi/api/pool/pool-web", json={})
    assert set(data["tenants"]) == {"admin"}
    assert data["missing"] == ["admin: poolgroup (HTTP 403: no access)"]  # mit Begruendung
    assert not any(p == "/api/tenant" for _, p, _, _ in calls)


def test_login_abgelehnt():
    with pytest.raises(RuntimeError, match="Login abgelehnt"):
        AviClient("https://avi", "ro", AVI_PW, client=_controller([], login_status=401)).login()


def test_publizierte_dienste_aus_den_beispieldaten():
    rows = normalize(avi_tasks._sample())
    by = {(r.name, r.ext_port): r for r in rows}
    assert set(by) == {("vs-web", "80"), ("vs-web", "443"), ("vs-app", "8443")}
    web = by[("vs-web", "443")]
    assert (web.source_platform, web.vip_type, web.ext_ip, web.family) == (
        "avi", "server-load-balance", "10.20.0.50", "v4")  # fmt: skip
    assert [(t["ip"], t["port"], t["status"]) for t in web.targets] == [
        ("10.20.0.5", 8080, "active"), ("10.20.0.6", 8081, "disable")]  # fmt: skip
    assert (web.mapped_ip, web.mapped_port) == ("10.20.0.5", "8080")
    assert web.attrs["ssl"] and web.attrs["fqdn"] == ["web.example.ch"]
    assert web.attrs["snat"] == "service_engine" and web.attrs["oper_status"] == "OPER_UP"
    assert web.attrs["health_monitors"] == ["System-HTTP"] and web.attrs["tenant"] == "admin"
    assert "ssl" not in by[("vs-web", "80")].attrs
    app = by[("vs-app", "8443")]
    # Pool Group: hoechste Prioritaet zuerst; L4-Profil erhaelt die Client-IP
    assert [t["pool"] for t in app.targets] == ["pool-app-a", "pool-app-b"]
    assert app.mapped_ip == "10.20.0.7" and app.attrs["snat"] == "client"
    assert app.attrs["pool_group"] == "pg-app" and app.attrs["oper_status"] == "OPER_DOWN"
    assert len({r.external_id for r in rows}) == len(rows)


def test_sni_child_erbt_vip_und_ipv6():
    data = {"tenants": {"t": {
        "virtualservice": [
            {"uuid": "p", "name": "parent", "type": "VS_TYPE_VH_PARENT",
             "vsvip_ref": "https://a/api/vsvip/v#v", "services": [{"port": 443}]},
            {"uuid": "c", "name": "child", "type": "VS_TYPE_VH_CHILD",
             "vh_parent_vs_ref": "https://a/api/virtualservice/p#parent",
             "vh_domain_name": ["app.example.ch"], "pool_ref": "https://a/api/pool/x#x"},
        ],
        "vsvip": [{"uuid": "v", "vip": [{"ip_address": {"addr": "10.1.1.1"},
                                         "ip6_address": {"addr": "2001:db8::1"},
                                         "floating_ip": {"addr": "198.51.100.10"}}]}],
        "pool": [{"uuid": "x", "name": "x", "default_server_port": 80,
                  "servers": [{"ip": {"addr": "10.1.2.3"}}]}],
    }}}  # fmt: skip
    rows = normalize(data)
    child = [r for r in rows if r.name == "child"]
    assert {r.ext_ip for r in child} == {"10.1.1.1", "2001:db8::1", "198.51.100.10"}
    assert all(r.ext_port == "443" and r.attrs["parent"] == "parent" for r in child)
    assert {r.family for r in child} == {"v4", "v6"}
    assert next(r for r in child if r.ext_ip == "198.51.100.10").attrs["floating_ip"]
    assert child[0].attrs["domains"] == ["app.example.ch"] and child[0].mapped_ip == "10.1.2.3"


def test_lauf_und_pruefung(monkeypatch):
    sent = {}

    def submit(run_id, result, via_agent=False):
        sent["result"] = result
        return {"status": result.status}

    monkeypatch.setattr(avi_tasks, "submit_result", submit)
    monkeypatch.setattr(avi_tasks, "confirmed", lambda result, response: result)
    result = avi_tasks.collect_once("run-1")  # zentral ohne Zugangsdaten: Beispieldaten
    assert result.status == "completed" and len(result.nat_rules) == 3
    ok, text = avi_tasks.check(Target(name="avi", platform="avi"))
    assert not ok and "keine Zugangsdaten" in text


def _rejecting(calls: list, *, version_ok: bool) -> httpx.Client:
    """Controller, der jede Abfrage mit X-Avi-Version ablehnt (HTTP 400 mit Begruendung) - bzw.
    auch ohne (`version_ok=False`: der Grund ist ein anderer)."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append((path, request.headers.get("X-Avi-Version"),
                      request.headers.get("X-Avi-Tenant")))  # fmt: skip
        if path == "/api/initial-data":
            return httpx.Response(200, json={"version": {"Version": "31.1.1"}})
        if path in ("/login", "/logout"):
            return httpx.Response(200, headers=[("set-cookie", "csrftoken=c1; Path=/")], json={})
        if request.headers.get("X-Avi-Version") or not version_ok:
            return httpx.Response(400, json={"error": "Ungueltige Anfrage (Test)"})
        if path == "/api/tenant":
            return httpx.Response(200, json={"results": [{"name": "kunde-a"}]})
        return httpx.Response(200, json={"count": 0, "results": []})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_http_400_version_ohne_header_und_begruendung():
    """Befund 0.78.2 (HTTP 400 auf jede Abfrage): lehnt der Controller die API-Version ab, liest
    der Client ohne `X-Avi-Version` weiter; sonst steht die Begruendung in der Meldung."""
    calls: list = []
    with AviClient("https://avi", "ro", AVI_PW, client=_rejecting(calls, version_ok=True)) as avi:
        data = avi.gather()
    assert data["missing"] == [] and set(data["tenants"]) == {"kunde-a"}
    assert avi.version == "31.1.1 (ohne X-Avi-Version)"
    # einmal mit, dann nur noch ohne Version
    assert [v for p, v, _ in calls if p.startswith("/api/") and p != "/api/initial-data"][:3] == [
        "31.1.1", None, None]  # fmt: skip

    calls = []
    with AviClient("https://avi", "ro", AVI_PW, client=_rejecting(calls, version_ok=False)) as avi:
        data = avi.gather()
    assert data["tenants"] == {"admin": data["tenants"]["admin"]}  # Tenant-Liste nicht lesbar
    assert data["missing"][0] == (
        "Standard-Tenant: virtualservice (HTTP 400: Ungueltige Anfrage (Test))"
    )


def test_pruefung_mit_gewaehltem_tenant_und_begruendung(monkeypatch):
    calls: list = []
    client = _rejecting(calls, version_ok=False)
    monkeypatch.setattr(avi_tasks, "AviClient", lambda *a, **k: AviClient(*a, **k, client=client))
    target = Target(source_system_id=1, name="lb-01", platform="avi", base_url="https://avi",
                    username="ro", password=AVI_PW, read_scope="kunde-a")  # fmt: skip
    ok, text = avi_tasks.check(target)
    assert not ok and "HTTP 400" in text and "Geraet meldet: Ungueltige Anfrage (Test)" in text
    assert ("/api/virtualservice", None, "kunde-a") in calls  # erster gewaehlter Tenant


def test_version_erst_nach_der_anmeldung():
    """`/api/initial-data` ohne Anmeldung gesperrt: die Version kommt nach dem Login (ein
    Versuch je Seite, keine Wiederholungen)."""
    calls: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        path, logged_in = request.url.path, "csrftoken=c1" in (request.headers.get("cookie") or "")
        calls.append(path)
        if path == "/api/initial-data":
            if not logged_in:
                return httpx.Response(401, json={"error": "Authentication credentials missing"})
            return httpx.Response(200, json={"version": {"Version": "30.2.1"}})
        if path in ("/login", "/logout"):
            return httpx.Response(200, headers=[("set-cookie", "csrftoken=c1; Path=/")], json={})
        assert request.headers.get("X-Avi-Version") == "30.2.1"
        return httpx.Response(200, json={"count": 0, "results": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with AviClient("https://avi", "ro", AVI_PW, client=client) as avi:
        assert avi.ping() == 0 and avi.version == "30.2.1"
    assert calls[:3] == ["/api/initial-data", "/login", "/api/initial-data"]
