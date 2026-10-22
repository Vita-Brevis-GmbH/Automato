"""Kunden als Ausschnitt der Umgebung (ADR 0018, Schritt 1): Selektoren, abgeleitete
Zugehoerigkeit, Vorschau, Vorschlaege und „keinem Kunden zugeordnet“.

Umgebung: ein APIC mit TE_ka (EPG_web in AP_007, EPG_db in AP_900), TE_kb (EPG_app) und common
(EPG_dns); eine FortiGate fw-k mit VDOM root und kunde-b; ein vCenter mit zwei VMs.
"""

import copy

ADMIN = {"X-Dev-Roles": "admin"}
OP = {"X-Dev-Roles": "operator"}
TOKEN = {"X-Internal-Token": "dev-only-token"}


def _epg(name, tenant, ap, vrf, subnet, bd=None):
    return {"name": name, "dn": f"uni/tn-{tenant}/ap-{ap}/epg-{name}", "tenant": tenant, "ap": ap,
            "bd": bd or f"BD_{name}", "vrf": vrf, "bd_subnets": [subnet]}  # fmt: skip


def _contract(name, src, dst):
    return {"source_platform": "cisco_aci", "external_id": f"uni/tn-TE_ka/brc-{name}/SJ_1",
            "name": name, "action": "allow", "sources": [f"epg:{src}"],
            "destinations": [f"epg:{dst}"], "services": ["tcp/443"], "aci_scope": "global",
            "raw": {"dn": f"uni/tn-TE_ka/brc-{name}", "name": name}}  # fmt: skip


WEB = "uni/tn-TE_ka/ap-AP_007/epg-EPG_web"
DB = "uni/tn-TE_ka/ap-AP_900/epg-EPG_db"
APP = "uni/tn-TE_kb/ap-AP_100/epg-EPG_app"
DNS = "uni/tn-common/ap-AP_infra/epg-EPG_dns"
APIC = {
    "status": "completed",
    "epgs": [
        _epg("EPG_web", "TE_ka", "AP_007", "VRF_ka", "10.50.1.1/24"),
        _epg("EPG_db", "TE_ka", "AP_900", "VRF_ka", "10.50.9.1/24"),
        _epg("EPG_app", "TE_kb", "AP_100", "VRF_kb", "10.60.1.1/24"),
        _epg("EPG_dns", "common", "AP_infra", "default", "10.0.53.1/24"),
    ],
    "rules": [
        _contract("CT_web", DB, WEB),
        _contract("CT_app", APP, WEB),  # ueber zwei Kunden
        _contract("CT_dns", APP, DNS),
    ],
}


def _policy(pid, name, src, dst, vdom="root", srcintf="lan", dstintf="wan"):
    return {"source_platform": "fortigate", "external_id": str(pid), "name": name,
            "action": "allow", "sources": src, "destinations": dst, "services": ["tcp/443"],
            "vdom": vdom, "family": "v4",
            "raw": {"policyid": pid, "srcintf": [{"name": srcintf}],
                    "dstintf": [{"name": dstintf}], "status": "enable"}}  # fmt: skip


FW = {
    "status": "completed",
    "rules": [
        _policy(1, "web-to-backend", ["10.50.1.10"], ["10.99.0.5"]),
        _policy(2, "kb-out", ["10.70.0.0/24"], ["all"], vdom="kunde-b", srcintf="kb-lan"),
        _policy(3, "any-any", ["all"], ["all"]),
    ],
    "inventory": {
        "devices": [{"name": "fw-k", "role": "firewall"}],
        "interfaces": [
            {"device": "fw-k", "name": "lan", "ips": ["10.99.0.1/24"], "vdom": "root"},
            {"device": "fw-k", "name": "kb-lan", "ips": ["10.70.0.1/24"], "vdom": "kunde-b",
             "zone": "kb"},
        ],
    },
}  # fmt: skip


def _vm(name, ip, portgroup):
    nic = {"name": "Network adapter 1", "portgroup": portgroup, "connected": True, "ips": [ip]}
    return {"name": name, "power_state": "poweredOn", "nics": [nic]}


VC = {
    "status": "completed",
    "virtual": {
        "vms": [
            _vm("web01-007-ka", "10.50.1.10", "TE_ka|AP_007|EPG_web"),
            _vm("app01-100-kb", "10.60.1.5", "TE_kb|AP_100|EPG_app"),
        ],
        "portgroups": [
            {"name": "TE_ka|AP_007|EPG_web", "vm_count": 1},
            {"name": "TE_kb|AP_100|EPG_app", "vm_count": 1},
        ],
    },
}


def _system(client, tid, name, platform, **extra):
    body = {"tenant_id": tid, "name": name, "platform": platform, "base_url": "https://x", **extra}
    r = client.post("/systems", json=body, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _deliver(client, tid, sid, platform, result):
    body = {"platform": platform, "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text


def _seed(client) -> tuple[int, dict[str, int]]:
    tid = client.post("/tenants", json={"name": "Umgebung 1"}, headers=ADMIN).json()["id"]
    ids = {}
    for name, platform, result in (("apic", "cisco_aci", APIC), ("fw-k", "fortigate", FW),
                                   ("vc", "vmware", VC)):  # fmt: skip
        extra = {"username": "ro", "password": "x"} if platform == "vmware" else {}
        ids[name] = _system(client, tid, name, platform, **extra)
        _deliver(client, tid, ids[name], platform, result)
    return tid, ids


def _create(client, tid, name, selectors, **extra):
    body = {"tenant_id": tid, "name": name, "selectors": selectors, **extra}
    r = client.post("/customers", json=body, headers=OP)
    assert r.status_code == 201, r.text
    return r.json()


def _members(client, cid, kind) -> set[str]:
    r = client.get(f"/customers/{cid}/members", params={"kind": kind})
    assert r.status_code == 200, r.text
    return {m["name"] for m in r.json()}


def test_kunden_ueber_selektoren(client):
    tid, ids = _seed(client)

    # Vorschlaege: je ACI-Tenant ein Kunde mit Kuerzel, common als geteilter Teil
    sug = client.get("/customers/suggestions", params={"tenant_id": tid}).json()
    assert {(s["name"], s["code"], s["shared"], s["epgs"]) for s in sug} == {
        ("TE_ka", "ka", False, 2),
        ("TE_kb", "kb", False, 1),
        ("Geteilt", None, True, 1),
    }
    before = client.get("/customers/unassigned", params={"tenant_id": tid}).json()
    assert (before["epg"]["count"], before["rule"]["count"], before["vm"]["count"]) == (4, 6, 2)

    # Kunde A: Tenant TE_ka ohne AP_900
    a = _create(client, tid, "Kunde A", [
        {"kind": "aci_tenant", "value": "TE_ka"},
        {"kind": "aci_ap", "value": "TE_ka/AP_900", "exclude": True},
    ], code="ka")  # fmt: skip
    assert a["counts"] == {"epg": 1, "net": 1, "object": 1, "rule": 3, "vm": 1, "nat": 0,
                           "finding": 1}  # fmt: skip
    # Finding ueber sein Subjekt: das Objekt 10.50.1.10 fehlt im IPAM (0.31.0)
    assert _members(client, a["id"], "finding") == {"unmatched_object: 10.50.1.10"}
    assert _members(client, a["id"], "epg") == {"TE_ka/AP_007/EPG_web"}
    assert _members(client, a["id"], "net") == {"10.50.1.0/24"}
    assert _members(client, a["id"], "rule") == {"CT_web", "CT_app", "web-to-backend"}
    assert _members(client, a["id"], "vm") == {"web01-007-ka"}
    rows = client.get(f"/customers/{a['id']}/members", params={"kind": "rule"}).json()
    assert {r["name"]: r["via"] for r in rows}["web-to-backend"] == "Netz 10.50.1.0/24"

    # Kunde B: VDOM kunde-b auf fw-k und Tenant TE_kb; geteilter Teil: common
    b = _create(client, tid, "Kunde B", [
        {"kind": "fw_vdom", "value": "kunde-b", "source_system_id": ids["fw-k"]},
        {"kind": "aci_tenant", "value": "TE_kb"},
    ])  # fmt: skip
    assert _members(client, b["id"], "rule") == {"kb-out", "CT_app", "CT_dns"}
    assert _members(client, b["id"], "net") == {"10.60.1.0/24", "10.70.0.0/24"}
    assert _members(client, b["id"], "vm") == {"app01-100-kb"}
    shared = _create(client, tid, "Geteilt", [{"kind": "aci_tenant", "value": "common"}],
                     shared=True)  # fmt: skip
    listed = client.get("/customers", params={"tenant_id": tid}).json()
    assert [(c["name"], c["shared"]) for c in listed] == [
        ("Geteilt", True), ("Kunde A", False), ("Kunde B", False)]  # fmt: skip
    assert shared["counts"]["epg"] == 1
    assert client.get("/customers/suggestions", params={"tenant_id": tid}).json() == []
    after = client.get("/customers/unassigned", params={"tenant_id": tid}).json()
    assert after["epg"] == {"count": 1, "samples": ["TE_ka/AP_900/EPG_db"]}
    assert after["rule"] == {"count": 1, "samples": ["any-any"]}  # any beruehrt kein Netz
    assert after["vm"]["count"] == 0

    # neuer Lauf: neue EPG im Tenant gehoert ohne Pflege dazu
    apic = copy.deepcopy(APIC)
    apic["epgs"].append(_epg("EPG_api", "TE_ka", "AP_007", "VRF_ka", "10.50.2.1/24"))
    _deliver(client, tid, ids["apic"], "cisco_aci", apic)
    assert "TE_ka/AP_007/EPG_api" in _members(client, a["id"], "epg")
    assert "10.50.2.0/24" in _members(client, a["id"], "net")

    # aendern: ohne Ausschluss gehoert auch AP_900 dazu
    body = {"tenant_id": tid, "name": "Kunde A", "code": "ka",
            "selectors": [{"kind": "aci_tenant", "value": "TE_ka"}]}  # fmt: skip
    r = client.put(f"/customers/{a['id']}", json=body, headers=OP)
    assert r.status_code == 200 and r.json()["counts"]["epg"] == 3

    # Quellsystem geloescht: sein Selektor faellt weg, die Zugehoerigkeit neu
    assert client.delete(f"/systems/{ids['fw-k']}", headers=ADMIN).status_code in (200, 204)
    b_now = next(c for c in client.get("/customers", params={"tenant_id": tid}).json()
                 if c["name"] == "Kunde B")  # fmt: skip
    assert [s["kind"] for s in b_now["selectors"]] == ["aci_tenant"]
    assert b_now["counts"]["rule"] == 2  # nur noch die Contracts

    # loeschen
    assert client.delete(f"/customers/{b['id']}", headers=OP).status_code == 204
    assert [c["name"] for c in client.get("/customers", params={"tenant_id": tid}).json()] == [
        "Geteilt", "Kunde A"]  # fmt: skip


def test_vorschau_kuerzel_netze_und_rechte(client):
    tid, ids = _seed(client)

    def preview(selectors):
        r = client.post("/customers/preview", json={"tenant_id": tid, "selectors": selectors})
        assert r.status_code == 200, r.text
        return r.json()

    # Kuerzel: Tenant TE_ka (beide EPGs) und die VM web01-007-ka
    out = preview([{"kind": "code", "value": "ka"}])
    assert out["counts"]["epg"] == 2 and out["counts"]["vm"] == 1
    assert out["samples"]["vm"] == ["web01-007-ka"]

    # Netz mit ausgeschnittenem Teil: 10.50.9.0/24 (EPG_db) gehoert nicht dazu
    out = preview([{"kind": "prefix", "value": "10.50.0.0/16"},
                   {"kind": "prefix", "value": "10.50.9.0/24", "exclude": True}])  # fmt: skip
    assert "10.50.9.0/24" not in out["samples"]["net"] and out["counts"]["net"] == 8
    assert out["counts"]["object"] == 1  # der Host 10.50.1.10
    # Regeln, deren Quelle bzw. Ziel das Netz beruehrt (auch Contracts ueber EPG-Subnetze)
    assert set(out["samples"]["rule"]) == {"web-to-backend", "CT_web", "CT_app"}

    # Zone: Netze am Interface und die Regeln der Zone
    out = preview([{"kind": "fw_zone", "value": "kb"}])
    assert out["samples"]["net"] == ["10.70.0.0/24"]

    # Pruefung der Eingaben, Rechte und Mandant
    bad = client.post("/customers/preview", json={"tenant_id": tid,
                      "selectors": [{"kind": "prefix", "value": "10.0.0.300/8"}]})  # fmt: skip
    assert bad.status_code == 422
    unknown = client.post("/customers/preview", json={"tenant_id": tid,
                          "selectors": [{"kind": "zone", "value": "x"}]})  # fmt: skip
    assert unknown.status_code == 422
    viewer = {"X-Dev-Roles": "viewer"}
    body = {"tenant_id": tid, "name": "X", "selectors": []}
    assert client.post("/customers", json=body, headers=viewer).status_code == 403
    _create(client, tid, "X", [])
    assert client.post("/customers", json=body, headers=OP).status_code == 409
    other = client.post("/tenants", json={"name": "Umgebung 2"}, headers=ADMIN).json()["id"]
    foreign = {
        "tenant_id": other,
        "name": "Y",
        "selectors": [{"kind": "fw_vdom", "value": "root", "source_system_id": ids["fw-k"]}],
    }
    assert client.post("/customers", json=foreign, headers=OP).status_code == 404  # fmt: skip

    # Mandant geloescht: Kunden mit
    r = client.delete(f"/tenants/{tid}?confirm=Umgebung 1", headers=ADMIN)
    assert r.status_code in (200, 204), r.text
    assert client.get("/customers", params={"tenant_id": tid}).json() == []


DDI = [
    {"kind": "prefix", "value": "10.80.0.0/24", "tenant": "Kunde C", "vrf": "VRF_C"},
    {"kind": "prefix", "value": "10.81.0.0/24", "tags": ["kunde-c"]},
    {"kind": "host", "value": "10.82.0.7", "dns_name": "c01.example.ch",
     "attrs": {"Kunde": "C"}},
    {"kind": "prefix", "value": "10.83.0.0/24", "tenant": "Kunde D"},
]  # fmt: skip
FWC = {
    "status": "completed",
    "rules": [
        _policy(10, "c-web", ["10.80.0.10"], ["all"], vdom="kunde-c"),
        {**_policy(11, "c-leer", ["10.80.0.11"], [], vdom="kunde-c"), "destinations": []},
    ],
    "nat_rules": [
        {"source_platform": "fortigate", "external_id": "VIP_c", "name": "VIP_c",
         "nat_type": "dnat_vip", "ext_ip": "198.51.100.20", "mapped_ip": "10.80.0.10"},
        {"source_platform": "fortigate", "external_id": "VIP_d", "name": "VIP_d",
         "nat_type": "dnat_vip", "ext_ip": "198.51.100.21", "mapped_ip": "10.83.0.10"},
    ],
}  # fmt: skip


def test_ipam_merkmale_nat_und_findings(client):
    """0.31.0: Netze aus dem IPAM ueber Tenant, VRF bzw. Netzansicht und Tag bzw. Attribut;
    NAT ueber die interne IP; Findings ueber ihr Subjekt."""
    tid = client.post("/tenants", json={"name": "Umgebung 3"}, headers=ADMIN).json()["id"]
    ddi = _system(client, tid, "ddi", "infoblox", username="ro", password="x")
    body = {"platform": "infoblox", "tenant_id": tid, "source_system_id": ddi}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    client.post(f"/internal/collector-runs/{run_id}/references", headers=TOKEN,
                json={"records": DDI})  # fmt: skip
    done = {"status": "completed", "reference_count": len(DDI)}
    client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=done)
    _deliver(client, tid, _system(client, tid, "fw-c", "fortigate"), "fortigate", FWC)

    def preview(*selectors):
        body = {"tenant_id": tid, "selectors": [dict(zip(("kind", "value"), s, strict=True))
                                                for s in selectors]}  # fmt: skip
        r = client.post("/customers/preview", json=body)
        assert r.status_code == 200, r.text
        return r.json()

    assert preview(("ipam_tenant", "Kunde C"))["samples"]["net"] == ["10.80.0.0/24"]
    assert preview(("ipam_vrf", "vrf_c"))["samples"]["net"] == ["10.80.0.0/24"]  # ohne Gross/klein
    assert preview(("ipam_tag", "kunde-c"))["samples"]["net"] == ["10.81.0.0/24"]
    assert preview(("ipam_tag", "Kunde=C"))["samples"]["net"] == ["10.82.0.7/32"]

    c = _create(client, tid, "Kunde C", [
        {"kind": "ipam_tenant", "value": "Kunde C"},
        {"kind": "fw_vdom", "value": "kunde-c"},
    ])  # fmt: skip
    assert _members(client, c["id"], "nat") == {"VIP_c"}  # interne IP im Netz, VIP_d nicht
    rows = client.get(f"/customers/{c['id']}/members", params={"kind": "nat"}).json()
    assert rows[0]["via"] == "interne IP im Netz"
    findings = _members(client, c["id"], "finding")
    assert "rule_incomplete: c-leer (VDOM kunde-c)" in findings  # Regel des Kunden
    assert not any("10.83" in f for f in findings)
    # ausschliessen ueber das IPAM-Merkmal
    out = preview(("ipam_tenant", "Kunde C"))
    body = {"tenant_id": tid, "selectors": [
        {"kind": "prefix", "value": "10.80.0.0/16"},
        {"kind": "ipam_tag", "value": "kunde-c", "exclude": True}]}  # fmt: skip
    cut = client.post("/customers/preview", json=body).json()
    assert "10.81.0.0/24" not in cut["samples"]["net"] and out["counts"]["nat"] == 1


def test_kunden_filter_in_den_listen(client):
    """0.32.0 (ADR 0018 Schritt 2): `customer_id` zeigt in jeder Liste den Ausschnitt des Kunden
    samt geteiltem Teil (Tenant common)."""
    tid, ids = _seed(client)
    a = _create(client, tid, "Kunde A", [{"kind": "aci_tenant", "value": "TE_ka"}])["id"]
    b = _create(client, tid, "Kunde B", [{"kind": "aci_tenant", "value": "TE_kb"}])["id"]
    _create(client, tid, "Geteilt", [{"kind": "aci_tenant", "value": "common"}], shared=True)

    def get(path, cid, **params):
        flt = {"customer_id": cid} if cid is not None else {}
        r = client.get(path, params={"tenant_id": tid, **flt, **params})
        assert r.status_code == 200, (path, r.text)
        return r

    rules = get("/rules", a)
    assert {x["name"] for x in rules.json()} == {"CT_web", "CT_app", "web-to-backend", "CT_dns"}
    assert rules.headers["X-Total-Count"] == "4"  # CT_dns ueber den geteilten Teil (common)
    assert get("/rules/summary", a).json()["total"] == 4
    assert {x["name"] for x in get("/rules", b).json()} == {"CT_app", "CT_dns"}
    assert get("/rules", None).headers["X-Total-Count"] == "6"  # ohne Kunde: alles
    assert [f["subject_id"] for f in get("/findings", a).json()] == ["10.50.1.10"]
    assert get("/findings/summary", b).json()["total"] == 0
    assert [v["name"] for v in get("/vms", a).json()["rows"]] == ["web01-007-ka"]
    assert [p["name"] for p in get("/portgroups", b).json()] == ["TE_kb|AP_100|EPG_app"]
    systems = {s["name"] for s in get("/appdoc/systems", a).json()}
    assert systems == {"10.50.1.10", "EPG_web", "EPG_db", "EPG_dns"}
    assert get("/objects", a).json()["total"] == 0 and get("/nat", a).json() == []

    # Applikationen ueber ihre Mitglieder
    for name, ref in (("Web", WEB), ("App", APP)):
        body = {"name": name, "members": [{"kind": "epg", "ref": ref}]}
        assert (
            client.post(f"/applications?tenant_id={tid}", json=body, headers=OP).status_code == 201
        )
    assert [x["name"] for x in get("/applications", a).json()] == ["Web"]
    assert [x["name"] for x in get("/applications", b).json()] == ["App"]

    # fremder Kunde bzw. ohne Mandant
    other = client.post("/tenants", json={"name": "Umgebung 9"}, headers=ADMIN).json()["id"]
    foreign = _create(client, other, "Fremd", [])["id"]
    r = client.get("/rules", params={"tenant_id": tid, "customer_id": foreign})
    assert r.status_code == 404
