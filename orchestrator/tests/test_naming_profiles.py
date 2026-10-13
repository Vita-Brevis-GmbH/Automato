"""Namensprofile & Wörterbücher (ADR 0004-Ergänzung): Store + API."""

from app.config import get_settings
from app.store import naming_profiles as store


def test_seed_idempotent(session_factory):
    session = session_factory()
    p1 = store.seed_sop_profile(session)
    session.commit()
    n1 = len(store.list_entries(session, p1.id))

    # zweiter Lauf: gleiches Profil, keine Duplikate
    p2 = store.seed_sop_profile(session)
    session.commit()
    assert p2.id == p1.id
    assert len(store.list_entries(session, p2.id)) == n1

    prefixes = {e.key: e.value for e in store.list_entries(session, p1.id, kind="object_prefix")}
    assert prefixes["H-EXT_"] == "host_external"
    assert prefixes["IPP_"] == "snat_pool"


def test_upsert_offener_kind_und_delete(session_factory):
    session = session_factory()
    p = store.get_or_create_profile(session, customer="Kunde B", name="Standard")
    session.commit()

    # neue, bisher unbekannte Wörterbuch-Art ist problemlos möglich (kind offen)
    e = store.upsert_entry(session, p.id, "neue_vorgabe", "X_", "custom")
    session.commit()
    assert e.value == "custom"

    # upsert aktualisiert statt zu duplizieren
    e2 = store.upsert_entry(session, p.id, "neue_vorgabe", "X_", "geaendert")
    session.commit()
    assert e2.id == e.id
    assert len(store.list_entries(session, p.id, kind="neue_vorgabe")) == 1
    assert e2.value == "geaendert"

    assert store.delete_entry(session, e.id) is True
    session.commit()
    assert store.list_entries(session, p.id, kind="neue_vorgabe") == []


def test_api_read_open_write_protected(session_factory, client):
    # Seed in dieselbe In-Memory-DB (StaticPool -> von client geteilt)
    seed = session_factory()
    profile = store.seed_sop_profile(seed)
    seed.commit()

    r = client.get("/naming-profiles")
    assert r.status_code == 200
    assert any(p["customer"] == "SOP" for p in r.json())

    r = client.get(f"/naming-profiles/{profile.id}/dictionary?kind=utm_prefix")
    assert r.status_code == 200
    keys = {e["key"] for e in r.json()}
    assert {"WF_", "AC_", "SSL_"} <= keys

    # Schreiben ohne internen Token -> abgelehnt
    payload = {"kind": "site_code", "key": "010", "value": "Werk Ost"}
    r = client.post(f"/naming-profiles/{profile.id}/dictionary", json=payload)
    assert r.status_code in (401, 422)

    # Schreiben mit Token -> ok
    token = get_settings().internal_api_token
    r = client.post(
        f"/naming-profiles/{profile.id}/dictionary",
        json=payload,
        headers={"X-Internal-Token": token},
    )
    assert r.status_code == 200
    assert r.json()["value"] == "Werk Ost"

    # unbekanntes Profil -> 404
    r = client.get("/naming-profiles/9999/dictionary")
    assert r.status_code == 404
