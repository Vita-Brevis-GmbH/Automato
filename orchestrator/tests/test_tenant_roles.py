"""Rechte je Rolle im eigenen Mandanten (ADR 0068).

Benutzer eines Kunden-Realms mit `customer_operator` bzw. `customer_admin` im Mandanten, den
Automato fuer ihren Portal-Kunden angelegt hat, sind Mandanten-Benutzer: alles des Mandanten
sehen, Operator nur einlesen, Admin alles im Mandanten anpassen - nie andere Mandanten, nie die
Plattform. Geprueft je Rolle x Endpunktgruppe (positiv und negativ), dazu fremder und gemeinsamer
Mandant, der Weg ueber den Realm (Token), der Link „Ganze Plattform“ und die Vollstaendigkeit der
Positivliste.

Umgebung wie in `test_customers` (Mandant „Umgebung 1“ mit APIC, FortiGate fw-k und vCenter),
dazu ein fremder Mandant mit eigener FortiGate und Applikation, ein Agent des Mandanten und ein
Agent fuer alle Mandanten.
"""

import json
import re
from datetime import UTC, datetime

import pytest
from app.auth import capabilities as caps
from app.auth import tenant_routes
from app.auth.customer_routes import CUSTOMER_ROUTES
from app.auth.deps import NOT_FOR_TENANT_USERS, tenant_scope
from app.auth.principal import Principal
from app.config import Settings, get_settings
from app.models import AuditLog, CockpitTenant, Customer, IpamType, PortalCustomer, Tenant
from app.models.tenant import DnsType
from fastapi import HTTPException
from test_customer_users import _routes
from test_customers import ADMIN, _create, _seed, _system
from test_portal import ISSUER, _token, keys  # noqa: F401 - Fixture

OPERATOR, KADMIN = caps.CUSTOMER_OPERATOR, caps.CUSTOMER_ADMIN


def _env(client) -> dict:
    own, ids = _seed(client)
    other = client.post("/tenants", json={"name": "Fremd"}, headers=ADMIN).json()["id"]
    ids["fremd-fw"] = _system(client, other, "fremd-fw", "fortigate")
    kunde = _create(client, own, "Kunde A", [{"kind": "aci_tenant", "value": "TE_ka"}])
    agent = client.post("/agents", json={"name": "agent-a", "tenant_id": own}, headers=ADMIN)
    alle = client.post("/agents", json={"name": "agent-alle"}, headers=ADMIN)
    fremd_agent = client.post("/agents", json={"name": "agent-f", "tenant_id": other},
                              headers=ADMIN)  # fmt: skip
    app = client.post(f"/applications?tenant_id={other}", json={"name": "Fremd-App"},
                      headers=ADMIN)  # fmt: skip
    assert {r.status_code for r in (agent, alle, fremd_agent, app)} == {201}, app.text
    return {"own": own, "other": other, "ids": ids, "kunde": kunde["id"],
            "agent": agent.json()["id"], "alle": alle.json()["id"],
            "fremd_agent": fremd_agent.json()["id"], "fremd_app": app.json()["id"]}  # fmt: skip


def _user(env, role, own=True) -> dict:
    """Benutzer eines Kunden-Realms im Mandanten `own` (dev-Kopfzeilen wie aus dem Portal)."""
    return {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(env["own"]),
            "X-Dev-Customers": str(env["kunde"]), "X-Dev-Customer-Role": role,
            **({"X-Dev-Own-Tenant": "1"} if own else {})}  # fmt: skip


def _detail(r) -> str | None:
    return r.json().get("detail") if r.status_code == 403 else None


# --- Positivliste ---------------------------------------------------------------------------


def _role_routes(client) -> dict[tuple[str, str], str]:
    """(Methode, Pfad) -> Rolle laut `require_role` (viewer | operator | admin)."""
    import inspect

    out: dict[tuple[str, str], str] = {}

    def walk(dep, found):
        call = dep.call
        if call is not None and getattr(call, "__qualname__", "").startswith("require_role"):
            found.append(inspect.getclosurevars(call).nonlocals["required"])
        for d in dep.dependencies:
            walk(d, found)

    for route in _routes(client.app.routes, []):
        found: list[str] = []
        walk(route.dependant, found)
        for method in route.methods - {"HEAD", "OPTIONS"}:
            if found:
                out[(method, route.path)] = found[0]
    return out


def test_jeder_endpunkt_hat_einen_entscheid(client):
    """Jeder Endpunkt mit Rollenpruefung steht in genau einer Liste (Mandanten-Benutzer bzw.
    Plattform) - ein neuer Endpunkt braucht einen Entscheid; keine veralteten Eintraege. Ein
    Betreuer-Operator-Endpunkt oeffnet sich nie mit dem Leserecht, ein Admin-Endpunkt nur mit
    `mandant.manage`."""
    roles = _role_routes(client)
    allowed, platform = set(tenant_routes.TENANT_ROUTE_CAPABILITIES), tenant_routes.PLATFORM_ROUTES
    assert allowed & platform == set()
    assert set(roles) - allowed - platform == set(), "ohne Entscheid (ADR 0068)"
    assert (allowed | platform) - set(roles) == set(), "veraltet"
    for key, need in tenant_routes.TENANT_ROUTE_CAPABILITIES.items():
        if roles[key] == "operator":
            assert need != caps.TENANT_READ, key
        if roles[key] == "admin":
            assert need == caps.TENANT_MANAGE, key
    # Einlesen ist genau: Laeufe starten, Regeln einer Applikation einlesen (mit Quellen)
    assert {k for k, v in tenant_routes.TENANT_ROUTE_CAPABILITIES.items()
            if v == caps.TENANT_COLLECT} == {
        ("POST", "/jobs/collect"), ("POST", "/jobs/collect-all"),
        ("POST", "/applications/{application_id}/read-rules"),
        ("GET", "/applications/{application_id}/rule-sources")}  # fmt: skip


@pytest.mark.parametrize("role", [OPERATOR, KADMIN])
def test_fail_closed_fuer_alle_endpunkte(client, role):
    """Jeden Endpunkt einmal aufrufen: Plattform immer 403 „not available in the own tenant“,
    Anpassen fuer den Operator 403 „insufficient role“, sonst kommt die Anfrage am Tor vorbei."""
    env = _env(client)
    user = _user(env, role)
    roles = _role_routes(client)
    for (method, path), _required in sorted(roles.items()):
        r = client.request(method, re.sub(r"\{[^}]+\}", "999999", path),
                           params={"tenant_id": env["own"]}, headers=user)  # fmt: skip
        need = tenant_routes.capability(method, path)
        detail = _detail(r)
        if need is None:
            assert detail == NOT_FOR_TENANT_USERS, (method, path, r.status_code, r.text[:200])
        elif need not in caps.TENANT_ROLE_CAPABILITIES[role]:
            assert detail == "insufficient role", (method, path, r.status_code, r.text[:200])
        else:
            assert detail not in (NOT_FOR_TENANT_USERS, "insufficient role"), (method, path)


# --- je Rolle x Endpunktgruppe --------------------------------------------------------------

# lesen je Schicht (L1-L7), Wege, Abgleich, Laeufe, Quellsysteme, Standorte
LESEN_L1_L3 = ("/cabling/ports", "/cabling/vlans", "/routing/routes", "/routing/summary",
               "/static-routes", "/stp/overview", "/path-compare/sections", "/reconcile/summary",
               "/jobs", "/systems", "/routing/net-assignments")  # fmt: skip
LESEN_L4_7 = ("/rules", "/objects", "/nat", "/applications", "/findings", "/zones")


@pytest.mark.parametrize("role", [OPERATOR, KADMIN, "viewer", "triage", "editor"])
def test_lesen_je_schicht(client, role):
    env = _env(client)
    user = _user(env, role)
    for path in LESEN_L4_7:  # was heute jeder Kunden-Benutzer sieht
        r = client.get(path, params={"tenant_id": env["own"]}, headers=user)
        assert r.status_code == 200, (role, path, r.text)
    for path in LESEN_L1_L3 + (f"/tenants/{env['own']}/sites",):
        r = client.get(path, params={"tenant_id": env["own"]}, headers=user)
        if role in (OPERATOR, KADMIN):
            assert r.status_code == 200, (role, path, r.text)
        elif path.endswith("/sites"):
            assert r.status_code == 200, (role, path)  # Standorte lesen Kunden schon heute
        else:  # Kunden-Benutzer: wie bisher nur die Freigabeliste
            assert _detail(r) == "not available for customer users", (role, path, r.text)
    # Mandanten-Benutzer sehen alles des Mandanten (kein Kunden-Filter): auch Regeln anderer
    # Kunden; der Viewer nur die seines Kunden
    total = client.get("/rules", params={"tenant_id": env["own"]}, headers=ADMIN)
    mine = client.get("/rules", params={"tenant_id": env["own"]}, headers=user)
    if role in (OPERATOR, KADMIN):
        assert mine.headers["X-Total-Count"] == total.headers["X-Total-Count"]
    else:
        assert int(mine.headers["X-Total-Count"]) < int(total.headers["X-Total-Count"])


@pytest.mark.parametrize("role", [OPERATOR, KADMIN, "viewer", "editor"])
def test_einlesen_anstossen(client, role):
    env = _env(client)
    body = {"platform": "fortigate", "tenant_id": env["own"],
            "source_system_id": env["ids"]["fw-k"]}  # fmt: skip
    r = client.post("/jobs/collect", json=body, headers=_user(env, role))
    if role in (OPERATOR, KADMIN):
        assert r.status_code == 202, r.text
        run = r.json()["run_id"]
        # abbrechen ist anpassen - nur der Admin
        stop = client.post(f"/jobs/{run}/cancel", headers=_user(env, role))
        assert stop.status_code == (403 if role == OPERATOR else 200), stop.text
    else:
        assert _detail(r) == "not available for customer users"
    alle = client.post("/jobs/collect-all", json={"tenant_id": env["own"]},
                       headers=_user(env, role))  # fmt: skip
    assert (alle.status_code in (202, 409)) == (role in (OPERATOR, KADMIN)), alle.text


@pytest.mark.parametrize("role", [OPERATOR, KADMIN])
def test_anpassen_im_mandanten(client, role):
    env = _env(client)
    own, user = env["own"], _user(env, role)
    admin = role == KADMIN

    def check(r, created=200):
        if admin:
            assert r.status_code == created, r.text
        else:
            assert _detail(r) == "insufficient role", r.text
        return r

    app = check(client.post(f"/applications?tenant_id={own}", json={"name": "Neu"}, headers=user),
                201)  # fmt: skip
    if admin:
        r = client.patch(f"/applications/{app.json()['id']}", json={"owner": "Team A"},
                         headers=user)  # fmt: skip
        assert r.status_code == 200, r.text
    finding = {"tenant_id": own, "title": "Von Hand", "severity": "info"}
    made = check(client.post("/findings", json=finding, headers=user), 201)
    if admin:
        r = client.patch(f"/findings/{made.json()['id']}", json={"status": "ignored"}, headers=user)
        assert r.status_code == 200, r.text
    check(client.post(f"/tenants/{own}/sites", json={"code": "chzrh9", "name": "Zuerich"},
                      headers=user), 201)  # fmt: skip
    check(client.put("/modules/design", params={"tenant_id": own}, json={"enabled": True},
                     headers=user))  # fmt: skip
    check(client.get("/netbox-sync/targets", params={"tenant_id": own}, headers=user))
    check(client.post("/zones", json={"tenant_id": own, "name": "DMZ-Neu"}, headers=user), 201)
    # Agenten: der Admin sieht nur die seines Mandanten (Auswahl im Quellsystem)
    agents = check(client.get("/agents", headers=user))
    if admin:
        assert [a["name"] for a in agents.json()] == ["agent-a"]


def test_quellsysteme_nur_mit_eigenem_agenten(client):
    """Ein Mandanten-Admin bringt die Plattform nie dazu, eine Adresse seiner Wahl zu lesen:
    neue Quellsysteme nur mit einem Agenten seines Mandanten, nie NetBox; wohin gelesen wird,
    aendert er nur an solchen Systemen. Name, Zeitplan und Zugangsdaten gehen ueberall."""
    env = _env(client)
    own, user = env["own"], _user(env, KADMIN)

    def post(**extra):
        body = {
            "tenant_id": own,
            "name": f"fw-{len(extra)}-{extra.get('agent_id')}",
            "platform": "fortigate",
            "base_url": "https://fw.example.ch",
            **extra,
        }
        return client.post("/systems", json=body, headers=user)  # fmt: skip

    assert post().status_code == 403  # zentraler Worker
    assert post(agent_id=env["alle"]).status_code == 403  # Agent fuer alle Mandanten
    assert post(agent_id=env["fremd_agent"]).status_code in (403, 422)
    nb = client.post("/systems", json={"tenant_id": own, "name": "nb", "platform": "netbox",
                                       "base_url": "https://nb.example.ch",
                                       "agent_id": env["agent"]}, headers=user)  # fmt: skip
    assert nb.status_code == 403 and "NetBox" in nb.json()["detail"]
    mine = post(agent_id=env["agent"], token="geheim")
    assert mine.status_code == 201, mine.text
    assert "geheim" not in mine.text and mine.json()["has_credentials"] is True
    sid = mine.json()["id"]
    # eigenes System: Adresse und Agent im eigenen Mandanten aenderbar, nie zum zentralen Worker
    ok = client.patch(f"/systems/{sid}", json={"base_url": "https://fw2.example.ch"}, headers=user)
    assert ok.status_code == 200, ok.text
    assert client.patch(f"/systems/{sid}", json={"agent_id": None}, headers=user).status_code == 403
    # System des Betreibers (zentral gelesen): Adresse nein, Zeitplan, Name und Zugangsdaten ja
    central = env["ids"]["fw-k"]
    assert client.patch(f"/systems/{central}", json={"base_url": "https://10.0.0.1"},
                        headers=user).status_code == 403  # fmt: skip
    assert client.patch(f"/systems/{central}", json={"verify_tls": False},
                        headers=user).status_code == 403  # fmt: skip
    r = client.patch(f"/systems/{central}", json={"schedule": "0 3 * * *", "token": "neu"},
                     headers=user)  # fmt: skip
    assert r.status_code == 200 and r.json()["schedule"] == "0 3 * * *", r.text
    # loeschen im eigenen Mandanten
    assert client.delete(f"/systems/{sid}", headers=user).status_code == 200


@pytest.mark.parametrize("role", [OPERATOR, KADMIN])
def test_plattform_nie(client, role):
    env = _env(client)
    user = _user(env, role)
    for method, path, body in (
        ("GET", "/system/status", None), ("GET", "/system/health", None),
        ("GET", "/system/connections", None), ("GET", "/portal/customers", None),
        ("GET", "/kopplung/cockpit", None), ("POST", "/tenants", {"name": "X"}),
        ("PATCH", f"/tenants/{env['own']}", {"audit_reads": False}),
        ("DELETE", f"/tenants/{env['own']}", None), ("GET", "/customer-users", None),
        ("GET", "/naming-profiles", None), ("POST", "/agents", {"name": "neu"}),
        ("GET", "/agents/package/info", None),
        ("POST", "/customers", {"tenant_id": env["own"], "name": "Neu", "selectors": []}),
        ("DELETE", f"/customers/{env['kunde']}", None),
    ):  # fmt: skip
        r = client.request(method, path, json=body, params={"tenant_id": env["own"]},
                           headers=user)  # fmt: skip
        assert _detail(r) == NOT_FOR_TENANT_USERS, (method, path, r.status_code, r.text)
    me = client.get("/auth/me", headers=user).json()
    assert me["is_admin"] is False and me["tenant_ids"] == [env["own"]]


@pytest.mark.parametrize("role", [OPERATOR, KADMIN])
def test_fremder_mandant(client, role):
    env = _env(client)
    user, other = _user(env, role), env["other"]
    for path in LESEN_L4_7 + LESEN_L1_L3:
        r = client.get(path, params={"tenant_id": other}, headers=user)
        assert _detail(r) == "tenant not allowed", (path, r.text)
    assert client.get(f"/applications/{env['fremd_app']}", headers=user).status_code == 403
    assert client.get(f"/tenants/{other}/sites", headers=user).status_code == 403
    body = {"platform": "fortigate", "tenant_id": other, "source_system_id": env["ids"]["fremd-fw"]}
    assert client.post("/jobs/collect", json=body, headers=user).status_code == 403
    assert client.get("/tenants", headers=user).json()[0]["id"] == env["own"]
    assert len(client.get("/tenants", headers=user).json()) == 1
    if role == KADMIN:
        fremd = env["ids"]["fremd-fw"]
        assert client.patch(f"/systems/{fremd}", json={"name": "x"},
                            headers=user).status_code == 403  # fmt: skip
        assert client.delete(f"/systems/{fremd}", headers=user).status_code == 403
        assert client.delete(f"/applications/{env['fremd_app']}", headers=user).status_code == 403
        moved = client.patch(f"/systems/{env['ids']['fw-k']}", json={"tenant_id": other},
                             headers=user)  # fmt: skip
        assert moved.status_code == 403


@pytest.mark.parametrize("role", [OPERATOR, KADMIN])
def test_gemeinsamer_mandant_bleibt_kunden_sicht(client, role):
    """Im gemeinsamen Mandanten (nicht fuer den Portal-Kunden angelegt) bleibt alles wie bisher:
    Kunden-Filter, nur die Freigabeliste; der Operator liest dort nur."""
    env = _env(client)
    user = _user(env, role, own=False)
    me = client.get("/auth/me", headers=user).json()
    assert me["is_customer"] is True and me["mandant"] is None
    expected = ["doc.read"] if role == OPERATOR else sorted(caps.ROLE_CAPABILITIES[KADMIN])
    assert me["capabilities"] == expected
    assert _detail(client.get("/cabling/ports", headers=user)) == "not available for customer users"
    body = {"platform": "fortigate", "tenant_id": env["own"],
            "source_system_id": env["ids"]["fw-k"]}  # fmt: skip
    assert _detail(client.post("/jobs/collect", json=body, headers=user)) is not None


def test_lesezugriffe_immer_protokolliert(client, session_factory):
    env = _env(client)
    assert client.get("/rules", params={"tenant_id": env["own"]},
                      headers=_user(env, OPERATOR)).status_code == 200  # fmt: skip
    with session_factory() as s:
        rows = [r for r in s.query(AuditLog).all() if r.action == "read.rules"]
    assert len(rows) == 1 and rows[0].detail["mandant_rolle"] == OPERATOR


def test_ohne_mandant_keine_rechte():
    """Ein Mandanten-Benutzer ohne Mandanten (nie vorgesehen) wird nie zum „alle Mandanten“."""
    p = Principal(subject="x", roles=frozenset({"viewer"}), tenant_ids=None, tenant_role=KADMIN)
    assert not p.is_admin and p.tenant_capabilities == frozenset()
    with pytest.raises(HTTPException):
        tenant_scope(p, 5)
    with pytest.raises(HTTPException):
        tenant_scope(p, None)
    admin = Principal(subject="a", roles=frozenset({"admin"}), tenant_role=KADMIN, tenant_ids=(1,))
    assert not admin.is_admin  # `admin` nie zusammen mit einer Mandanten-Rolle


def test_kunden_routen_unveraendert():
    """Die Freigabeliste der Kunden-Benutzer bleibt (gemeinsame Mandanten)."""
    assert ("GET", "/cabling/ports") not in CUSTOMER_ROUTES
    assert caps.ROLE_CAPABILITIES[OPERATOR] == frozenset({caps.READ})
    assert caps.CUSTOMER_ROLES.index(OPERATOR) == caps.CUSTOMER_ROLES.index(caps.EDITOR) + 1
    assert caps.CUSTOMER_ROLES[-1] == KADMIN


# --- ueber den Realm (Token) ----------------------------------------------------------------


@pytest.fixture
def realm_client(client, session_factory, keys, monkeypatch, tmp_path):  # noqa: F811
    topo = {"oberflaeche": "https://automato.int.example.ch", "kunden": []}
    (tmp_path / "topologie.json").write_text(json.dumps(topo), encoding="utf-8")
    settings = Settings(auth_mode="oidc", oidc_issuer=ISSUER, oidc_audience="automato-api",
                        oidc_jwks_url=f"{ISSUER}/protocol/openid-connect/certs",
                        front_state_dir=str(tmp_path))  # fmt: skip
    client.settings = settings
    monkeypatch.setattr("app.auth.deps.get_settings", lambda: settings)
    client.app.dependency_overrides[get_settings] = lambda: client.settings

    class _Jwks:
        def __init__(self, url):
            self.url = url

        def get_signing_key_from_jwt(self, token):
            realm = self.url.split("/realms/")[1].split("/")[0]

            class _K:
                key = keys[realm].public_key()

            return _K()

    monkeypatch.setattr("app.auth.oidc._jwks_client", _Jwks)
    from app.auth import realms

    realms.invalidate()
    return client


def _realm_env(session_factory, eigen: bool, quelle: str = "cockpit") -> tuple[int, int, int]:
    with session_factory() as s:
        t = Tenant(name="Kunde A", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
        fremd = Tenant(name="Fremd", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
        s.add_all([t, fremd])
        s.flush()
        c = Customer(tenant_id=t.id, name="Kunde A")
        s.add(c)
        s.flush()
        if eigen:
            s.add(CockpitTenant(short_name="kunde-a", tenant_id=t.id, created_at=datetime.now(UTC)))
        users = [{"benutzer": f"{r}@example.ch", "rolle": r} for r in caps.CUSTOMER_ROLES]
        users.append({"benutzer": "anna@betrieb", "rolle": KADMIN})
        s.add(PortalCustomer(short_name="kunde-a", name="Kunde A", doc=True, realm="kunde-a",
                             tenant_id=t.id, customer_ids=[c.id], links=[], source=quelle,
                             users=users))  # fmt: skip
        s.commit()
        return t.id, c.id, fremd.id


def _me(client, keys, name, **claims):  # noqa: F811
    token = _token(keys["kunde-a"], "kunde-a", preferred_username=name, **claims)
    auth = {"Authorization": f"Bearer {token}"}
    r = client.get("/auth/me", headers=auth)
    assert r.status_code == 200, r.text
    return r.json(), auth


@pytest.mark.parametrize("role", caps.CUSTOMER_ROLES)
def test_realm_im_eigenen_mandanten(realm_client, session_factory, keys, role):  # noqa: F811
    tid, cid, fremd = _realm_env(session_factory, eigen=True)
    # der Realm meldet Admin und fremde Mandanten - zaehlt nicht (Rolle nur aus dem Cockpit)
    me, auth = _me(realm_client, keys, f"{role}@example.ch",
                   realm_access={"roles": ["admin"]}, tenant_ids=[fremd])  # fmt: skip
    assert me["is_admin"] is False and me["tenant_ids"] == [tid]
    if role in (OPERATOR, KADMIN):
        assert me["is_customer"] is False and me["customer_ids"] is None
        assert me["mandant"] == {"mandant": tid, "rolle": role,
                                 "rechte": sorted(caps.TENANT_ROLE_CAPABILITIES[role])}  # fmt: skip
        assert realm_client.get("/cabling/ports", headers=auth).status_code == 200
    else:
        assert me["is_customer"] is True and me["mandant"] is None and me["customer_role"] == role
        assert realm_client.get("/cabling/ports", headers=auth).status_code == 403
    assert me["betreiber"] is False and me["plattform_url"] == ""
    assert realm_client.get("/portal/customers", headers=auth).status_code == 403
    assert realm_client.get("/rules", params={"tenant_id": fremd}, headers=auth).status_code == 403


def test_realm_gemeinsamer_mandant_und_ohne_cockpit(realm_client, session_factory, keys):  # noqa: F811
    _realm_env(session_factory, eigen=False)
    me, _auth = _me(realm_client, keys, f"{KADMIN}@example.ch")
    assert me["is_customer"] is True and me["mandant"] is None
    with session_factory() as s:  # eigener Mandant, aber nicht aus dem Cockpit: Kunden-Sicht
        pc = s.query(PortalCustomer).one()
        s.add(CockpitTenant(short_name="kunde-a", tenant_id=pc.tenant_id,
                            created_at=datetime.now(UTC)))  # fmt: skip
        pc.source = "oberflaeche"
        s.commit()
    from app.auth import realms

    realms.invalidate()
    me, _auth = _me(realm_client, keys, f"{KADMIN}@example.ch")
    assert me["is_customer"] is True and me["mandant"] is None


def test_betreiber_link_ganze_plattform(realm_client, session_factory, keys):  # noqa: F811
    _realm_env(session_factory, eigen=True)
    me, auth = _me(realm_client, keys, "anna@betrieb")
    assert me["betreiber"] is True and me["mandant"]["rolle"] == KADMIN
    assert me["plattform_url"] == "https://automato.int.example.ch"
    # der Link gibt keine Rechte: Plattform bleibt zu
    assert realm_client.get("/system/status", headers=auth).status_code == 403
    # kein Betreiber: kein Link
    me, _auth = _me(realm_client, keys, f"{KADMIN}@example.ch")
    assert me["betreiber"] is False and me["plattform_url"] == ""
    # eigene Instanz (ADR 0061): kein Link
    realm_client.settings = realm_client.settings.model_copy(update={"instanz_kunde": "kunde-a"})
    me, _auth = _me(realm_client, keys, "anna@betrieb")
    assert me["betreiber"] is True and me["plattform_url"] == ""


def test_dev_kopfzeile_nur_mit_einem_mandanten(client):
    env = _env(client)
    two = {**_user(env, KADMIN), "X-Dev-Tenants": f"{env['own']},{env['other']}"}
    me = client.get("/auth/me", headers=two).json()
    assert me["is_customer"] is True and me["mandant"] is None
