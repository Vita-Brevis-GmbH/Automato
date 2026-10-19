"""Audit-Log E1.4: Allowlist (keine Secrets im Log), Principal+Aktion, optionale
At-Rest-Verschlüsselung. Macht „wer hat was getan" lückenlos (SECURITY.md #5)."""

from app import audit
from app.audit import allowlist, crypto
from app.config import Settings
from app.models import AuditLog


def test_allowlist_filtert_unbekannte_keys():
    safe = allowlist.sanitize(
        "collector_run.queued",
        {"platform": "fortigate", "tenant_id": 1, "source_system_id": 2, "token": "secret"},
    )
    assert safe == {
        "platform": "fortigate",
        "tenant_id": 1,
        "source_system_id": 2,
        "_dropped": ["token"],  # nur der Name, nie der Wert
    }


def test_allowlist_unbekannte_aktion_verwirft_alles():
    # Fail-closed: ohne Eintrag bleibt kein inhaltlicher Key erhalten.
    assert allowlist.sanitize("mystery.action", {"password": "x"}) == {"_dropped": ["password"]}


def test_allowlist_leeres_detail_ist_none():
    assert allowlist.sanitize("collector_run.queued", None) is None
    assert allowlist.sanitize("collector_run.queued", {}) is None


def test_record_schreibt_nie_secret_werte(session_factory):
    s = session_factory()
    audit.record(
        s,
        actor="user:alice",
        action="collector_run.queued",
        subject="r1",
        detail={"platform": "fortigate", "psksecret": "TOP", "token": "abc"},
    )
    s.commit()
    row = s.query(AuditLog).one()
    assert row.actor == "user:alice"
    assert row.action == "collector_run.queued"
    payload = audit.decode_detail(row)
    assert "psksecret" not in payload and "token" not in payload
    assert payload["_dropped"] == ["psksecret", "token"]
    # kein Secret-Wert taucht irgendwo in der gespeicherten Zeile auf
    assert "TOP" not in str(row.detail) and "abc" not in str(row.detail)


def test_verschluesselung_roundtrip(session_factory, monkeypatch):
    monkeypatch.setattr(crypto, "get_settings", lambda: Settings(audit_encryption_key="pw"))
    s = session_factory()
    audit.record(
        s,
        actor="user:bob",
        action="dictionary_entry.deleted",
        subject="7",
        detail={"entry_id": 7},
    )
    s.commit()
    row = s.query(AuditLog).one()
    # at-rest nur der Chiffretext, kein Klartext-Feld
    assert set(row.detail) == {"_enc"}
    assert "entry_id" not in str(row.detail)
    assert audit.decode_detail(row) == {"entry_id": 7}


def test_naming_profile_schreiben_wird_auditiert(session_factory, client):
    admin = {"X-Dev-Roles": "admin"}
    r = client.post("/naming-profiles", json={"customer": "ACME", "name": "Std"}, headers=admin)
    assert r.status_code == 201
    pid = r.json()["id"]

    r2 = client.post(
        f"/naming-profiles/{pid}/dictionary",
        json={"kind": "object_prefix", "key": "H_", "value": "host"},
        headers=admin,
    )
    assert r2.status_code == 200
    entry_id = r2.json()["id"]

    dr = client.delete(f"/naming-profiles/dictionary/{entry_id}", headers=admin)
    assert dr.status_code == 204

    s = session_factory()
    rows = s.query(AuditLog).order_by(AuditLog.id).all()
    actions = [r.action for r in rows]
    assert actions == [
        "naming_profile.created",
        "dictionary_entry.upserted",
        "dictionary_entry.deleted",
    ]
    assert all(r.actor == "user:dev" for r in rows)  # X-Dev-Roles -> subject "dev"
    created = audit.decode_detail(rows[0])
    assert created == {"profile_id": pid, "customer": "ACME", "name": "Std", "copied_from": None}
