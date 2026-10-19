"""Namensprofile & Wörterbücher (ADR 0004-Ergänzung): Store + API."""

from app.store import naming_profiles as store


def test_seed_idempotent(session_factory):
    session = session_factory()
    p1 = store.seed_default_profile(session)
    session.commit()
    n1 = len(store.list_entries(session, p1.id))

    # zweiter Lauf: gleiches Profil, keine Duplikate
    p2 = store.seed_default_profile(session)
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
    profile = store.seed_default_profile(seed)
    seed.commit()

    r = client.get("/naming-profiles")
    assert r.status_code == 200
    assert any(p["name"] == "default" for p in r.json())

    r = client.get(f"/naming-profiles/{profile.id}/dictionary?kind=utm_prefix")
    assert r.status_code == 200
    keys = {e["key"] for e in r.json()}
    assert {"WF_", "AC_", "SSL_"} <= keys

    # Schreiben als viewer -> 403 (RBAC: nur admin)
    payload = {"kind": "site_code", "key": "010", "value": "Werk Ost"}
    r = client.post(
        f"/naming-profiles/{profile.id}/dictionary",
        json=payload,
        headers={"X-Dev-Roles": "viewer"},
    )
    assert r.status_code == 403

    # Schreiben als admin (dev-Default ohne Header = Admin) -> ok
    r = client.post(f"/naming-profiles/{profile.id}/dictionary", json=payload)
    assert r.status_code == 200
    assert r.json()["value"] == "Werk Ost"

    # unbekanntes Profil -> 404
    r = client.get("/naming-profiles/9999/dictionary")
    assert r.status_code == 404


def test_api_profil_anlegen_leer_oder_mit_standardvorlage(client):
    leer = client.post("/naming-profiles", json={"customer": "Kunde B", "name": "Standard"})
    assert leer.status_code == 201
    assert client.get(f"/naming-profiles/{leer.json()['id']}/dictionary").json() == []

    vorlage = client.post(
        "/naming-profiles",
        json={"customer": "Kunde C", "name": "Standard", "with_defaults": True},
    )
    assert vorlage.status_code == 201
    eintraege = client.get(f"/naming-profiles/{vorlage.json()['id']}/dictionary").json()
    kinds = {e["kind"] for e in eintraege}
    assert {"object_prefix", "utm_prefix", "site_code"} <= kinds

    # nur Admin darf Profile anlegen
    r = client.post(
        "/naming-profiles",
        json={"customer": "X", "name": "Y"},
        headers={"X-Dev-Roles": "operator"},
    )
    assert r.status_code == 403


def test_vorlage_uebernimmt_den_gepflegten_standard(session_factory):
    session = session_factory()
    default = store.seed_default_profile(session)
    assert (default.customer, default.name) == ("default", "default")
    # Anpassung am Standard (z. B. eigenes Praefix) gilt auch fuer neue Profile
    store.upsert_entry(session, default.id, "object_prefix", "SRV_", "host")
    neu = store.get_or_create_profile(session, customer="Kunde B", name="Standard")
    store.apply_defaults(session, neu.id)
    session.commit()
    keys = {e.key for e in store.list_entries(session, neu.id, kind="object_prefix")}
    assert {"SRV_", "H_", "IPP_"} <= keys
    # keine kundenspezifischen Laufzeitdaten im Standard
    assert store.list_entries(session, default.id, kind="gg_code") == []


def test_api_profil_als_kopie_eines_bestehenden(session_factory, client):
    seed = session_factory()
    default = store.seed_default_profile(seed)
    store.upsert_entry(seed, default.id, "gg_code", "kunde1", "7")  # eigene Ergänzung
    seed.commit()
    n_default = len(store.list_entries(seed, default.id))
    seed.close()

    r = client.post(
        "/naming-profiles",
        json={"customer": "Kunde B", "name": "Standard", "copy_from_profile_id": default.id},
    )
    assert r.status_code == 201
    kopie = client.get(f"/naming-profiles/{r.json()['id']}/dictionary").json()
    assert len(kopie) == n_default
    assert {"kunde1"} <= {e["key"] for e in kopie if e["kind"] == "gg_code"}

    # Kopie ist unabhaengig: Aenderung am Original wirkt nicht auf die Kopie
    client.post(f"/naming-profiles/{default.id}/dictionary", json={"kind": "gg_code", "key": "x9"})
    kopie2 = client.get(f"/naming-profiles/{r.json()['id']}/dictionary?kind=gg_code").json()
    assert "x9" not in {e["key"] for e in kopie2}

    # Duplikat / unbekannte Vorlage / leere Namen
    assert (
        client.post(
            "/naming-profiles", json={"customer": "Kunde B", "name": "Standard"}
        ).status_code
        == 409
    )
    bad = {"customer": "Kunde C", "name": "S", "copy_from_profile_id": 999}
    assert client.post("/naming-profiles", json=bad).status_code == 422
    assert client.post("/naming-profiles", json={"customer": " ", "name": "S"}).status_code == 422


def test_api_namenskonvention_aus_bestehender_art_kopieren(session_factory, client):
    seed = session_factory()
    default = store.seed_default_profile(seed)
    leer = store.get_or_create_profile(seed, customer="Kunde B", name="Standard")
    seed.commit()
    n_prefixe = len(store.list_entries(seed, default.id, kind="object_prefix"))
    did, lid = default.id, leer.id
    seed.close()

    body = {"source_profile_id": did, "source_kind": "object_prefix", "kind": "vm_prefix"}
    r = client.post(f"/naming-profiles/{lid}/dictionary/copy", json=body)
    assert r.status_code == 200 and r.json()["copied"] == n_prefixe
    neu = client.get(f"/naming-profiles/{lid}/dictionary?kind=vm_prefix").json()
    assert len(neu) == n_prefixe

    same = {"source_profile_id": did, "source_kind": "object_prefix", "kind": "object_prefix"}
    assert client.post(f"/naming-profiles/{did}/dictionary/copy", json=same).status_code == 422
    assert client.post("/naming-profiles/999/dictionary/copy", json=body).status_code == 404
    op = {"X-Dev-Roles": "operator"}
    assert (
        client.post(f"/naming-profiles/{lid}/dictionary/copy", json=body, headers=op).status_code
        == 403
    )
