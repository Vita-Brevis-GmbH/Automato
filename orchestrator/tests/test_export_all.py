"""Gesamt-Exporte (1.0.1): Doku aller Applikationen als ZIP je Applikation mit Export-Passwort
(AES-256) und alle Findings als Excel nach Kategorie, Details als Kommentar."""

import io
import zipfile

import pytest
import test_aci_relevance as ar
import test_app_paths as tap
from app.models import (
    AuditLog,
    Finding,
    FindingStatus,
    FindingType,
    IpamType,
    Severity,
    SubjectKind,
    Tenant,
)
from app.models.tenant import DnsType
from app.store import secure_zip
from openpyxl import load_workbook

OP = {"X-Dev-Roles": "operator"}
PW = "geheim-1234"


def test_verschluesseltes_zip():
    body = secure_zip.encrypted([("a.txt", "hallo"), ("b.bin", b"\x00\x01")], PW)
    z = zipfile.ZipFile(io.BytesIO(body))
    assert all(i.flag_bits & 0x1 for i in z.infolist())  # verschlüsselt
    with pytest.raises(RuntimeError):  # ohne Passwort nicht lesbar
        z.read("a.txt")
    assert secure_zip.read(body, PW) == {"a.txt": b"hallo", "b.bin": b"\x00\x01"}
    with pytest.raises(RuntimeError):
        secure_zip.read(body, "falsch-falsch")


def test_alle_applikationen_je_zip_mit_passwort(client, session_factory):
    tid = tap._seed(client)
    for name in ("Web", "Portal Süd"):
        body = {"name": name, "members": [{"kind": "epg", "ref": ar.WEB}]}
        assert client.post(f"/applications?tenant_id={tid}", json=body, headers=OP).is_success
    r = client.post("/applications/export", json={"tenant_id": tid, "password": PW})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    outer = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(outer.namelist()) == ["portal-sued.zip", "web.zip"]
    for n in outer.namelist():
        inner = outer.read(n)
        assert all(i.flag_bits & 0x1 for i in zipfile.ZipFile(io.BytesIO(inner)).infolist())
        files = secure_zip.read(inner, PW)
        assert any(f.endswith(".docx") for f in files)
        assert any(f.endswith(".drawio") for f in files)
    s = session_factory()
    rows = [a for a in s.query(AuditLog).all() if a.action == "application.exported"]
    assert len(rows) == 1 and PW not in str(rows[0].detail)


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


def test_findings_export_nur_eigener_mandant(client, session_factory):
    a, b = _findings(session_factory())
    r = client.get("/findings/export", headers={"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(a)})
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.content))
    types = {line[2].value for ws in wb.worksheets[1:] for line in ws.iter_rows(min_row=2)}
    assert types == {"unmatched_object"}
    r = client.get(f"/findings/export?tenant_id={b}",
                   headers={"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(a)})  # fmt: skip
    assert r.status_code == 403
