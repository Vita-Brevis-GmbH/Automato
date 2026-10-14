"""Mandanten-Kuerzel (GG-Code) + Standorte (ADR 0004-Ergaenzung): RBAC, Scoping, Audit."""

from app import audit
from app.models import AuditLog, IpamType, Site, Tenant
from app.models.tenant import DnsType


def _mk_tenant(session, name: str) -> int:
    t = Tenant(name=name, ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    session.add(t)
    session.flush()
    return t.id


def test_tenant_liste_enthaelt_code(session_factory, client):
    seed = session_factory()
    tid = _mk_tenant(seed, "kunde-a")
    seed.commit()
    rows = client.get("/tenants").json()
    assert rows[0]["id"] == tid
    assert rows[0]["code"] is None  # noch kein Kuerzel


def test_patch_code_nur_admin(session_factory, client):
    seed = session_factory()
    tid = _mk_tenant(seed, "kunde-a")
    seed.commit()

    # viewer darf nicht
    r = client.patch(f"/tenants/{tid}", json={"code": "ssgch"}, headers={"X-Dev-Roles": "viewer"})
    assert r.status_code == 403

    # admin setzt das Kuerzel
    r = client.patch(f"/tenants/{tid}", json={"code": "ssgch"}, headers={"X-Dev-Roles": "admin"})
    assert r.status_code == 200
    assert r.json()["code"] == "ssgch"
    assert client.get("/tenants").json()[0]["code"] == "ssgch"


def test_admin_ist_global_und_patcht_jeden_mandant(session_factory, client):
    # Schreiben ist admin-only, und admin = Global-Admin (Gesamtsicht): das Setzen des
    # Kuerzels gilt fuer jeden Mandanten, auch mit gesetztem X-Dev-Tenants (ignoriert).
    seed = session_factory()
    a = _mk_tenant(seed, "kunde-a")
    b = _mk_tenant(seed, "kunde-b")
    seed.commit()
    h = {"X-Dev-Roles": "admin", "X-Dev-Tenants": str(a)}
    assert client.patch(f"/tenants/{b}", json={"code": "casch"}, headers=h).status_code == 200


def test_standorte_crud_und_validierung(session_factory, client):
    seed = session_factory()
    tid = _mk_tenant(seed, "kunde-a")
    seed.commit()
    admin = {"X-Dev-Roles": "admin"}

    # ungueltiger Code (nicht dreistellig) -> 422
    assert (
        client.post(
            f"/tenants/{tid}/sites", json={"code": "0", "name": "RZ"}, headers=admin
        ).status_code
        == 422
    )

    # anlegen
    r = client.post(
        f"/tenants/{tid}/sites",
        json={"code": "000", "name": "RZ01 Hauptstandort"},
        headers=admin,
    )
    assert r.status_code == 201
    site_id = r.json()["id"]
    assert r.json()["code"] == "000"

    # doppelter Code pro Mandant -> 409
    assert (
        client.post(
            f"/tenants/{tid}/sites", json={"code": "000", "name": "x"}, headers=admin
        ).status_code
        == 409
    )

    # viewer darf lesen, aber nicht anlegen
    assert client.get(f"/tenants/{tid}/sites", headers={"X-Dev-Roles": "viewer"}).status_code == 200
    assert (
        client.post(
            f"/tenants/{tid}/sites",
            json={"code": "001", "name": "x"},
            headers={"X-Dev-Roles": "viewer"},
        ).status_code
        == 403
    )

    # loeschen (admin), danach leer
    assert client.delete(f"/tenants/{tid}/sites/{site_id}", headers=admin).status_code == 204
    assert client.get(f"/tenants/{tid}/sites", headers=admin).json() == []


def test_standorte_mandanten_scoping(session_factory, client):
    seed = session_factory()
    a = _mk_tenant(seed, "kunde-a")
    b = _mk_tenant(seed, "kunde-b")
    seed.commit()
    # viewer auf a darf die Standorte von b nicht sehen
    h = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(a)}
    assert client.get(f"/tenants/{b}/sites", headers=h).status_code == 403


def test_schreibpfade_werden_auditiert(session_factory, client):
    seed = session_factory()
    tid = _mk_tenant(seed, "kunde-a")
    seed.commit()
    admin = {"X-Dev-Roles": "admin"}

    client.patch(f"/tenants/{tid}", json={"code": "ssgch"}, headers=admin)
    sid = client.post(
        f"/tenants/{tid}/sites", json={"code": "000", "name": "RZ"}, headers=admin
    ).json()["id"]
    client.delete(f"/tenants/{tid}/sites/{sid}", headers=admin)

    s = session_factory()
    rows = s.query(AuditLog).order_by(AuditLog.id).all()
    assert [r.action for r in rows] == ["tenant.updated", "site.created", "site.deleted"]
    assert all(r.actor == "user:dev" for r in rows)
    assert audit.decode_detail(rows[0]) == {"tenant_id": tid, "code": "ssgch", "audit_reads": False}


def test_site_model_defaults(session_factory):
    s = session_factory()
    tid = _mk_tenant(s, "kunde-a")
    s.add(Site(tenant_id=tid, code="000", name="RZ01"))
    s.commit()
    got = s.query(Site).one()
    assert got.code == "000" and got.description is None
