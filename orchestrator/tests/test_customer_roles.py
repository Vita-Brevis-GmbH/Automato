"""Feinere Rollen der Kunden-Benutzer (ADR 0018 Ergaenzung „Feinere Rollen“).

Kunden-Rollen viewer < triage < editor < customer_admin, je Kunde, ueber Capabilities
(`auth/capabilities.py`) und die Freigabeliste je Methode und Pfad (`auth/customer_routes.py`).
Umgebung wie in `test_customer_users`: Kunde A = TE_ka + 10.10.1.0/24 (Applikation „Web“ mit
EPG_web), Kunde B = TE_kb + 10.10.2.0/24 (Applikation „App“ mit EPG_app), geteilt = common.
"""

import pytest
from app.auth import assignments, customer_routes, oidc
from app.auth import capabilities as caps
from app.auth.principal import Principal
from app.config import Settings
from app.models import AuditLog, CustomerUser
from test_customer_users import _by_name, _env
from test_customers import ADMIN, DNS, OP, WEB


def _cust(tid, *customers, role="viewer"):
    """dev: Kunden-Benutzer mit Kunden-Rolle (wie Claim `customer_role`)."""
    return {"X-Dev-Tenants": str(tid), "X-Dev-Customers": ",".join(str(c) for c in customers),
            "X-Dev-Customer-Role": role}  # fmt: skip


def _apps(client, tid) -> dict[str, dict]:
    return {a["name"]: a for a in client.get("/applications", params={"tenant_id": tid}).json()}


def _assign(client, customer_id, login, role, headers=ADMIN):
    body = {"customer_id": customer_id, "login": login, "role": role}
    return client.post("/customer-users", json=body, headers=headers)


def _audit(session_factory, action) -> list[AuditLog]:
    s = session_factory()
    rows = [r for r in s.query(AuditLog).order_by(AuditLog.id).all() if r.action == action]
    s.close()
    return rows


def test_rollen_und_capabilities():
    """Tabelle Rolle -> Capabilities: jede Rolle schliesst die vorherige ein; operator aus 0.53.0
    = triage (kein Recht mehr als bisher); unbekannt = viewer; `admin` ist keine Kunden-Rolle."""
    assert caps.capabilities("viewer") == {caps.READ}
    assert caps.capabilities("triage") == {caps.READ, caps.TRIAGE}
    assert caps.capabilities("editor") == {caps.READ, caps.TRIAGE, caps.APPS_EDIT}
    assert caps.capabilities("customer_admin") == set(caps.CAPABILITIES)
    assert caps.capabilities("operator") == caps.capabilities("triage")
    for unknown in ("admin", "superuser", "", None, "EDITOR "):
        expected = "editor" if unknown == "EDITOR " else "viewer"
        assert caps.normalize(unknown) == expected
    assert caps.higher("triage", "editor") == "editor" and caps.higher(None, "operator") == "triage"
    assert caps.capabilities(None) == frozenset()
    # Freigabeliste: schreibende Methoden nie mit dem Leserecht
    for (method, path), need in customer_routes.CUSTOMER_ROUTE_CAPABILITIES.items():
        assert need in caps.CAPABILITIES, (method, path)
        if method != "GET":
            assert need != caps.READ, (method, path)
    assert customer_routes.capability("get", "/rules") == caps.READ
    assert customer_routes.capability("DELETE", "/applications/{application_id}") is None


def test_viewer_triage_editor_je_endpunkt(client, session_factory):
    tid, ids, a, b, shared = _env(client)
    apps = _apps(client, tid)
    web, other = apps["Web"], apps["App"]
    url = f"/applications/{web['id']}"
    patch = {"external_name": "Shop Kunde A"}

    # viewer: liest, pflegt nichts; die Applikation ist fuer ihn nicht bearbeitbar
    viewer = _cust(tid, a)
    listed = client.get("/applications", params={"tenant_id": tid}, headers=viewer).json()
    assert [(x["name"], x["editable"]) for x in listed] == [("Web", False)]
    assert client.patch(url, json=patch, headers=viewer).status_code == 403
    own = client.get("/findings", params={"tenant_id": tid}, headers=viewer).json()
    r = client.patch(f"/findings/{own[0]['id']}", json={"note": "x"}, headers=viewer)
    assert r.status_code == 403

    # triage: quittiert eigene Findings, pflegt keine Applikationen
    triage = _cust(tid, a, role="triage")
    r = client.patch(f"/findings/{own[0]['id']}", json={"status": "acknowledged"}, headers=triage)
    assert r.status_code == 200, r.text
    assert client.patch(url, json=patch, headers=triage).json()["detail"] == "insufficient role"

    # editor: Stammdaten der eigenen Applikation
    editor = _cust(tid, a, role="editor")
    assert client.get(url, headers=editor).json()["editable"] is True
    r = client.patch(url, json=patch, headers=editor)
    assert r.status_code == 200, r.text
    assert r.json()["external_name"] == "Shop Kunde A" and r.json()["editable"] is True
    assert client.get(url).json()["editable"] is None  # Betreuer: die Rolle gilt
    # fremde Applikation nicht auffindbar, Betreuer-Pflege nicht freigegeben
    r = client.patch(f"/applications/{other['id']}", json=patch, headers=editor)
    assert r.status_code == 404
    for method, path, body in (
        ("DELETE", url, None),
        ("POST", f"/applications?tenant_id={tid}", {"name": "Neu"}),
        ("POST", f"{url}/members", {"members": [{"kind": "epg", "ref": WEB}]}),
        ("POST", f"/applications/import?tenant_id={tid}", {"csv": "x"}),
        ("POST", "/applications/delete", {"ids": [web["id"]]}),
    ):
        r = client.request(method, path, json=body, headers=editor)
        assert r.status_code == 403, (method, path, r.status_code)
        assert r.json()["detail"] == "not available for customer users"
    assert client.get(url).json()["external_name"] == "Shop Kunde A"

    # Regeln zuordnen bzw. ausschliessen: nur Regeln des eigenen Ausschnitts
    ct_web = _by_name(client, "/rules", tid, "CT_web")
    b_dns = _by_name(client, "/rules", tid, "b-dns")
    r = client.post(f"{url}/rules", json={"rule_id": ct_web["id"], "exclude": True}, headers=editor)
    assert r.status_code == 200, r.text
    norule = next(m for m in r.json()["members"] if m["kind"] == "norule")
    r = client.post(f"{url}/rules", json={"rule_id": b_dns["id"]}, headers=editor)
    assert r.status_code == 404  # Regel von Kunde B
    # rueckgaengig: nur Regel-Mitglieder, nicht die EPG
    epg = next(m for m in client.get(url).json()["members"] if m["kind"] == "epg")
    assert client.delete(f"{url}/members/{epg['id']}", headers=editor).status_code == 403
    assert client.delete(f"{url}/members/{norule['id']}", headers=editor).status_code == 200
    assert [m["kind"] for m in client.get(url).json()["members"]] == ["epg"]

    # Regeln je Kategorie ausblenden (mit Begruendung) und wieder einblenden
    hide = {"rule_ids": [ct_web["id"]], "category": None, "reason": "nur Monitoring"}
    r = client.post(f"{url}/hidden", json=hide, headers=editor)
    assert r.status_code == 201, r.text
    hide_id = r.json()[0]["id"]
    foreign = {**hide, "rule_ids": [b_dns["id"]]}
    assert client.post(f"{url}/hidden", json=foreign, headers=editor).status_code == 404
    assert client.delete(f"{url}/hidden/{hide_id}", headers=triage).status_code == 403
    assert client.delete(f"{url}/hidden/{hide_id}", headers=editor).status_code == 204

    # Audit wie bisher, mit der Kunden-Rolle des Handelnden
    updated = _audit(session_factory, "application.updated")[-1]
    assert updated.detail["customer_roles"] == {str(a): "editor"}
    assert _audit(session_factory, "application.rule_hidden")[-1].detail["customer_roles"] == {
        str(a): "editor"}  # fmt: skip
    triaged = _audit(session_factory, "finding.triaged")[-1]
    assert triaged.detail["customer_roles"] == {str(a): "triage"}
    assert updated.actor == "user:dev-customer"


def test_nur_applikationen_ganz_beim_kunden(client):
    """Editor pflegt nur Applikationen, deren Mitglieder ganz zu seinen Kunden gehoeren - eine
    Applikation mit einer geteilten EPG (common) bzw. einer fremden Regel bleibt den Betreuern."""
    tid, ids, a, b, shared = _env(client)
    members = [{"kind": "epg", "ref": WEB}, {"kind": "epg", "ref": DNS}]
    body = {"name": "Mit DNS", "members": members}
    mixed = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP).json()
    editor = _cust(tid, a, role="editor")
    rows = client.get("/applications", params={"tenant_id": tid}, headers=editor).json()
    assert {x["name"]: x["editable"] for x in rows} == {"Web": True, "Mit DNS": False}
    r = client.patch(f"/applications/{mixed['id']}", json={"owner": "x"}, headers=editor)
    assert r.status_code == 403
    assert r.json()["detail"].startswith("Applikation liegt nicht ganz beim Kunden")
    r = client.patch(f"/applications/{mixed['id']}", json={"owner": "x"},
                     headers={**editor, "Accept-Language": "en"})  # fmt: skip
    assert r.json()["detail"].startswith("application not entirely within the customer")
    # Betreuer ordnet eine Regel von Kunde B zu: ab dann nicht mehr ganz beim Kunden
    web = _apps(client, tid)["Web"]
    b_dns = _by_name(client, "/rules", tid, "b-dns")
    r = client.post(f"/applications/{web['id']}/rules", json={"rule_id": b_dns["id"]}, headers=OP)
    assert r.status_code == 200
    assert client.patch(f"/applications/{web['id']}", json={"owner": "x"},
                        headers=editor).status_code == 403  # fmt: skip


def test_rollen_je_kunde_aus_der_zuordnung(client):
    """Die Rolle gilt je Kunde: Triage fuer B erlaubt keine Triage bei A, Editor fuer B pflegt
    keine Applikation von A."""
    tid, ids, a, b, shared = _env(client)
    login = "multi@example.ch"
    assert _assign(client, a, login, "viewer").status_code == 201
    assert _assign(client, b, login, "editor").status_code == 201
    user = {"X-Dev-User": login}
    me = client.get("/auth/me", headers=user).json()
    assert me["customer_roles"] == {str(a): "viewer", str(b): "editor"}
    assert me["customer_role"] == "editor" and caps.APPS_EDIT in me["capabilities"]
    apps = _apps(client, tid)
    patch = {"owner": "Team B"}
    r = client.patch(f"/applications/{apps['App']['id']}", json=patch, headers=user)
    assert r.status_code == 200, r.text
    r = client.patch(f"/applications/{apps['Web']['id']}", json=patch, headers=user)
    assert r.status_code == 403

    def findings(headers) -> set[int]:
        rows = client.get("/findings", params={"tenant_id": tid, "limit": 500}, headers=headers)
        return {f["id"] for f in rows.json()}

    only_a = findings(_cust(tid, a)) - findings(_cust(tid, b))
    only_b = findings(_cust(tid, b)) - findings(_cust(tid, a))
    assert only_a and only_b and only_a | only_b <= findings(user)
    r = client.patch(f"/findings/{min(only_b)}", json={"note": "B"}, headers=user)
    assert r.status_code == 200, r.text
    r = client.patch(f"/findings/{min(only_a)}", json={"note": "A"}, headers=user)
    assert r.status_code == 403 and r.json()["detail"] == "insufficient role"


def test_kunden_admin_ordnet_benutzer_des_eigenen_kunden_zu(client, session_factory):
    tid, ids, a, b, shared = _env(client)
    chef = "chef.a@example.ch"
    assert _assign(client, a, chef, "customer_admin").status_code == 201
    assert _assign(client, b, "kollege.b@example.ch", "viewer").status_code == 201
    me = {"X-Dev-User": chef}
    who = client.get("/auth/me", headers=me).json()
    assert who["is_customer"] and not who["is_admin"]
    assert caps.USERS_MANAGE in who["capabilities"] and who["customer_role"] == "customer_admin"

    # nur die Zuordnungen des eigenen Kunden, ohne Namen der Betreuer
    listed = client.get("/customer-users", params={"tenant_id": tid}, headers=me).json()
    assert [(x["login"], x["created_by"]) for x in listed] == [(chef, None)]
    r = _assign(client, a, "Mitarbeiter@Example.ch", "editor", headers=me)
    assert r.status_code == 201, r.text
    added = r.json()
    assert (added["login"], added["role"], added["origin"]) == ("mitarbeiter@example.ch", "editor",
                                                                "customer")  # fmt: skip
    assert added["created_by"] == chef
    # nie andere Kunden, nie globale Rollen, nie das eigene Konto
    assert _assign(client, b, "x@example.ch", "viewer", headers=me).status_code == 404
    assert _assign(client, a, "x@example.ch", "admin", headers=me).status_code == 422
    assert _assign(client, a, "x@example.ch", "operator", headers=me).status_code == 201  # = triage
    assert _assign(client, a, chef, "viewer", headers=me).status_code == 409
    own = next(x for x in listed if x["login"] == chef)
    assert client.patch(f"/customer-users/{own['id']}", json={"role": "viewer"},
                        headers=me).status_code == 409  # fmt: skip
    assert client.delete(f"/customer-users/{own['id']}", headers=me).status_code == 409
    other_b = next(x for x in client.get("/customer-users", params={"tenant_id": tid}).json()
                   if x["customer_id"] == b)  # fmt: skip
    assert client.patch(f"/customer-users/{other_b['id']}", json={"role": "editor"},
                        headers=me).status_code == 404  # fmt: skip
    # eigene Kunden-Benutzer aendern und entfernen
    r = client.patch(f"/customer-users/{added['id']}", json={"role": "customer_admin"}, headers=me)
    assert r.status_code == 200 and r.json()["role"] == "customer_admin"
    assert client.delete(f"/customer-users/{added['id']}", headers=me).status_code == 204
    # Editor bzw. Viewer verwalten keine Benutzer; Betreuer-Verwaltung bleibt verschlossen
    editor = _cust(tid, a, role="editor")
    r = client.get("/customer-users", params={"tenant_id": tid}, headers=editor)
    assert r.status_code == 403
    assert client.get("/systems", params={"tenant_id": tid}, headers=me).status_code == 403
    assert client.post("/customers", json={"tenant_id": tid, "name": "X", "selectors": []},
                       headers=me).status_code == 403  # fmt: skip

    # Audit mit Herkunft, Rolle und den Kunden-Rollen des Handelnden
    assigned = [r for r in _audit(session_factory, "customer_user.assigned")
                if r.detail["login"] == "mitarbeiter@example.ch"]  # fmt: skip
    assert assigned[0].detail["origin"] == "customer" and assigned[0].actor == f"user:{chef}"
    assert assigned[0].detail["customer_roles"] == {str(a): "customer_admin"}
    changed = _audit(session_factory, "customer_user.changed")[-1]
    assert changed.detail["previous_role"] == "editor"
    assert changed.detail["role"] == "customer_admin"


def test_zuordnung_eines_kunden_admins_sperrt_keine_betreuer(client):
    """Eine Zuordnung des Kunden-Admins wirkt auf Benutzer ohne Betreuer-Rolle (neue Kollegen),
    nie auf Betreuer - sonst koennte er einen Betreuer zum Kunden-Benutzer machen."""
    tid, ids, a, b, shared = _env(client)
    assert _assign(client, a, "chef.a@example.ch", "customer_admin").status_code == 201
    me = {"X-Dev-User": "chef.a@example.ch"}
    for login in ("betreuer@example.ch", "neu@example.ch"):
        assert _assign(client, a, login, "viewer", headers=me).status_code == 201
    staff = {"X-Dev-User": "betreuer@example.ch", "X-Dev-Roles": "operator",
             "X-Dev-Tenants": str(tid)}  # fmt: skip
    assert client.get("/systems", params={"tenant_id": tid}, headers=staff).status_code == 200
    assert client.get("/auth/me", headers=staff).json()["is_customer"] is False
    fresh = client.get("/auth/me", headers={"X-Dev-User": "neu@example.ch"}).json()
    assert fresh["is_customer"] and fresh["customer_ids"] == [a]
    # vom Betreuer angelegt: schraenkt wie bisher auch Betreuer ein
    assert _assign(client, a, "betreuer@example.ch", "viewer").status_code == 409  # gibt es schon
    principal = Principal(subject="s", roles=frozenset({"operator"}), logins=("s",))
    assert assignments.apply(principal, [(a, tid, "viewer", "customer")]) is principal
    restricted = assignments.apply(principal, [(a, tid, "viewer", "staff")])
    assert restricted.is_customer and restricted.customer_roles == ((a, "viewer"),)


def test_claim_und_zuordnung_hoehere_rolle():
    claim = Principal(subject="k", roles=frozenset({"viewer"}), tenant_ids=(1,), customer_ids=(3,),
                      logins=("k",), customer_source="claim",
                      customer_roles=((3, "customer_admin"),))  # fmt: skip
    p = assignments.apply(claim, [(3, 1, "viewer", "staff"), (4, 1, "triage", "customer")])
    assert dict(p.customer_roles) == {3: "customer_admin", 4: "triage"}
    assert p.can(caps.USERS_MANAGE, 3) and not p.can(caps.USERS_MANAGE, 4)
    assert p.customers_with(caps.TRIAGE) == (3, 4) and p.customers_with(caps.APPS_EDIT) == (3,)
    low = Principal(subject="k", roles=frozenset({"viewer"}), tenant_ids=(1,), customer_ids=(3,),
                    customer_source="claim", customer_roles=((3, "viewer"),))  # fmt: skip
    p = assignments.apply(low, [(3, 1, "editor", "staff")])
    assert p.customer_role(3) == "editor" and "operator" in p.roles
    # Betreuer sind nie Kunden-Benutzer mit Capabilities
    assert Principal(subject="b", roles=frozenset({"admin"})).capabilities == frozenset()


def _settings() -> Settings:
    return Settings(
        auth_mode="oidc",
        oidc_jwks_url="https://kc/certs",
        oidc_roles_claim="realm_access.roles",
        oidc_tenants_claim="tenant_ids",
    )


@pytest.mark.parametrize(
    ("roles", "claim", "expected"),
    [
        (["customer"], "editor", "editor"),
        (["customer"], ["viewer", "customer_admin"], "customer_admin"),
        (["customer"], "admin", "viewer"),  # nie globaler Admin: unbekannt = viewer
        (["customer"], "chef", "viewer"),
        (["operator"], None, "triage"),  # wie bis 0.87.0
        (["customer"], None, "viewer"),
        (["admin"], None, None),  # ohne Kunden-Rolle: keine Rechte
    ],
)
def test_oidc_claim_customer_role(roles, claim, expected):
    claims = {"sub": "k", "tenant_ids": [1], "customer_ids": [3, 4],
              "realm_access": {"roles": roles}}  # fmt: skip
    if claim is not None:
        claims["customer_role"] = claim
    p = oidc.claims_to_principal(claims, _settings())
    assert p.is_customer and not p.is_admin
    assert p.customer_role(3) == expected and p.customer_role(4) == expected
    assert p.capabilities == caps.capabilities(expected)


def test_unbekannte_rolle_gilt_als_viewer(client, session_factory):
    tid, ids, a, b, shared = _env(client)
    assert _assign(client, a, "alt@example.ch", "viewer").status_code == 201
    s = session_factory()
    s.query(CustomerUser).filter_by(login="alt@example.ch").update({"role": "superuser"})
    s.commit()
    s.close()
    user = {"X-Dev-User": "alt@example.ch"}
    assert client.get("/auth/me", headers=user).json()["customer_roles"] == {str(a): "viewer"}
    assert client.get("/rules", params={"tenant_id": tid}, headers=user).status_code == 200
    own = client.get("/findings", params={"tenant_id": tid}, headers=user).json()
    r = client.patch(f"/findings/{own[0]['id']}", json={"note": "x"}, headers=user)
    assert r.status_code == 403
    # dev ohne Kunden-Rolle (nur admin): keine Rechte, nichts lesbar
    nothing = {"X-Dev-Roles": "admin", "X-Dev-Tenants": str(tid), "X-Dev-Customers": str(a)}
    assert client.get("/rules", params={"tenant_id": tid}, headers=nothing).status_code == 403


def test_schreibender_endpunkt_nie_mit_leserecht(client, monkeypatch):
    """Selbst ein falscher Eintrag in der Freigabeliste oeffnet keinen schreibenden Endpunkt mit
    dem Leserecht: `require_role` ueber viewer verlangt eine schreibende Capability."""
    tid, ids, a, b, shared = _env(client)
    web = _apps(client, tid)["Web"]
    wrong = dict(customer_routes.CUSTOMER_ROUTE_CAPABILITIES)
    wrong[("DELETE", "/applications/{application_id}")] = caps.READ
    monkeypatch.setattr(customer_routes, "CUSTOMER_ROUTE_CAPABILITIES", wrong)
    r = client.delete(f"/applications/{web['id']}", headers=_cust(tid, a, role="customer_admin"))
    assert r.status_code == 403 and r.json()["detail"] == "not available for customer users"
    assert client.get(f"/applications/{web['id']}").status_code == 200


def test_auth_me_capabilities_der_betreuer(client):
    me = client.get("/auth/me", headers=ADMIN).json()
    assert set(me["capabilities"]) == set(caps.CAPABILITIES) and me["customer_role"] is None
    op = client.get("/auth/me", headers=OP).json()
    assert caps.USERS_MANAGE not in op["capabilities"] and caps.APPS_EDIT in op["capabilities"]
