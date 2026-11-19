"""Gesamt-Exporte (1.0.1): Doku aller Applikationen als ZIP je Applikation mit Export-Passwort
(AES-256) und alle Findings als Excel nach Kategorie, Details als Kommentar.

Seit 1.21.0 (ADR 0069, Anwender 2026-11-18: „eine Auswahl treffen …, welche Applikationen ich
herunterladen möchte“, „als Hintergrunddienst laufen lassen“): Auswahl der Applikationen (jede Id
sichtbar, sonst 404 mit den Ids), der Export als Auftrag im Hintergrund mit Fortschritt, Fehlern
je Applikation, Download nur für den Auftraggeber, Ablauf, Abbruch durch einen Neustart und dem
Export-Passwort nur im Speicher. Der Auftrag läuft in Tests direkt in der Anfrage (`conftest`),
hier auch im eigenen Thread.
"""

import io
import logging
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import test_aci_relevance as ar
import test_app_paths as tap
import test_tenant_roles as ttr
from app.api import applications as apps_api
from app.audit import decode_detail
from app.config import get_settings
from app.models import (
    AuditLog,
    Base,
    ExportJob,
    Finding,
    FindingStatus,
    FindingType,
    IpamType,
    Severity,
    SubjectKind,
    Tenant,
)
from app.models.tenant import DnsType
from app.store import applications as app_store
from app.store import export_jobs, secure_zip
from openpyxl import load_workbook
from sqlalchemy import select

OP = {"X-Dev-Roles": "operator"}
ADMIN = {"X-Dev-Roles": "admin"}
ANNA = {"X-Dev-User": "anna", "X-Dev-Roles": "admin"}
BEAT = {"X-Dev-User": "beat", "X-Dev-Roles": "admin"}
PW = "geheim-1234"
REAL_SPAWN = export_jobs.spawn  # vor dem Ersatz in `conftest` (beim Sammeln der Tests)


def test_verschluesseltes_zip():
    body = secure_zip.encrypted([("a.txt", "hallo"), ("b.bin", b"\x00\x01")], PW)
    z = zipfile.ZipFile(io.BytesIO(body))
    assert all(i.flag_bits & 0x1 for i in z.infolist())  # verschlüsselt
    with pytest.raises(RuntimeError):  # ohne Passwort nicht lesbar
        z.read("a.txt")
    assert secure_zip.read(body, PW) == {"a.txt": b"hallo", "b.bin": b"\x00\x01"}
    with pytest.raises(RuntimeError):
        secure_zip.read(body, "falsch-falsch")


def _apps(client, names=("Web", "Portal Süd")) -> tuple[int, dict[str, int]]:
    tid = tap._seed(client)
    ids = {}
    for name in names:
        body = {"name": name, "members": [{"kind": "epg", "ref": ar.WEB}]}
        r = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP)
        assert r.status_code == 201, r.text
        ids[name] = r.json()["id"]
    return tid, ids


def _start(client, tid, headers=None, **extra):
    body = {"tenant_id": tid, "password": PW, **extra}
    return client.post("/applications/export", json=body, headers=headers or {})


def _download(client, job, headers=None) -> zipfile.ZipFile:
    r = client.get(f"/applications/exports/{job['id']}/download", headers=headers or {})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert job["filename"] in r.headers["content-disposition"]
    return zipfile.ZipFile(io.BytesIO(r.content))


def _deferred(monkeypatch) -> list:
    """Thread erst nach der Antwort starten (die SQLite der Tests hat eine Verbindung)."""
    pending: list = []
    monkeypatch.setattr(export_jobs, "spawn", lambda *call: pending.append(call))
    return pending


def test_alle_applikationen_je_zip_mit_passwort(client, session_factory):
    tid, _ids = _apps(client)
    r = _start(client, tid)
    assert r.status_code == 202, r.text
    job = r.json()
    assert (job["status"], job["total"], job["done"], job["errors"]) == ("done", 2, 2, [])
    assert job["filename"].startswith("applikationen-kunde-a-") and job["size"] > 0
    assert client.get(f"/applications/exports/{job['id']}").json()["status"] == "done"
    outer = _download(client, job)
    assert sorted(outer.namelist()) == ["portal-sued.zip", "web.zip"]
    for n in outer.namelist():
        inner = outer.read(n)
        assert all(i.flag_bits & 0x1 for i in zipfile.ZipFile(io.BytesIO(inner)).infolist())
        files = secure_zip.read(inner, PW)
        assert any(f.endswith(".docx") for f in files)
        assert any(f.endswith(".drawio") for f in files)
    s = session_factory()
    rows = [a for a in s.query(AuditLog).all() if a.action.startswith("application.export")]
    assert [a.action for a in rows] == ["application.exported", "application.export_downloaded"]
    detail = decode_detail(rows[0])
    assert detail["count"] == 2 and detail["export_id"] == job["id"] and PW not in str(detail)


def test_export_ohne_passwort_bzw_zu_kurz(client):
    tid = tap._seed(client)
    assert client.post("/applications/export", json={"tenant_id": tid}).status_code == 422
    r = client.post("/applications/export", json={"tenant_id": tid, "password": "kurz"})
    assert r.status_code == 422
    r = client.post("/applications/export", json={"tenant_id": tid, "password": " " * 10})
    assert r.status_code == 422


def test_export_ohne_applikationen_404(client):
    tid = tap._seed(client)
    r = client.post("/applications/export", json={"tenant_id": tid, "password": PW})
    assert r.status_code == 404


# --- Auswahl ---------------------------------------------------------------------------------


def test_nur_die_gewaehlten_applikationen(client):
    tid, ids = _apps(client, ("Web", "Portal Süd", "Backup"))
    r = _start(client, tid, ids=[ids["Backup"], ids["Web"], ids["Web"]])  # doppelt = einmal
    assert r.status_code == 202, r.text
    job = r.json()
    assert (job["status"], job["total"]) == ("done", 2)
    assert sorted(_download(client, job).namelist()) == ["backup.zip", "web.zip"]


def test_auswahl_fremd_bzw_unbekannt_ist_ein_fehler(client):
    """Jede gewählte Id muss sichtbar sein - sonst 404 mit den Ids, kein Auftrag."""
    tid, ids = _apps(client, ("Web",))
    other = client.post("/tenants", json={"name": "Kunde B"}, headers=ADMIN).json()["id"]
    fremd = client.post(f"/applications?tenant_id={other}", json={"name": "Fremd"}, headers=OP)
    fremd_id = fremd.json()["id"]
    for bad in ([ids["Web"], fremd_id], [ids["Web"], 999999]):
        r = _start(client, tid, ids=bad)
        assert r.status_code == 404, r.text
        assert str(bad[1]) in r.json()["detail"]
    assert client.get("/applications/exports").json() == []
    assert _start(client, tid, ids=[]).status_code == 422  # leere Auswahl


def test_kunden_filter_der_betreuer(client):
    """Betreuer mit gewähltem Kunden: nur Applikationen dieses Kunden sind wählbar."""
    tid, ids = _apps(client, ("Web",))
    body = {"tenant_id": tid, "name": "Kunde X",
            "selectors": [{"kind": "aci_tenant", "value": "TE_gibts_nicht"}]}  # fmt: skip
    kunde = client.post("/customers", json=body, headers=OP).json()["id"]
    r = _start(client, tid, customer_id=kunde, ids=[ids["Web"]])
    assert r.status_code == 404 and str(ids["Web"]) in r.json()["detail"]


# --- Hintergrund -----------------------------------------------------------------------------


def test_im_thread_mit_fortschritt(client, monkeypatch):
    """Die Anfrage kehrt sofort zurück (wartet); der Thread rechnet, schreibt je Applikation den
    Fortschritt (n von m, aktuelle Applikation) und ist danach fertig."""
    tid, _ids = _apps(client)
    pending = _deferred(monkeypatch)
    seen: list[tuple] = []
    original = apps_api.export_files

    def spy(session, *args, **kwargs):
        job = session.scalar(select(ExportJob))
        seen.append((job.status, job.done, job.total, job.current))
        return original(session, *args, **kwargs)

    monkeypatch.setattr(apps_api, "export_files", spy)
    r = _start(client, tid)
    assert r.status_code == 202, r.text
    job = r.json()
    assert (job["status"], job["done"], job["total"]) == ("waiting", 0, 2)
    REAL_SPAWN(*pending[0])  # der echte Thread, nach der Antwort
    export_jobs.join(job["id"], 60)
    assert not export_jobs.running_here(job["id"])
    assert seen == [("running", 0, 2, "Portal Süd"), ("running", 1, 2, "Web")]
    done = client.get(f"/applications/exports/{job['id']}").json()
    assert (done["status"], done["done"], done["current"]) == ("done", 2, None)
    assert done["started_at"] and done["finished_at"]


def test_eine_applikation_scheitert_der_rest_kommt(client, monkeypatch):
    tid, ids = _apps(client)
    original = apps_api.export_files

    def flaky(session, factory, background, principal, app, *args, **kwargs):
        if app.name == "Portal Süd":
            raise RuntimeError("kaputt")
        return original(session, factory, background, principal, app, *args, **kwargs)

    monkeypatch.setattr(apps_api, "export_files", flaky)
    job = _start(client, tid).json()
    assert job["status"] == "done"
    assert job["errors"] == [{"application_id": ids["Portal Süd"], "name": "Portal Süd",
                              "error": "RuntimeError: kaputt"}]  # fmt: skip
    outer = _download(client, job)
    assert sorted(outer.namelist()) == ["FEHLER.txt", "web.zip"]
    assert "Portal Süd" in outer.read("FEHLER.txt").decode()


def test_abbrechen(client, monkeypatch):
    tid, _ids = _apps(client)
    original = apps_api.export_files

    def cancel_after_first(session, *args, **kwargs):
        out = original(session, *args, **kwargs)
        export_jobs.cancel(session.scalar(select(ExportJob.id)))  # wie POST …/cancel
        return out

    monkeypatch.setattr(apps_api, "export_files", cancel_after_first)
    job = _start(client, tid).json()
    assert job["status"] == "cancelled" and job["message"] == "vom Benutzer abgebrochen"
    assert not list(Path(get_settings().export_dir).iterdir())  # halbe Datei weg
    assert client.get(f"/applications/exports/{job['id']}/download").status_code == 404
    # beendet: abbrechen 409, entfernen 204
    assert client.post(f"/applications/exports/{job['id']}/cancel").status_code == 409
    assert client.delete(f"/applications/exports/{job['id']}").status_code == 204
    assert client.get(f"/applications/exports/{job['id']}").status_code == 404


def test_wartender_auftrag_abbrechen(client, monkeypatch):
    tid, _ids = _apps(client, ("Web",))
    pending = _deferred(monkeypatch)
    job = _start(client, tid).json()
    r = client.post(f"/applications/exports/{job['id']}/cancel")
    assert r.status_code == 200 and r.json()["cancelling"] is True
    pending[0][0]()
    assert client.get(f"/applications/exports/{job['id']}").json()["status"] == "cancelled"


def test_verwaister_auftrag_abbrechen(client, session_factory):
    """Auftrag ohne Thread in diesem Prozess (z. B. gestorben): abbrechen beendet ihn gleich."""
    tid, _ids = _apps(client, ("Web",))
    s = session_factory()
    job = export_jobs.create(s, "applications", "dev-admin", tid, 1, "x.zip")
    s.commit()
    r = client.post(f"/applications/exports/{job.id}/cancel")
    s.close()
    assert r.status_code == 200 and r.json()["status"] == "cancelled"


def test_nur_ein_auftrag_je_benutzer(client, monkeypatch):
    tid, _ids = _apps(client, ("Web",))
    _deferred(monkeypatch)  # bleibt „wartet“
    assert _start(client, tid, ANNA).status_code == 202
    r = _start(client, tid, ANNA)
    assert r.status_code == 409 and "läuft schon" in r.json()["detail"]
    assert _start(client, tid, BEAT).status_code == 202  # ein anderer Benutzer schon


def test_nur_fuer_den_auftraggeber(client):
    tid, _ids = _apps(client, ("Web",))
    job = _start(client, tid, ANNA).json()
    url = f"/applications/exports/{job['id']}"
    assert client.get(url, headers=ANNA).status_code == 200
    mine = client.get("/applications/exports", headers=ANNA).json()
    assert [j["id"] for j in mine] == [job["id"]]
    for method, path in (("GET", url), ("GET", f"{url}/download"), ("POST", f"{url}/cancel"),
                         ("DELETE", url)):  # fmt: skip
        r = client.request(method, path, headers=BEAT)
        assert r.status_code == 404, (method, path, r.text)
    assert client.get("/applications/exports", headers=BEAT).json() == []
    assert client.get(f"{url}/download", headers=ANNA).status_code == 200


def test_ablauf_loescht_auftrag_und_datei(client, session_factory):
    tid, _ids = _apps(client, ("Web",))
    job = _start(client, tid).json()
    folder = Path(get_settings().export_dir)
    assert (folder / f"{job['id']}.zip").is_file()
    expires = datetime.fromisoformat(job["expires_at"])
    assert timedelta(hours=23) < expires - datetime.now(UTC) <= timedelta(hours=24)
    orphan = folder / f"{'a' * 32}.zip"  # Datei ohne Auftrag (Rest)
    orphan.write_bytes(b"x")
    other = folder / "notiz.txt"  # fremde Dateien bleiben
    other.write_text("bleibt")
    s = session_factory()
    s.get(ExportJob, job["id"]).expires_at = datetime.now(UTC) - timedelta(minutes=1)
    s.commit()
    assert export_jobs.purge(s) == 2
    s.close()
    assert not (folder / f"{job['id']}.zip").exists() and not orphan.exists() and other.exists()
    assert client.get(f"/applications/exports/{job['id']}/download").status_code == 404
    assert client.get("/applications/exports").json() == []


def test_neustart_bricht_laufende_auftraege_ab(client, session_factory):
    tid, _ids = _apps(client, ("Web",))
    s = session_factory()
    job = export_jobs.create(s, "applications", "dev-admin", tid, 3, "x.zip")
    job.status, job.done, job.current = export_jobs.RUNNING, 1, "Web"
    s.commit()
    folder = Path(get_settings().export_dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{job.id}.part").write_bytes(b"halb")
    assert export_jobs.abort_interrupted(session_factory) == 1
    s.close()
    out = client.get(f"/applications/exports/{job.id}").json()
    assert (out["status"], out["current"]) == ("aborted", None)
    assert "neu gestartet" in out["message"] and "nicht gespeichert" in out["message"]
    assert not (folder / f"{job.id}.part").exists()
    en = client.get(f"/applications/exports/{job.id}", headers={"Accept-Language": "en"}).json()
    assert "restarted" in en["message"]


def test_groesse_begrenzt(client):
    tid, _ids = _apps(client, ("Web",))
    settings = get_settings()
    previous, settings.export_max_mb = settings.export_max_mb, 0
    try:
        job = _start(client, tid).json()
    finally:
        settings.export_max_mb = previous
    assert job["status"] == "failed" and "grösser als 0 MB" in job["message"]
    assert not list(Path(get_settings().export_dir).iterdir())


def test_ablage_nicht_beschreibbar(client, tmp_path):
    tid, _ids = _apps(client, ("Web",))
    blocker = tmp_path / "datei"
    blocker.write_text("x")
    settings = get_settings()
    previous, settings.export_dir = settings.export_dir, str(blocker / "exports")
    try:
        job = _start(client, tid).json()
    finally:
        settings.export_dir = previous
    assert job["status"] == "failed" and "nicht beschreibbar" in job["message"]


def test_passwort_nirgends_gespeichert(client, session_factory, caplog, monkeypatch):
    """Das Export-Passwort steht weder in der Datenbank (alle Tabellen) noch im Protokoll, im
    Audit oder in den Dateien der Ablage - auch nicht, wenn eine Applikation scheitert."""
    caplog.set_level(logging.DEBUG)
    secret = "Unik4t-Exportpasswort-7781"
    tid, _ids = _apps(client)
    original = apps_api.export_files

    def flaky(session, factory, background, principal, app, *args, **kwargs):
        if app.name == "Web":
            raise RuntimeError("kaputt")
        return original(session, factory, background, principal, app, *args, **kwargs)

    monkeypatch.setattr(apps_api, "export_files", flaky)
    r = client.post("/applications/export", json={"tenant_id": tid, "password": secret})
    job = r.json()
    assert job["status"] == "done" and secret not in r.text
    assert client.get(f"/applications/exports/{job['id']}/download").status_code == 200
    s = session_factory()
    for table in Base.metadata.sorted_tables:
        for row in s.execute(table.select()):
            assert secret not in repr(tuple(row)), table.name
    for row in s.scalars(select(AuditLog)):
        assert secret not in repr(decode_detail(row))
    s.close()
    assert secret not in caplog.text
    files = [p for p in Path(get_settings().export_dir).rglob("*") if p.is_file()]
    assert files
    for path in files:
        assert secret.encode() not in path.read_bytes() and secret not in path.name, path


def test_modul_doku_aus(client):
    tid, _ids = _apps(client, ("Web",))
    r = client.put("/modules/doku", params={"tenant_id": tid}, json={"enabled": False},
                   headers=ADMIN)  # fmt: skip
    assert r.status_code == 200
    r = _start(client, tid)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "module_disabled"


def test_abhaengigkeiten_einmal_je_applikation(client, monkeypatch):
    """1.21.0: die Fussabdrücke für die Abhängigkeiten rechnet der Gesamt-Export je Applikation
    einmal - vorher je Paar (bei 51 Applikationen 2601 statt 51, rund 90 % der Zeit)."""
    tid, _ids = _apps(client, ("Web", "Portal Süd", "Backup", "Monitoring"))
    calls: list[int] = []
    original = app_store.footprint

    def count(session, app, *args):
        calls.append(app.id)
        return original(session, app, *args)

    monkeypatch.setattr(app_store, "footprint", count)
    job = _start(client, tid).json()
    assert job["status"] == "done"
    assert len(calls) == len(set(calls)) == 4  # je Applikation einmal (vorher 4 x 4 = 16)


def test_router_vor_den_applikationen(client):
    """`/applications/exports` darf nicht als `/applications/{id}` gelesen werden."""
    assert client.get("/applications/exports").status_code == 200


# --- Rechte (ADR 0068) -----------------------------------------------------------------------


@pytest.mark.parametrize("role", [ttr.OPERATOR, ttr.KADMIN])
def test_mandanten_benutzer_exportieren_im_eigenen_mandanten(client, role):
    env = ttr._env(client)
    own, user = env["own"], ttr._user(env, role)
    app = client.post(f"/applications?tenant_id={own}", json={"name": "Eigene"}, headers=ADMIN)
    r = _start(client, own, user)
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["status"] == "done" and job["total"] == 1
    assert _download(client, job, user).namelist() == ["eigene.zip"]
    mine = client.get("/applications/exports", headers=user).json()
    assert [j["id"] for j in mine] == [job["id"]]
    # fremder Mandant bzw. fremde Applikation: nie
    assert _start(client, env["other"], user).status_code == 403
    r = _start(client, own, user, ids=[app.json()["id"], env["fremd_app"]])
    assert r.status_code == 404 and str(env["fremd_app"]) in r.json()["detail"]
    # der Export gehört dem Benutzer - ein Admin lädt ihn nicht herunter
    assert client.get(f"/applications/exports/{job['id']}/download").status_code == 404


@pytest.mark.parametrize("role", [ttr.OPERATOR, ttr.KADMIN, "viewer", "editor"])
def test_kunden_benutzer_im_gemeinsamen_mandanten_nicht(client, role):
    """Kunden-Benutzer exportieren ihre Applikationen einzeln (ohne Verbindungen und Plan) -
    den Gesamt-Export und den Excel-Export der Findings nicht (enger, ADR 0018)."""
    env = ttr._env(client)
    user = ttr._user(env, role, own=False)
    for method, path in (("POST", "/applications/export"), ("GET", "/applications/exports"),
                         ("GET", "/findings/export")):  # fmt: skip
        r = client.request(method, path, json={"tenant_id": env["own"], "password": PW},
                           headers=user)  # fmt: skip
        assert r.json()["detail"] == "not available for customer users", (method, path, r.text)


# --- Findings als Excel ----------------------------------------------------------------------


def _findings(session) -> tuple[int, int]:
    ids = []
    for name in ("kunde-a", "kunde-b"):
        t = Tenant(name=name, ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
        session.add(t)
        session.flush()
        ids.append(t.id)
    session.add_all([
        Finding(tenant_id=ids[0], type=FindingType.UNMATCHED_OBJECT, subject_kind=SubjectKind.RULE,
                subject_id="=HYPERLINK(\"x\")", severity=Severity.WARNING,
                detail={"object": "H_srv01", "rule": 7}),
        Finding(tenant_id=ids[1], type=FindingType.ZONE_OVERLAP, subject_kind=SubjectKind.ZONE,
                subject_id="DMZ", severity=Severity.ERROR, status=FindingStatus.IGNORED,
                note="bekannt"),
        Finding(tenant_id=ids[1], type=FindingType.MANUAL, subject_kind=SubjectKind.PATH,
                subject_id="a → b", detail={"title": "Regel anpassen"}),
    ])  # fmt: skip
    session.commit()
    return ids[0], ids[1]


def _types(content: bytes) -> set[str]:
    wb = load_workbook(io.BytesIO(content))
    return {line[2].value for ws in wb.worksheets[1:] for line in ws.iter_rows(min_row=2)}


def test_findings_als_excel_nach_kategorie(client, session_factory):
    a, b = _findings(session_factory())
    r = client.get("/findings/export")
    assert r.status_code == 200, r.text
    assert "spreadsheetml" in r.headers["content-type"]
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames[0] == "Übersicht"
    rows = {}
    for ws in wb.worksheets[1:]:
        head = [c.value for c in ws[1]]
        for line in ws.iter_rows(min_row=2):
            rec = dict(zip(head, line, strict=True))
            rows[rec["Typ"].value] = (ws.title, rec)
    assert set(rows) == {"unmatched_object", "zone_overlap", "manual"}  # alle Mandanten
    sheet, rec = rows["unmatched_object"]
    assert rec["Mandant"].value == "kunde-a"
    assert rec["Objekt"].value == '=HYPERLINK("x")' and rec["Objekt"].data_type == "s"
    assert "H_srv01" in rec["Meldung"].comment.text  # Details als Kommentar
    _, rec = rows["zone_overlap"]
    assert rec["Status"].value == "ignoriert" and rec["Notiz"].value == "bekannt"
    assert rows["manual"][0] == "Weitere"
    summary = [[c.value for c in line] for line in wb["Übersicht"].iter_rows(min_row=2)]
    assert any(line[2] == "zone_overlap" and line[6] == 1 for line in summary)  # ignoriert
    s = session_factory()
    (audit,) = (x for x in s.query(AuditLog).all() if x.action == "finding.exported")
    assert decode_detail(audit) == {"tenant_ids": [a, b], "count": 3}


def test_findings_export_nur_eigener_mandant(client, session_factory):
    a, b = _findings(session_factory())
    r = client.get("/findings/export", headers={"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(a)})
    assert r.status_code == 200
    assert _types(r.content) == {"unmatched_object"}
    r = client.get(f"/findings/export?tenant_id={b}",
                   headers={"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(a)})  # fmt: skip
    assert r.status_code == 403


def test_findings_export_ohne_mandanten_mit_modul_aus(client, session_factory):
    a, b = _findings(session_factory())
    r = client.put("/modules/findings", params={"tenant_id": b}, json={"enabled": False},
                   headers=ADMIN)  # fmt: skip
    assert r.status_code == 200
    assert _types(client.get("/findings/export").content) == {"unmatched_object"}
    assert client.get(f"/findings/export?tenant_id={b}").status_code == 403


def test_findings_export_im_eigenen_mandanten(client):
    env = ttr._env(client)
    user = ttr._user(env, ttr.OPERATOR)
    assert client.get("/findings/export", headers=user).status_code == 200
    r = client.get("/findings/export", params={"tenant_id": env["other"]}, headers=user)
    assert r.status_code == 403


def test_mandant_loeschen_nimmt_die_exporte_mit(client, session_factory):
    tid, _ids = _apps(client, ("Web",))
    job = _start(client, tid).json()
    r = client.delete(f"/tenants/{tid}", params={"confirm": "Kunde A"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert client.get(f"/applications/exports/{job['id']}").status_code == 404
    s = session_factory()
    assert s.scalar(select(ExportJob)) is None
    export_jobs.purge(s)  # die Datei ist danach ein Rest ohne Auftrag
    s.close()
    assert not list(Path(get_settings().export_dir).iterdir())


def test_zweiter_export_nutzt_die_staende(client, monkeypatch):
    """Stand der Doku (ADR 0037): der Export rechnet fehlende Stände und speichert sie; sein
    Audit (`application.exported`, `…export_downloaded`) ändert den Fingerabdruck nicht - ein
    zweiter Export rechnet nichts neu."""
    from app.store import doc_snapshots

    settings = get_settings()
    monkeypatch.setattr(settings, "doc_snapshots", True)
    tid, _ids = _apps(client)
    first = _start(client, tid).json()
    assert first["status"] == "done"
    _download(client, first)
    calls: list[str] = []
    original = doc_snapshots.compute

    def count(session, app, kind, params):
        calls.append(kind)
        return original(session, app, kind, params)

    monkeypatch.setattr(doc_snapshots, "compute", count)
    second = _start(client, tid).json()
    assert second["status"] == "done" and calls == []
