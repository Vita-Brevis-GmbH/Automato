"""System-Seite: Betriebsstand fuer Admins (keine Secrets)."""

import json

from app.config import Settings, get_settings
from test_agent_update import _package
from test_reference_sources import ADMIN, _system, _tenant


def test_system_status(client, tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    _package(pkg)
    backup = tmp_path / "last-backup.json"
    backup.write_text(
        json.dumps(
            {"at": "2026-10-20T02:00:00+00:00", "file": "a.dump.age", "encrypted": True, "size": 42}
        )
    )
    settings = Settings(
        agent_package_dir=str(pkg),
        agent_signature_file=str(tmp_path / "none.sig"),
        backup_status_file=str(backup),
        scheduler_enabled=True,
        audit_encryption_key="x" * 44,
        credentials_key="geheim",
    )
    client.app.dependency_overrides[get_settings] = lambda: settings
    try:
        tid = _tenant(client)
        _system(
            client,
            tid,
            name="fw",
            platform="fortigate",
            base_url="https://fw",
            schedule="0 2 * * *",
        )
        s = client.get("/system/status", headers=ADMIN).json()
        assert s["database"]["head"] and s["database"]["revision"] is None  # Tests ohne Alembic
        assert (
            s["agent_package"]["signature"] == "unsigned" and not s["agent_package"]["pubkey_set"]
        )
        assert s["agent_package"]["version"].startswith("0.1.0+")
        assert s["scheduler"]["enabled"] and s["scheduler"]["scheduled_sources"] == 1
        assert s["scheduler"]["next_run_at"] is not None
        sec = s["security"]
        assert sec["audit_encryption"] and sec["credentials_key"] == "setting"
        assert "geheim" not in json.dumps(s)  # nie Schluessel ausgeben
        assert s["backup"]["encrypted"] and s["backup"]["file"] == "a.dump.age"
        viewer = {"X-Dev-Roles": "operator"}
        assert client.get("/system/status", headers=viewer).status_code == 403
    finally:
        client.app.dependency_overrides.pop(get_settings, None)
