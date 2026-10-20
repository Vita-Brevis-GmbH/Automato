"""Selbst-Update der Collector-Agenten ueber den Agent-Kanal (ADR 0011)."""

import hashlib
import io
import zipfile

from app.config import Settings, get_settings
from app.models import AuditLog

ADMIN = {"X-Dev-Roles": "admin"}


def _package(tmp_path, wheel=b"wheel-v1"):
    (tmp_path / "collectors-0.1.0-py3-none-any.whl").write_bytes(wheel)
    (tmp_path / "sot_gateway-0.1.0-py3-none-any.whl").write_bytes(b"sot")
    (tmp_path / "run-agent.ps1").write_text("# wrapper")
    (tmp_path / "install.ps1").write_text("# install")
    (tmp_path / "agent.env").write_text("AUTOMATO_AGENT_TOKEN=geheim")  # nie ausliefern


def _agent(client) -> tuple[int, dict]:
    a = client.post("/agents", json={"name": "agent-win"}, headers=ADMIN).json()
    return a["id"], {"X-Agent-Token": a["token"]}


def test_manifest_dateien_und_version(client, tmp_path, session_factory):
    _package(tmp_path)
    client.app.dependency_overrides[get_settings] = lambda: Settings(
        agent_package_dir=str(tmp_path)
    )
    try:
        agent_id, headers = _agent(client)
        info = client.get("/internal/agent/update", headers=headers).json()
        assert info["enabled"] and info["release"] == "0.1.0" and len(info["build"]) == 8
        assert info["version"] == f"0.1.0+{info['build']}"
        names = {f["name"]: f for f in info["files"]}
        assert set(names) == {
            "collectors-0.1.0-py3-none-any.whl",
            "sot_gateway-0.1.0-py3-none-any.whl",
            "run-agent.ps1",
        }
        wheel = client.get(
            "/internal/agent/update/files/collectors-0.1.0-py3-none-any.whl", headers=headers
        )
        assert (
            hashlib.sha256(wheel.content).hexdigest()
            == names[wheel.url.path.rsplit("/")[-1]]["sha256"]
        )
        # nur Dateien aus dem Manifest, keine Secrets/Pfade
        for bad in ("agent.env", "install.ps1", "..%2Fagent.env"):
            r = client.get(f"/internal/agent/update/files/{bad}", headers=headers)
            assert r.status_code == 404, bad
        assert client.get("/internal/agent/update").status_code == 422  # ohne Token
        assert (
            client.get("/internal/agent/update", headers={"X-Agent-Token": "x"}).status_code == 401
        )

        # Agent meldet seine Version -> UI zeigt "Update verfuegbar"
        client.get("/internal/agent/targets", headers={**headers, "X-Agent-Version": "alt"})
        (agent,) = client.get("/agents", headers=ADMIN).json()
        assert agent["version"] == "alt" and agent["update_available"] is True
        client.get(
            "/internal/agent/targets", headers={**headers, "X-Agent-Version": info["version"]}
        )
        assert client.get("/agents", headers=ADMIN).json()[0]["update_available"] is False

        # neues Paket -> neue Version
        _package(tmp_path, wheel=b"wheel-v2")
        assert (
            client.get("/internal/agent/update", headers=headers).json()["version"]
            != (info["version"])
        )

        # Paket-ZIP enthaelt die Version fuer install.ps1
        z = client.get("/agents/package", headers=ADMIN)
        names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
        assert "automato-collector-agent/agent-version.txt" in names

        s = session_factory()
        actions = [a.action for a in s.query(AuditLog).all()]
        s.close()
        assert "agent.update_downloaded" in actions
    finally:
        del client.app.dependency_overrides[get_settings]


def test_update_je_agent_abschaltbar(client, tmp_path):
    _package(tmp_path)
    client.app.dependency_overrides[get_settings] = lambda: Settings(
        agent_package_dir=str(tmp_path)
    )
    try:
        agent_id, headers = _agent(client)
        r = client.patch(f"/agents/{agent_id}", json={"auto_update": False}, headers=ADMIN)
        assert r.status_code == 200 and r.json()["auto_update"] is False
        assert r.json()["enabled"] is True  # unveraendert
        info = client.get("/internal/agent/update", headers=headers).json()
        assert info["enabled"] is False and info["files"] == []
        r = client.get(
            "/internal/agent/update/files/collectors-0.1.0-py3-none-any.whl", headers=headers
        )
        assert r.status_code == 403
        operator = {"X-Dev-Roles": "operator"}
        r = client.patch(f"/agents/{agent_id}", json={"auto_update": True}, headers=operator)
        assert r.status_code == 403
    finally:
        del client.app.dependency_overrides[get_settings]


def test_ohne_agent_paket_kein_update(client, tmp_path):
    client.app.dependency_overrides[get_settings] = lambda: Settings(
        agent_package_dir=str(tmp_path / "fehlt")
    )
    try:
        _, headers = _agent(client)
        info = client.get("/internal/agent/update", headers=headers).json()
        assert info["version"] is None and info["enabled"] is False and info["files"] == []
    finally:
        del client.app.dependency_overrides[get_settings]
