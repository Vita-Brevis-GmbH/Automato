"""Verwaltung im UI: Mandant/Quellsystem anlegen, letzte Laeufe, Link zur Benutzerverwaltung."""

from datetime import UTC, datetime, timedelta

from app.api.auth_config import admin_console_url
from app.config import Settings
from app.models import AuditLog, CollectorRun, IpamType, SourceSystem, Tenant
from app.models.collector_run import RunStatus
from app.models.tenant import DnsType

ADMIN = {"X-Dev-Roles": "admin"}


def _tenant(session, name: str) -> int:
    t = Tenant(name=name, ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    session.add(t)
    session.flush()
    return t.id


# --- Mandant anlegen ---------------------------------------------------------------


def test_mandant_anlegen_admin_mit_audit(session_factory, client):
    r = client.post("/tenants", json={"name": "  testkunde ", "code": "TK1"}, headers=ADMIN)
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "testkunde"  # getrimmt
    assert body["code"] == "TK1"
    assert body["ipam_type"] == "netbox"
    assert [t["name"] for t in client.get("/tenants").json()] == ["testkunde"]

    s = session_factory()
    entry = s.query(AuditLog).filter_by(action="tenant.created").one()
    assert entry.subject == str(body["id"])
    s.close()


def test_mandant_anlegen_nur_admin_und_eindeutig(client):
    viewer = {"X-Dev-Roles": "viewer"}
    operator = {"X-Dev-Roles": "operator"}
    assert client.post("/tenants", json={"name": "a"}, headers=viewer).status_code == 403
    assert client.post("/tenants", json={"name": "a"}, headers=operator).status_code == 403
    assert client.post("/tenants", json={"name": "a"}, headers=ADMIN).status_code == 201
    assert client.post("/tenants", json={"name": "a"}, headers=ADMIN).status_code == 409
    assert client.post("/tenants", json={"name": "   "}, headers=ADMIN).status_code == 422
    assert (
        client.post("/tenants", json={"name": "b", "ipam_type": "x"}, headers=ADMIN).status_code
        == 422
    )


# --- Quellsystem anlegen -----------------------------------------------------------


def test_quellsystem_anlegen_und_liste(session_factory, client):
    r = client.post(
        "/systems",
        json={"name": "fw-test", "platform": "fortigate", "base_url": "https://fw.example"},
        headers=ADMIN,
    )
    assert r.status_code == 201
    assert r.json()["base_url"] == "https://fw.example"
    rows = client.get("/systems").json()
    assert [(s["name"], s["platform"], s["base_url"]) for s in rows] == [
        ("fw-test", "fortigate", "https://fw.example")
    ]

    s = session_factory()
    assert s.query(AuditLog).filter_by(action="source.created").count() == 1
    s.close()


def test_quellsystem_nur_admin_eindeutig_und_plattform_geprueft(client):
    op = {"X-Dev-Roles": "operator"}
    body = {"name": "fw-1", "platform": "fortigate"}
    assert client.post("/systems", json=body, headers=op).status_code == 403
    assert client.post("/systems", json=body, headers=ADMIN).status_code == 201
    assert client.post("/systems", json=body, headers=ADMIN).status_code == 409
    bad = {"name": "fw-2", "platform": "pfsense"}
    assert client.post("/systems", json=bad, headers=ADMIN).status_code == 422


# --- Letzte Laeufe -----------------------------------------------------------------


def _seed_runs(session_factory) -> tuple[int, int]:
    s = session_factory()
    a = _tenant(s, "kunde-a")
    b = _tenant(s, "kunde-b")
    fw = SourceSystem(name="fw", platform="fortigate", base_url="", enabled=True)
    s.add(fw)
    s.flush()
    now = datetime.now(UTC)
    s.add_all(
        [
            CollectorRun(
                id="run-a-alt",
                platform="fortigate",
                tenant_id=a,
                queued_at=now - timedelta(hours=2),
            ),
            CollectorRun(
                id="run-a-neu",
                platform="fortigate",
                tenant_id=a,
                source_system_id=fw.id,
                execution="external",
                status=RunStatus.COMPLETED,
                rule_count=75,
                queued_at=now,
                finished_at=now,
            ),
            CollectorRun(
                id="run-b", platform="fortigate", tenant_id=b, queued_at=now - timedelta(hours=1)
            ),
        ]
    )
    s.commit()
    s.close()
    return a, b


def test_laeufe_neueste_zuerst_mit_details(session_factory, client):
    _seed_runs(session_factory)
    rows = client.get("/jobs").json()  # dev ohne Header = Admin -> alle
    assert [r["id"] for r in rows] == ["run-a-neu", "run-b", "run-a-alt"]
    neu = rows[0]
    assert neu["execution"] == "external"
    assert neu["status"] == "completed"
    assert neu["rule_count"] == 75
    assert neu["queued_at"] and neu["finished_at"]
    assert [r["id"] for r in client.get("/jobs?limit=1").json()] == ["run-a-neu"]


def test_laeufe_mandantsgescoped(session_factory, client):
    a, b = _seed_runs(session_factory)
    viewer_a = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(a)}
    ids = [r["id"] for r in client.get("/jobs", headers=viewer_a).json()]
    assert ids == ["run-a-neu", "run-a-alt"]  # kein Lauf von kunde-b
    assert client.get(f"/jobs?tenant_id={b}", headers=viewer_a).status_code == 403
    gefiltert = client.get(f"/jobs?tenant_id={b}").json()  # Admin filtert
    assert [r["id"] for r in gefiltert] == ["run-b"]
    assert client.get("/jobs?limit=0").status_code == 422


# --- Link zur Keycloak-Benutzerverwaltung ------------------------------------------


def test_admin_console_url_aus_issuer_oder_konfiguriert():
    derived = Settings(oidc_issuer="https://auth.example.ch/realms/automato-dev")
    assert admin_console_url(derived) == (
        "https://auth.example.ch/admin/master/console/#/automato-dev"
    )
    configured = Settings(
        oidc_issuer="https://auth.example.ch/realms/automato-dev",
        oidc_admin_console_url="https://auth.int.example.ch/admin/master/console/#/automato-dev/",
    )
    assert admin_console_url(configured) == (
        "https://auth.int.example.ch/admin/master/console/#/automato-dev"
    )
    assert admin_console_url(Settings(oidc_issuer="")) == ""


def test_auth_config_liefert_admin_console_url(client):
    assert "admin_console_url" in client.get("/auth/config").json()


# --- Namensprofil einem Mandanten zuordnen -----------------------------------------


def test_mandant_namensprofil_zuordnen_und_loesen(client):
    tid = client.post("/tenants", json={"name": "kunde-np"}, headers=ADMIN).json()["id"]
    pid = client.post(
        "/naming-profiles", json={"customer": "Kunde NP", "name": "Standard"}, headers=ADMIN
    ).json()["id"]

    r = client.patch(f"/tenants/{tid}", json={"naming_profile_id": pid}, headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["naming_profile_id"] == pid
    assert client.get("/tenants").json()[0]["naming_profile_id"] == pid

    # unbekanntes Profil -> 422; Kuerzel-Aenderung laesst die Zuordnung unberuehrt
    assert (
        client.patch(f"/tenants/{tid}", json={"naming_profile_id": 999}, headers=ADMIN).status_code
        == 422
    )
    assert (
        client.patch(f"/tenants/{tid}", json={"code": "NP1"}, headers=ADMIN).json()[
            "naming_profile_id"
        ]
        == pid
    )

    # null = zurueck zur SOP-Default-Konvention
    r = client.patch(f"/tenants/{tid}", json={"naming_profile_id": None}, headers=ADMIN)
    assert r.json()["naming_profile_id"] is None
