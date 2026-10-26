"""Kunden-Benutzer (ADR 0018, Schritt 3): serverseitige Einschraenkung auf den Ausschnitt ihrer
Kunden, fail-closed fuer alle nicht freigegebenen Endpunkte.

Umgebung wie in `test_customers` (APIC mit TE_ka, TE_kb, common; FortiGate fw-k; vCenter), dazu
eine FortiGate fw-u mit Objekten und NAT: Kunde A = TE_ka + 10.10.1.0/24, Kunde B = TE_kb +
10.10.2.0/24, geteilt = common (mit dem DNS-Netz 10.0.53.0/24).
"""

import copy
import re

import pytest
from app.api.customer_filter import finding_detail
from app.auth import oidc
from app.auth.customer_routes import CUSTOMER_ROUTES
from app.auth.principal import Principal
from app.config import Settings
from app.models import AuditLog
from fastapi.routing import APIRoute
from test_customers import ADMIN, APP, OP, VC, WEB, _create, _deliver, _policy, _seed, _system


def _obj(name, value):
    return {"name": name, "category": "address", "obj_type": "ipmask", "values": [value],
            "vdom": "root"}  # fmt: skip


def _refs(rule, src, dst):
    return {**rule, "source_refs": src, "destination_refs": dst}


FWU = {
    "status": "completed",
    "objects": [
        _obj("H_a01", "10.10.1.5"),
        _obj("H_b01", "10.10.2.5"),
        _obj("N_dns", "10.0.53.0/24"),
    ],
    "rules": [
        _refs(_policy(21, "a-dns", ["10.10.1.5"], ["10.0.53.0/24"]), ["H_a01"], ["N_dns"]),
        _refs(_policy(22, "b-dns", ["10.10.2.5"], ["10.0.53.0/24"]), ["H_b01"], ["N_dns"]),
        {**_policy(23, "b-leer", ["10.10.2.6"], []), "destinations": []},
    ],
    "nat_rules": [
        {"source_platform": "fortigate", "external_id": "VIP_a", "name": "VIP_a",
         "nat_type": "dnat_vip", "ext_ip": "198.51.100.30", "mapped_ip": "10.10.1.5"},
        {"source_platform": "fortigate", "external_id": "VIP_b", "name": "VIP_b",
         "nat_type": "dnat_vip", "ext_ip": "198.51.100.31", "mapped_ip": "10.10.2.5"},
    ],
}  # fmt: skip


def _env(client):
    tid, ids = _seed(client)
    ids["fw-u"] = _system(client, tid, "fw-u", "fortigate")
    _deliver(client, tid, ids["fw-u"], "fortigate", FWU)
    a = _create(
        client,
        tid,
        "Kunde A",
        [{"kind": "aci_tenant", "value": "TE_ka"}, {"kind": "prefix", "value": "10.10.1.0/24"}],
    )
    b = _create(
        client,
        tid,
        "Kunde B",
        [{"kind": "aci_tenant", "value": "TE_kb"}, {"kind": "prefix", "value": "10.10.2.0/24"}],
    )
    shared = _create(client, tid, "Geteilt", [{"kind": "aci_tenant", "value": "common"}],
                     shared=True)  # fmt: skip
    for name, ref in (("Web", WEB), ("App", APP)):
        body = {"name": name, "members": [{"kind": "epg", "ref": ref}]}
        r = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP)
        assert r.status_code == 201, r.text
    return tid, ids, a["id"], b["id"], shared["id"]


def _user(tid, *customers, roles="viewer"):
    return {"X-Dev-Roles": roles, "X-Dev-Tenants": str(tid),
            "X-Dev-Customers": ",".join(str(c) for c in customers)}  # fmt: skip


def _by_name(client, path, tid, name, key=None):
    r = client.get(path, params={"tenant_id": tid, "limit": 500}, headers=ADMIN)
    rows = r.json()[key] if key else r.json()
    return next(x for x in rows if x["name"] == name)


def test_kunden_benutzer_sehen_nur_ihren_ausschnitt(client):
    tid, ids, a, b, shared = _env(client)
    ua = _user(tid, a)

    def get(path, status=200, **params):
        r = client.get(path, params=params, headers=ua)
        assert r.status_code == status, (path, r.status_code, r.text)
        return r

    # Regeln: nur die eigenen - auch ohne tenant_id (auf den einzigen Mandanten verengt); eine
    # Regel von B zum geteilten DNS (b-dns) bzw. von B zu common (CT_dns) gehoert nicht dazu
    names = {x["name"] for x in get("/rules").json()}
    assert names == {"CT_web", "CT_app", "web-to-backend", "a-dns"}
    assert get("/rules", tenant_id=tid).headers["X-Total-Count"] == "4"
    assert get("/rules/summary", tenant_id=tid).json()["total"] == 4
    b_dns = _by_name(client, "/rules", tid, "b-dns")
    get(f"/rules/{b_dns['id']}", 404)
    assert get(f"/rules/{_by_name(client, '/rules', tid, 'a-dns')['id']}").json()["name"] == "a-dns"
    get("/rules", 403, tenant_id=tid, customer_id=b)  # fremder Kunde
    assert {x["name"] for x in get("/rules", tenant_id=tid, customer_id=a).json()} == names

    # Kunden: nur die eigenen (der geteilte gilt mit, wird aber nicht gezeigt)
    assert [c["name"] for c in get("/customers", tenant_id=tid).json()] == ["Kunde A"]
    get(f"/customers/{b}/members", 404, kind="rule")
    get(f"/customers/{shared}/members", 404, kind="rule")
    assert "a-dns" in {m["name"] for m in get(f"/customers/{a}/members", kind="rule").json()}

    # Objekte: eigene und geteilte; die Regel-Zahl nennt nur die eigenen Regeln
    objs = {o["name"]: o for o in get("/objects", tenant_id=tid).json()["rows"]}
    assert set(objs) == {"H_a01", "N_dns", "VIP_a → 10.10.1.5"}  # mit dem Ziel der VIP
    assert objs["N_dns"]["used_in_rules"] == 1
    assert _by_name(client, "/objects", tid, "N_dns", "rows")["used_in_rules"] == 2  # Betreuer
    detail = get(f"/objects/{objs['N_dns']['id']}").json()
    assert detail["rule_count"] == 1 and [r["name"] for r in detail["rules"]] == ["a-dns"]
    get(f"/objects/{_by_name(client, '/objects', tid, 'H_b01', 'rows')['id']}", 404)

    # NAT ueber die interne IP
    nat = get("/nat", tenant_id=tid).json()
    assert [n["name"] for n in nat] == ["VIP_a"]
    get(f"/nat/{_by_name(client, '/nat', tid, 'VIP_b')['id']}", 404)
    assert get(f"/nat/{nat[0]['id']}").json()["name"] == "VIP_a"

    # Quellsysteme: nur Bezeichnungen der Systeme mit eigenen Regeln, NAT bzw. Objekten
    # - ohne Geraetenamen (0.58.0): ACI = apic, Firewall 1 = fw-k, Firewall 2 = fw-u
    labels = {s["name"]: s for s in get("/systems/labels", tenant_id=tid).json()}
    assert set(labels) == {"ACI", "Firewall 1", "Firewall 2"}
    assert labels["Firewall 1"]["vdoms"] == ["root"] and "base_url" not in labels["Firewall 1"]
    get("/systems", 403, tenant_id=tid)
    full = client.get("/systems/labels", params={"tenant_id": tid}).json()  # Betreuer
    assert {s["name"] for s in full} == {"apic", "fw-k", "fw-u", "vc"}
    assert "kunde-b" in next(s for s in full if s["name"] == "fw-k")["vdoms"]

    # VMs, Portgruppen, Systeme, Applikationen
    assert [v["name"] for v in get("/vms", tenant_id=tid).json()["rows"]] == ["web01-007-ka"]
    pgs = client.get("/portgroups", params={"tenant_id": tid}, headers=ADMIN).json()
    pg_b = next(p for p in pgs if p["name"] == "TE_kb|AP_100|EPG_app")
    get(f"/portgroups/{pg_b['id']}", 404)
    assert [p["name"] for p in get("/portgroups", tenant_id=tid).json()] == ["TE_ka|AP_007|EPG_web"]
    systems = {s["name"]: s for s in get("/appdoc/systems", tenant_id=tid).json()}
    assert "EPG_app" not in systems and "EPG_web" in systems
    assert systems["EPG_dns"]["rule_count"] == 0  # geteilt, CT_dns gehoert zu B
    get("/appdoc/systems/EPG_app", 404, tenant_id=tid)
    doc = get("/appdoc/systems/EPG_web", tenant_id=tid).json()
    assert doc
    apps = {x["name"]: x for x in client.get(f"/applications?tenant_id={tid}").json()}
    assert [x["name"] for x in get("/applications", tenant_id=tid).json()] == ["Web"]
    get(f"/applications/{apps['App']['id']}", 404)
    get(f"/applications/{apps['App']['id']}/doc", 404)
    assert get(f"/applications/{apps['Web']['id']}/doc").status_code == 200

    # Findings: nur die eigenen; Triage fremder Findings nicht auffindbar
    all_findings = client.get("/findings", params={"tenant_id": tid, "limit": 500}).json()
    own = get("/findings", tenant_id=tid, limit=500).json()
    assert own and len(own) < len(all_findings)
    assert all("b-leer" not in (f["message"] or "") for f in own)
    foreign = next(f for f in all_findings if "b-leer" in (f["message"] or ""))
    uo = _user(tid, a, roles="operator")
    r = client.patch(f"/findings/{foreign['id']}", json={"status": "accepted"}, headers=uo)
    assert r.status_code == 404
    r = client.patch(f"/findings/{own[0]['id']}", json={"note": "gesehen"}, headers=uo)
    assert r.status_code == 200, r.text
    r = client.patch(f"/findings/{own[0]['id']}", json={"note": "x"}, headers=ua)
    assert r.status_code == 403  # viewer: keine Triage

    # Kunde B sieht seine Regeln (b-dns, CT_app, CT_dns), nichts von A ausser CT_app
    ub = _user(tid, b)
    rules_b = {x["name"] for x in client.get("/rules", headers=ub).json()}
    assert rules_b == {"CT_app", "CT_dns", "b-dns", "b-leer"}


# Namen der Infrastruktur in den Beispieldaten (Quellsysteme, VDOM, vCenter)
_INFRA = ("fw-k", "fw-u", "apic", "kunde-b", "esx-zrh-01", "CL-PROD-A", "DC-ZRH", "dvs-core")


def test_kunden_doku_ohne_geraetenamen(client):
    """0.58.0 (ADR 0018 Schritt 4): jede Antwort an Kunden-Benutzer ohne Namen der Infrastruktur -
    Quellsysteme neutral („Firewall 1“, „ACI“), VDOMs nummeriert, ESXi-Host, Cluster, Datacenter
    und Switch leer; Betreuer sehen weiter alles."""
    tid, ids, a, b, shared = _env(client)
    vc = copy.deepcopy(VC)
    for vm in vc["virtual"]["vms"]:
        vm.update(host="esx-zrh-01", cluster="CL-PROD-A", datacenter="DC-ZRH")
        vm["nics"][0]["switch"] = "dvs-core"
    for pg in vc["virtual"]["portgroups"]:
        pg["switch"] = "dvs-core"
    _deliver(client, tid, ids["vc"], "vmware", vc)
    ua = _user(tid, a)

    def get(path, **params):
        r = client.get(path, params={"tenant_id": tid, **params}, headers=ua)
        assert r.status_code == 200, (path, r.status_code, r.text[:300])
        return r

    rules = get("/rules").json()
    objects = get("/objects").json()["rows"]
    pgs = get("/portgroups").json()
    apps = get("/applications").json()
    paths = ["/rules/summary", "/findings", "/findings/summary", "/nat", "/vms", "/appdoc/systems",
             "/appdoc/systems/EPG_web", "/systems/labels", "/customers", "/zones",
             "/zones/matrix", "/zones/plan", "/auth/me", f"/customers/{a}/members"]  # fmt: skip
    paths += [f"/rules/{r['id']}" for r in rules] + [f"/objects/{o['id']}" for o in objects]
    paths += [f"/nat/{n['id']}" for n in get("/nat").json()]
    paths += [f"/portgroups/{p['id']}" for p in pgs]
    paths += [f"/applications/{x['id']}" for x in apps] + [f"/applications/{x['id']}/doc"
                                                           for x in apps]  # fmt: skip
    seen = []
    for path in paths:
        text = get(path, kind="rule").text
        seen.append(text)
        for name in _INFRA:
            assert not re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text), (path, name)
    joined = "\n".join(seen)
    assert "Firewall 1" in joined and "web01-007-ka" in joined  # eigene VM bleibt
    vms = get("/vms").json()["rows"]
    assert vms[0]["host"] is None and vms[0]["cluster"] is None
    by_name = {r["name"]: r for r in rules}
    assert by_name["a-dns"]["vdom"] == "root"
    # Export des Zonenplans (SVG) ebenso
    svg = get("/zones/plan/export", format="svg").text
    assert not any(name in svg for name in ("fw-k", "fw-u"))

    # Betreuer: unveraendert
    full = client.get("/vms", params={"tenant_id": tid}).json()["rows"]
    assert {v["host"] for v in full} == {"esx-zrh-01"}
    assert "fw-k" in {s["name"] for s in client.get("/systems/labels").json()}


def test_neutrale_bezeichnungen():
    """`customer_alias.Aliases` ohne Datenbank: Namen als ganze Woerter, laengste zuerst, VDOMs in
    Bezeichnungen und Knoten-IDs, Felder leer bzw. nummeriert."""
    from app.store.customer_alias import Aliases

    al = Aliases.__new__(Aliases)
    al.systems = {1: "Firewall 1", 2: "ACI"}
    al.names = {"fw-01": "Firewall 1", "fw-01-ha": "Firewall 1", "apic-1": "ACI",
                "leaf-101": "Leaf"}  # fmt: skip
    al.vdoms = {"kunde-x": "2", "edge": "1", "root": "root"}
    al._pattern = al._vdom_pattern = None
    al._compiled = False
    text = "fw-01 · VDOM kunde-x leitet 10.0.0.1 über port1 (fw-01-ha, leaf-101); fw-01.example.ch"
    assert al.text(text) == (
        "Firewall 1 · VDOM 2 leitet 10.0.0.1 über port1 (Firewall 1, Leaf); Firewall 1.example.ch"
    )
    assert al.text("H_fw-01_x und fw-010 bleiben") == "H_fw-01_x und fw-010 bleiben"
    assert al.text("Knoten 3|kunde-x, 3|root") == "Knoten 3|2, 3|root"
    data = {"vdom": "edge", "vdoms": ["root", "kunde-x"], "host": "esx", "nics": [{"switch": "d"}],
            "by_vdom": {"kunde-x": 3}, "source": "apic-1", "hostname": "web01"}  # fmt: skip
    assert al.clean(data) == {"vdom": "1", "vdoms": ["root", "2"], "host": None,
                              "nics": [{"switch": None}], "by_vdom": {"2": 3}, "source": "ACI",
                              "hostname": "web01"}  # fmt: skip


def test_kunden_benutzer_ohne_zuordnung_und_ohne_admin(client):
    tid, ids, a, b, shared = _env(client)
    # ohne Kunden bzw. ohne Mandant: nichts (fail-closed)
    for headers in (_user(tid), {"X-Dev-Customers": str(a), "X-Dev-Roles": "viewer"}):
        r = client.get("/rules", params={"tenant_id": tid}, headers=headers)
        assert r.status_code == 403, r.text
    # Kunde eines anderen Mandanten zaehlt nicht
    other = client.post("/tenants", json={"name": "Umgebung 9"}, headers=ADMIN).json()["id"]
    foreign = _create(client, other, "Fremd", [])["id"]
    assert client.get("/rules", params={"tenant_id": tid},
                      headers=_user(tid, foreign)).status_code == 403  # fmt: skip
    assert client.get("/rules", params={"tenant_id": other},
                      headers=_user(tid, foreign)).status_code == 403  # fmt: skip
    # Rolle admin gilt fuer Kunden-Benutzer nicht
    ua = _user(tid, a, roles="admin")
    assert client.get("/systems", headers=ua).status_code == 403
    assert client.get("/rules", params={"tenant_id": tid}, headers=ua).status_code == 403
    # nicht freigegeben: Verwaltung, Routing, Betrieb
    uv = _user(tid, a)
    for method, path in (
        ("GET", "/systems"),
        ("GET", "/agents"),
        ("GET", "/routing/trace"),
        ("GET", "/system/health"),
        ("GET", "/reconcile/runs"),
        ("GET", "/netbox-sync/preview"),
        ("GET", f"/customers/{a}"),
        ("POST", "/customers"),
        ("POST", "/jobs/collect-all"),
        ("GET", "/published-services"),
        ("GET", "/inventory/devices"),
    ):
        r = client.request(method, path, params={"tenant_id": tid}, headers=uv)
        assert r.status_code in (403, 404, 405), (method, path, r.status_code)
        if r.status_code == 403:
            assert r.json()["detail"] in ("not available for customer users", "insufficient role")


def _routes(routes, out):
    """Alle APIRoutes - auch aus verschachtelt eingebundenen Routern (FastAPI ab 0.13x)."""
    for r in routes:
        if isinstance(r, APIRoute):
            out.append(r)
        elif hasattr(r, "original_router"):
            _routes(r.original_router.routes, out)
    return out


def _calls(dependant, out):
    for d in dependant.dependencies:
        out.append(getattr(d.call, "__qualname__", ""))
        _calls(d, out)
    return out


def test_fail_closed_fuer_alle_endpunkte(client):
    """Jeder Endpunkt mit Benutzer-Bezug prueft die Rolle (`require_role`); Kunden-Benutzer
    erhalten ausserhalb der Freigabeliste 403 - auch fuer Endpunkte, die spaeter dazukommen."""
    tid, ids, a, b, shared = _env(client)
    routes = _routes(client.app.routes, [])
    seen: set[tuple[str, str]] = set()
    ua = _user(tid, a, roles="operator")
    for route in routes:
        calls = _calls(route.dependant, [])
        uses_principal = any("get_principal" in c for c in calls)
        if not uses_principal:  # Agent-, interne und offene Endpunkte (eigene Pruefung)
            continue
        assert any("require_role" in c for c in calls), f"{route.path}: ohne require_role"
        path = re.sub(r"\{[^}]+\}", "1", route.path)
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            seen.add((method, route.path))
            r = client.request(method, path, params={"tenant_id": tid}, headers=ua)
            detail = r.json().get("detail") if r.status_code == 403 else None
            if (method, route.path) in CUSTOMER_ROUTES:
                assert detail != "not available for customer users", (method, route.path)
            else:  # Admin-Endpunkte scheitern schon an der Rolle
                assert detail in ("not available for customer users", "insufficient role"), (
                    method, route.path, r.status_code, r.text[:200])  # fmt: skip
    assert CUSTOMER_ROUTES <= seen, CUSTOMER_ROUTES - seen  # keine veralteten Eintraege


def test_lesezugriffe_von_kunden_immer_auditiert(client, session_factory):
    tid, ids, a, b, shared = _env(client)
    assert client.get("/rules", params={"tenant_id": tid}, headers=_user(tid, a)).status_code == 200
    s = session_factory()
    rows = [r for r in s.query(AuditLog).all() if r.action == "read.rules"]
    s.close()
    assert len(rows) == 1  # audit_reads des Mandanten ist aus - Kunden-Benutzer trotzdem
    assert rows[0].detail["customers"] == [a] and rows[0].actor == "user:dev-customer"


def _settings() -> Settings:
    return Settings(
        auth_mode="oidc",
        oidc_jwks_url="https://kc/certs",
        oidc_roles_claim="realm_access.roles",
        oidc_tenants_claim="tenant_ids",
    )


@pytest.mark.parametrize(
    ("claims", "roles", "customers"),
    [
        ({"realm_access": {"roles": ["customer"]}, "customer_ids": ["3", 4]}, {"viewer"}, (3, 4)),
        ({"realm_access": {"roles": ["admin", "operator"]}, "customer_ids": 5}, {"operator"}, (5,)),
        ({"realm_access": {"roles": ["customer"]}}, {"viewer"}, ()),  # ohne Kunden: nichts
        ({"realm_access": {"roles": ["viewer"]}, "customer_ids": ["3,4"]}, {"viewer"}, (3, 4)),
    ],
)
def test_oidc_kunden_benutzer(claims, roles, customers):
    p = oidc.claims_to_principal({"sub": "k", "tenant_ids": [1], **claims}, _settings())
    assert p.is_customer and not p.is_admin
    assert p.roles == frozenset(roles) and p.customer_ids == customers and p.tenant_ids == (1,)
    betreuer = oidc.claims_to_principal(
        {"sub": "b", "realm_access": {"roles": ["admin"]}}, _settings()
    )
    assert betreuer.is_admin and not betreuer.is_customer
    # leerer Claim (Mapper ohne Attribut) ohne Rolle customer: Betreuer wie bisher
    leer = oidc.claims_to_principal(
        {"sub": "b", "realm_access": {"roles": ["viewer"]}, "tenant_ids": [1], "customer_ids": []},
        _settings(),
    )
    assert not leer.is_customer and leer.tenant_ids == (1,)


def test_finding_detail_ohne_fremde_regeln():
    detail = {"ip": "10.10.1.5", "rule_names": ["b-dns"], "rules": 2, "firewall_objects": ["H"]}
    customer = Principal(subject="k", roles=frozenset({"viewer"}), tenant_ids=(1,),
                         customer_ids=(1,))  # fmt: skip
    assert finding_detail(customer, detail) == {"ip": "10.10.1.5"}
    assert finding_detail(Principal(subject="b"), detail) == detail
