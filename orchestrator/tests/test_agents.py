"""Collector-Agenten (ADR 0008): Registrierung (admin) + Pull-Queue (Agent-Token)."""

from app.models import IpamType, Platform, SourceSystem, Tenant
from app.models.tenant import DnsType


def _seed(session) -> tuple[int, int]:
    t = Tenant(name="kunde-a", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    session.add(t)
    session.flush()
    fw = SourceSystem(
        tenant_id=t.id, name="fw", platform=Platform.FORTIGATE, base_url="https://fw", enabled=True
    )
    session.add(fw)
    session.flush()
    return t.id, fw.id


def _external_run(client, tid, sid) -> str:
    op = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(tid)}
    body = {
        "platform": "fortigate",
        "tenant_id": tid,
        "source_system_id": sid,
        "execution": "external",
    }
    return client.post("/jobs/collect", json=body, headers=op).json()["run_id"]


def test_create_agent_nur_admin(session_factory, client):
    seed = session_factory()
    tid, _ = _seed(seed)
    seed.commit()
    body = {"name": "agent-win-01", "tenant_id": tid}
    assert client.post("/agents", json=body, headers={"X-Dev-Roles": "operator"}).status_code == 403
    r = client.post("/agents", json=body, headers={"X-Dev-Roles": "admin"})
    assert r.status_code == 201
    assert r.json()["name"] == "agent-win-01" and len(r.json()["token"]) > 20


def test_create_agent_doppelt_leer_oder_fremder_mandant(session_factory, client):
    seed = session_factory()
    tid, _ = _seed(seed)
    seed.commit()
    admin = {"X-Dev-Roles": "admin"}
    body = {"name": "agent-win-01", "tenant_id": tid}
    assert client.post("/agents", json=body, headers=admin).status_code == 201
    # gleicher Name -> 409 (vorher 500 durch den Unique-Constraint)
    r = client.post("/agents", json={"name": " agent-win-01 ", "tenant_id": tid}, headers=admin)
    assert r.status_code == 409
    assert client.post("/agents", json={"name": "  "}, headers=admin).status_code == 422
    r = client.post("/agents", json={"name": "a-x", "tenant_id": 9999}, headers=admin)
    assert r.status_code == 422


def test_next_run_claimt_externen_lauf(session_factory, client):
    seed = session_factory()
    tid, sid = _seed(seed)
    seed.commit()
    token = client.post(
        "/agents", json={"name": "a1", "tenant_id": tid}, headers={"X-Dev-Roles": "admin"}
    ).json()["token"]
    run_id = _external_run(client, tid, sid)

    h = {"X-Agent-Token": token}
    r = client.get("/internal/agent/next-run", headers=h)
    assert r.status_code == 200
    assert r.json()["run"]["run_id"] == run_id and r.json()["run"]["platform"] == "fortigate"
    # erneut -> bereits zugeteilt (RUNNING), nichts mehr offen
    assert client.get("/internal/agent/next-run", headers=h).json()["run"] is None


def test_next_run_ignoriert_inprocess(session_factory, client):
    seed = session_factory()
    tid, sid = _seed(seed)
    seed.commit()
    token = client.post(
        "/agents", json={"name": "a2", "tenant_id": tid}, headers={"X-Dev-Roles": "admin"}
    ).json()["token"]
    # inprocess-Lauf (Default) -> darf NICHT an den Agenten gehen
    op = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(tid)}
    client.post(
        "/jobs/collect",
        json={"platform": "fortigate", "tenant_id": tid, "source_system_id": sid},
        headers=op,
    )
    assert (
        client.get("/internal/agent/next-run", headers={"X-Agent-Token": token}).json()["run"]
        is None
    )


def test_invalid_agent_token_401(client):
    assert (
        client.get("/internal/agent/next-run", headers={"X-Agent-Token": "nope"}).status_code == 401
    )
    assert (
        client.post("/internal/agent/heartbeat", headers={"X-Agent-Token": "nope"}).status_code
        == 401
    )


def test_heartbeat_ok(session_factory, client):
    token = client.post("/agents", json={"name": "a3"}, headers={"X-Dev-Roles": "admin"}).json()[
        "token"
    ]
    assert (
        client.post("/internal/agent/heartbeat", headers={"X-Agent-Token": token}).status_code
        == 204
    )


def test_disable_agent_sperrt_token_und_zeigt_status(session_factory, client):
    seed = session_factory()
    tid, _ = _seed(seed)
    seed.commit()
    admin = {"X-Dev-Roles": "admin"}
    created = client.post("/agents", json={"name": "a4", "tenant_id": tid}, headers=admin).json()
    aid, token = created["id"], created["token"]

    # deaktivieren (nur admin)
    assert (
        client.patch(
            f"/agents/{aid}", json={"enabled": False}, headers={"X-Dev-Roles": "operator"}
        ).status_code
        == 403
    )
    r = client.patch(f"/agents/{aid}", json={"enabled": False}, headers=admin)
    assert r.status_code == 200 and r.json()["enabled"] is False

    # deaktivierter Agent authentisiert nicht mehr
    assert (
        client.get("/internal/agent/next-run", headers={"X-Agent-Token": token}).status_code == 401
    )

    # Liste zeigt Status + last_seen-Feld
    row = next(a for a in client.get("/agents", headers=admin).json() if a["id"] == aid)
    assert row["enabled"] is False and "last_seen" in row


def test_patch_unbekannter_agent_404(client):
    assert (
        client.patch(
            "/agents/999", json={"enabled": True}, headers={"X-Dev-Roles": "admin"}
        ).status_code
        == 404
    )


def test_agent_paket_download_nur_admin(client, tmp_path):
    import io
    import zipfile

    from app.config import Settings, get_settings

    (tmp_path / "collectors-0.1.0-py3-none-any.whl").write_bytes(b"wheel")
    (tmp_path / "install.ps1").write_text("# install")
    (tmp_path / "run-agent.ps1").write_text("# run")
    (tmp_path / "agent.env").write_text("AUTOMATO_AGENT_TOKEN=geheim")  # darf nie mit
    client.app.dependency_overrides[get_settings] = lambda: Settings(
        agent_package_dir=str(tmp_path)
    )

    assert client.get("/agents/package", headers={"X-Dev-Roles": "operator"}).status_code == 403
    r = client.get("/agents/package", headers={"X-Dev-Roles": "admin"})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "automato-collector-agent/collectors-0.1.0-py3-none-any.whl" in names
    assert "automato-collector-agent/install.ps1" in names
    assert not any(n.endswith("agent.env") for n in names)  # keine Secrets im Paket

    client.app.dependency_overrides[get_settings] = lambda: Settings(
        agent_package_dir=str(tmp_path / "fehlt")
    )
    assert client.get("/agents/package", headers={"X-Dev-Roles": "admin"}).status_code == 404
    del client.app.dependency_overrides[get_settings]


def test_agent_paket_mit_ca_bei_tls_intern(client, tmp_path):
    """0.78.2 (Befund: Agent auf http:// nach TLS intern): mit TLS intern liegt die CA (nur das
    oeffentliche Zertifikat) im Paket und als Download; die Einrichtung erfaehrt es ueber info."""
    import io
    import zipfile

    from app.config import Settings, get_settings

    (tmp_path / "collectors-0.1.0-py3-none-any.whl").write_bytes(b"wheel")
    tls_dir = tmp_path / "tls"
    tls_dir.mkdir()
    (tls_dir / "ca.crt").write_text("-----BEGIN CERTIFICATE-----\nCA\n-----END CERTIFICATE-----\n")
    (tls_dir / "server.key").write_text("geheim")  # darf nie mit
    admin = {"X-Dev-Roles": "admin"}

    def settings(**extra):
        client.app.dependency_overrides[get_settings] = lambda: Settings(
            agent_package_dir=str(tmp_path), internal_tls_dir=str(tls_dir), **extra
        )

    settings()  # ohne TLS intern: keine CA
    assert client.get("/agents/package/info", headers=admin).json() == {
        "internal_tls": False, "ca": False, "ca_name": "automato-ca.crt"}  # fmt: skip
    assert client.get("/agents/package/ca", headers=admin).status_code == 404
    names = zipfile.ZipFile(io.BytesIO(client.get("/agents/package", headers=admin).content))
    assert not any(n.endswith(".crt") for n in names.namelist())

    settings(internal_tls=True)
    assert (
        client.get("/agents/package/info", headers={"X-Dev-Roles": "operator"}).status_code == 403
    )
    assert client.get("/agents/package/info", headers=admin).json()["ca"] is True
    r = client.get("/agents/package/ca", headers=admin)
    assert r.status_code == 200 and r.text.startswith("-----BEGIN CERTIFICATE-----")
    assert 'filename="automato-ca.crt"' in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(client.get("/agents/package", headers=admin).content))
    assert z.read("automato-collector-agent/automato-ca.crt").startswith(b"-----BEGIN")
    assert not any(n.endswith(".key") for n in z.namelist())  # nur das Zertifikat
    del client.app.dependency_overrides[get_settings]
