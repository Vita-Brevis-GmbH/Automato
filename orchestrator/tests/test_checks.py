"""Steckbare Pruefungen (0.110.0, ADR 0039; Anwender 2026-10-29, Roadmap Version 1 Nr. 1):
je Pruefung eine Datei in einem Paket je Modul, im Abgleich selbst gefunden; je Mandant
schaltbar (Admin, Audit), mit Dauer und Anzahl des letzten Durchgangs."""

from app import checks, modules
from app.models import AuditLog, FindingType
from test_app_paths import _seed_verbund

ADMIN = {"X-Dev-Roles": "admin"}
OP = {"X-Dev-Roles": "operator"}
VIEWER = {"X-Dev-Roles": "viewer"}
# keine Pruefung im Abgleich: von Hand erfasst (0.109.0) bzw. nur noch in alten Daten
NOT_CHECKED = {FindingType.MANUAL, FindingType.EPG_NO_ENDPOINTS, FindingType.AMBIGUOUS_IP}


def test_verzeichnis_der_pruefungen():
    all_ = checks.all_checks()
    keys = [c.key for c in all_]
    assert len(keys) == len(set(keys)) and len(keys) >= 15
    for c in all_:
        assert c.module in modules.BY_KEY, c.key
        assert c.types and all(isinstance(t, FindingType) for t in c.types), c.key
        assert c.step in (None, *checks.STEPS), c.key
        assert all(c.title) and all(c.purpose), c.key
        assert c.run is not None or not c.in_reconcile, c.key
    by_module = {c.key: c.module for c in all_}
    assert by_module["objekte"] == "kern" and by_module["zonen"] == "design"
    assert by_module["topologie"] == "analyse" and by_module["applikationen"] == "doku"
    # jeder Befund-Typ hat (genau) eine Pruefung - ausser den Namen (Objekte und Endpunkte)
    covered = [t for c in all_ for t in c.types]
    assert set(FindingType) - set(covered) == NOT_CHECKED
    assert {t for t in covered if covered.count(t) > 1} == {FindingType.INVALID_NAME}
    assert checks.types_of("zonen") == {FindingType.ZONE_POLICY_VIOLATION, FindingType.ZONE_OVERLAP,
                                        FindingType.ZONE_OUTSIDE_SCHEMA,
                                        FindingType.ZONE_NET_EXIT}  # fmt: skip


def test_datei_im_paket_des_moduls():
    import importlib

    for c in checks.all_checks():
        mod = importlib.import_module(c.run.__module__) if c.run else None
        if mod is not None and mod.__name__.startswith("app.checks."):
            assert mod.__name__.split(".")[2] == c.module, c.key


def _findings(client, tid) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in client.get("/findings", params={"tenant_id": tid, "limit": 5000}).json():
        out[f["type"]] = out.get(f["type"], 0) + 1
    return out


def _list(client, tid, headers=VIEWER) -> dict[str, dict]:
    r = client.get("/checks", params={"tenant_id": tid}, headers=headers)
    assert r.status_code == 200, r.text
    return {c["key"]: c for c in r.json()}


def _switch(client, tid, key, on, headers=ADMIN):
    return client.put(f"/checks/{key}", params={"tenant_id": tid}, json={"enabled": on},
                      headers=headers)  # fmt: skip


def test_pruefung_je_mandant_schalten(client, session_factory):
    tid, _, _ = _seed_verbund(client)
    other = client.post("/tenants", json={"name": "Kunde B"}, headers=ADMIN).json()["id"]
    assert client.post("/reconcile", params={"tenant_id": tid}, headers=OP).status_code == 200
    before = _findings(client, tid)
    assert before == {"unmatched_object": 2, "app_connection_mismatch": 1}, before

    listed = _list(client, tid)
    assert list(listed) == [c.key for c in checks.all_checks()]
    ipam = listed["ipam_endpunkte"]
    assert ipam["enabled"] and ipam["effective"] and ipam["module"] == "analyse"
    assert ipam["module_title"] == "Analyse" and "unmatched_object" in ipam["types"]
    assert ipam["stats"]["count"] == 2 and ipam["stats"]["ms"] >= 0
    assert listed["objekte"]["stats"]["count"] == 0
    assert listed["applikationen"]["stats"]["count"] == 1  # eigener Durchgang
    assert listed["applikationen"]["step"] == "applikationen"
    assert not listed["applikationen"]["in_reconcile"]

    # nur der Admin schaltet; unbekannte Pruefungen gibt es nicht
    assert _switch(client, tid, "ipam_endpunkte", False, OP).status_code == 403
    assert _switch(client, tid, "gibts-nicht", False).status_code == 404
    r = _switch(client, tid, "ipam_endpunkte", False)
    assert r.status_code == 200
    out = {c["key"]: c for c in r.json()}
    assert not out["ipam_endpunkte"]["enabled"] and not out["ipam_endpunkte"]["effective"]
    assert _list(client, other)["ipam_endpunkte"]["enabled"]  # der andere Mandant nicht

    # beim naechsten Abgleich fallen die Befunde der Pruefung weg, die anderen bleiben
    assert client.post("/reconcile", params={"tenant_id": tid}, headers=OP).status_code == 200
    assert _findings(client, tid) == {"app_connection_mismatch": 1}
    assert _switch(client, tid, "applikationen", False).status_code == 200
    assert client.post("/reconcile", params={"tenant_id": tid}, headers=OP).status_code == 200
    assert _findings(client, tid) == {}

    # wieder an: die Befunde kommen zurueck
    assert _switch(client, tid, "ipam_endpunkte", True).status_code == 200
    assert _switch(client, tid, "applikationen", True).status_code == 200
    assert client.post("/reconcile", params={"tenant_id": tid}, headers=OP).status_code == 200
    assert _findings(client, tid) == before

    s = session_factory()
    audits = [a for a in s.query(AuditLog) if a.action == "check.switched"]
    s.close()
    assert [(a.subject, a.detail["enabled"]) for a in audits] == [
        ("ipam_endpunkte", False), ("applikationen", False), ("ipam_endpunkte", True),
        ("applikationen", True)]  # fmt: skip
    assert set(audits[0].detail) == {"tenant_id", "check", "enabled"}


def test_modul_aus_pruefung_laeuft_nicht(client, session_factory):
    from app.store import reconcile

    tid, _, _ = _seed_verbund(client)
    assert client.post("/reconcile", params={"tenant_id": tid}, headers=OP).status_code == 200
    assert _findings(client, tid)["unmatched_object"] == 2
    r = client.put("/modules/analyse", params={"tenant_id": tid}, json={"enabled": False},
                   headers=ADMIN)  # fmt: skip
    assert r.status_code == 200
    listed = _list(client, tid)
    assert listed["ipam_endpunkte"]["enabled"] and not listed["ipam_endpunkte"]["effective"]
    assert listed["objekte"]["effective"]  # Kern bleibt
    # Abgleich wie nach einem Lauf (die Abgleich-API gehoert zur Analyse und ist jetzt aus)
    s = session_factory()
    reconcile.reconcile_tenant(s, tid)
    s.commit()
    s.close()
    assert "unmatched_object" not in _findings(client, tid)


def test_findings_aus_keine_pruefungen_api(client):
    tid = client.post("/tenants", json={"name": "Kunde C"}, headers=ADMIN).json()["id"]
    client.put("/modules/findings", params={"tenant_id": tid}, json={"enabled": False},
               headers=ADMIN)  # fmt: skip
    r = client.get("/checks", params={"tenant_id": tid}, headers=VIEWER)
    assert r.status_code == 403 and r.json()["detail"]["module"] == "findings"


def test_fremder_befund_typ_ist_ein_fehler(monkeypatch):
    import pytest
    from app.models import SubjectKind
    from app.store.reconcile import Issue

    chk = checks.Check(
        "probe",
        "kern",
        ("Probe", "Probe"),
        ("Probe", "Probe"),
        frozenset({FindingType.UNUSED_OBJECT}),
        lambda s, t, cx: [Issue(FindingType.ZONE_OVERLAP, SubjectKind.ZONE, "x")],
    )
    with pytest.raises(ValueError, match="zone_overlap"):
        checks.run(chk, None, 1, checks.Context())
    monkeypatch.setattr(checks, "STRICT", False)
    assert len(checks.run(chk, None, 1, checks.Context())) == 1  # nur Warnung
    assert checks.stats(1, "probe")["count"] == 1
