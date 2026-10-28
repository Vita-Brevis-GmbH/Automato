# ruff: noqa: S104 - 0.0.0.0/0 sind Routen, keine Bind-Adressen
"""Kunde oben gewaehlt (0.76.0): Routing, Topologie, Weg-Suche, Inventar, Verkabelung, Abgleich
und Zonen nur im Ausschnitt des Kunden - eigenes plus geteiltes (markiert `shared`).

Umgebung wie in den Kunden-Tests: APIC mit TE_ka (Kunde A), TE_kb (Kunde B) und common (geteilt),
FortiGate fw-k mit VDOM root (Regeln beider Kunden) und kunde-b (nur Kunde B), vCenter. Dazu
Routing je VDOM bzw. VRF und ein Leaf-Port je EPG.
"""

import copy

from test_customers import APIC, FW, TOKEN, VC, _create, _deliver, _system

ADMIN = {"X-Dev-Roles": "admin"}


def _route(node, context, prefix, hops=None, protocol="static"):
    return {"node": node, "context": context, "prefix": prefix, "table": "rib",
            "protocol": protocol, "next_hops": hops or [{"ip": "10.255.0.1"}]}  # fmt: skip


def _seed(client):
    tid = client.post("/tenants", json={"name": "Umgebung I"}, headers=ADMIN).json()["id"]
    apic = copy.deepcopy(APIC)
    apic["routing"] = {
        "contexts": [
            {"node": "leaf-101", "context": c, "kind": "vrf"}
            for c in ("TE_ka:VRF_ka", "TE_kb:VRF_kb", "common:default")
        ],
        "routes": [
            _route("leaf-101", "TE_ka:VRF_ka", "10.50.1.0/24", protocol="connected"),
            _route("leaf-101", "TE_kb:VRF_kb", "10.60.1.0/24", protocol="connected"),
            _route("leaf-101", "common:default", "10.0.53.0/24", protocol="connected"),
        ],
    }
    apic["inventory"] = {
        "devices": [{"name": "leaf-101", "role": "leaf", "node_id": "101"},
                    {"name": "leaf-102", "role": "leaf", "node_id": "102"}],
        "interfaces": [
            {"device": "leaf-101", "name": "eth1/1", "epgs": ["TE_ka/AP_007/EPG_web"],
             "tagged_vlans": [101]},
            {"device": "leaf-101", "name": "eth1/2", "epgs": ["TE_kb/AP_100/EPG_app"],
             "tagged_vlans": [201]},
            {"device": "leaf-101", "name": "eth1/3", "epgs": ["common/AP_infra/EPG_dns"]},
        ],
        "vlans": [
            {"vid": 101, "name": "EPG_web", "epgs": ["TE_ka/AP_007/EPG_web"]},
            {"vid": 201, "name": "EPG_app", "epgs": ["TE_kb/AP_100/EPG_app"]},
        ],
        "subnets": [
            {"prefix": "10.50.1.0/24", "gateway": "10.50.1.1/24", "context": "bd",
             "context_name": "BD_EPG_web", "vrf": "TE_ka:VRF_ka", "vlan": 101},
            {"prefix": "10.60.1.0/24", "gateway": "10.60.1.1/24", "context": "bd",
             "context_name": "BD_EPG_app", "vrf": "TE_kb:VRF_kb", "vlan": 201},
            {"prefix": "10.0.53.0/24", "gateway": "10.0.53.1/24", "context": "bd",
             "context_name": "BD_EPG_dns", "vrf": "common:default"},
        ],
    }  # fmt: skip
    fw = copy.deepcopy(FW)
    fw["routing"] = {
        "contexts": [{"node": "fw-k", "context": "root"}, {"node": "fw-k", "context": "kunde-b"}],
        "routes": [
            _route("fw-k", "root", "0.0.0.0/0"),
            _route("fw-k", "root", "10.50.0.0/16"),  # Netz von Kunde A
            _route("fw-k", "root", "10.60.0.0/16"),  # Netz von Kunde B
            _route("fw-k", "kunde-b", "0.0.0.0/0"),
        ],
        "peers": [
            {"node": "fw-k", "context": "root", "peer_ip": "10.255.0.1", "state": "Established"},
            {"node": "fw-k", "context": "kunde-b", "peer_ip": "10.255.1.1"},
        ],
    }
    ids = {}
    for name, platform, result in (("apic", "cisco_aci", apic), ("fw-k", "fortigate", fw),
                                   ("vc", "vmware", VC)):  # fmt: skip
        extra = {"username": "ro", "password": "x"} if platform == "vmware" else {}
        ids[name] = _system(client, tid, name, platform, **extra)
        _deliver(client, tid, ids[name], platform, result)
    common = _create(client, tid, "Gemeinsam", [{"kind": "aci_tenant", "value": "common"}],
                     shared=True)  # fmt: skip
    a = _create(client, tid, "Kunde A", [{"kind": "aci_tenant", "value": "TE_ka"}])
    b = _create(client, tid, "Kunde B", [
        {"kind": "aci_tenant", "value": "TE_kb"},
        {"kind": "fw_vdom", "value": "kunde-b"},
        {"kind": "prefix", "value": "10.70.0.0/24"},
    ])  # fmt: skip
    return tid, ids, a["id"], b["id"], common["id"]


def _get(client, path, tid, cid, **params):
    query = {"tenant_id": tid, **({"customer_id": cid} if cid is not None else {}), **params}
    r = client.get(path, params=query)
    assert r.status_code == 200, r.text
    return r.json()


def test_routing_nur_kontexte_und_routen_des_kunden(client):
    tid, ids, a, b, _ = _seed(client)
    everything = _get(client, "/routing/summary", tid, None)
    assert len(everything["contexts"]) == 5

    ctx = {c["context"]: c for c in _get(client, "/routing/summary", tid, a)["contexts"]}
    # Kunde A: seine VRF eigen, common geteilt, root geteilt (dort auch Regeln von Kunde B)
    assert set(ctx) == {"TE_ka:VRF_ka", "common:default", "root"}
    assert not ctx["TE_ka:VRF_ka"]["shared"] and ctx["common:default"]["shared"]
    assert ctx["root"]["shared"]
    ctx_b = {c["context"]: c for c in _get(client, "/routing/summary", tid, b)["contexts"]}
    assert not ctx_b["kunde-b"]["shared"] and "TE_ka:VRF_ka" not in ctx_b

    routes = {(r["context"], r["prefix"]) for r in _get(client, "/routing/routes", tid, a)}
    # im geteilten root nur die Default-Route und das Netz von Kunde A, nicht das von Kunde B
    assert ("root", "0.0.0.0/0") in routes and ("root", "10.50.0.0/16") in routes
    assert ("root", "10.60.0.0/16") not in routes
    assert ("TE_ka:VRF_ka", "10.50.1.0/24") in routes
    assert not any(c == "TE_kb:VRF_kb" or c == "kunde-b" for c, _ in routes)

    peers = _get(client, "/routing/peers", tid, a)
    assert [p["peer_ip"] for p in peers] == ["10.255.0.1"] and peers[0]["shared"]
    # Kunde B hat in root keine Regeln: nur sein VDOM kunde-b (eigen)
    peers_b = _get(client, "/routing/peers", tid, b)
    assert [(p["peer_ip"], p["shared"]) for p in peers_b] == [("10.255.1.1", False)]
    hits = _get(client, "/routing/lookup", tid, a, ip="10.60.1.5")  # Netz von Kunde B
    assert all(h["context"] != "root" for h in hits)

    # fremder Kunde eines anderen Mandanten: 404
    other = client.post("/tenants", json={"name": "Umgebung J"}, headers=ADMIN).json()["id"]
    r = client.get("/routing/summary", params={"tenant_id": other, "customer_id": a})
    assert r.status_code == 404


def test_inventar_weg_suche_und_topologie(client):
    tid, ids, a, b, _ = _seed(client)
    subnets = {s["prefix"]: s for s in _get(client, "/inventory/subnets", tid, a)}
    assert "10.50.1.0/24" in subnets and not subnets["10.50.1.0/24"]["shared"]
    assert subnets["10.0.53.0/24"]["shared"] and "10.60.1.0/24" not in subnets
    ports = {p["name"]: p for p in _get(client, "/inventory/interfaces", tid, a)}
    assert set(ports) >= {"eth1/1", "eth1/3"} and "eth1/2" not in ports
    assert ports["eth1/3"]["shared"] and not ports["eth1/1"]["shared"]
    vlans = {v["vid"] for v in _get(client, "/inventory/vlans", tid, a)}
    assert 101 in vlans and 201 not in vlans
    devices = {d["name"] for d in _get(client, "/inventory/devices", tid, a)}
    assert "leaf-101" in devices and "leaf-102" not in devices  # ohne Port/Kontext des Kunden

    found = _get(client, "/routing/resolve", tid, a, q="10.50.1")
    assert found["hits"] and all(h["ip"].startswith("10.50.1") for h in found["hits"])
    assert _get(client, "/routing/resolve", tid, a, q="app01")["hits"] == []  # VM von Kunde B
    assert _get(client, "/routing/resolve", tid, b, q="app01")["hits"]

    topo = _get(client, "/topology", tid, a)
    contexts = {n.get("context") for n in topo["nodes"] if n.get("source_system_id")}
    assert "TE_kb:VRF_kb" not in contexts and "kunde-b" not in contexts
    assert "TE_ka:VRF_ka" in contexts


def test_abgleich_zonen_verkabelung_und_dienste(client):
    tid, ids, a, b, _ = _seed(client)
    summary = _get(client, "/reconcile/summary", tid, a)
    total = sum(t["total"] for t in summary["findings"].values())
    assert total <= sum(t["total"] for t in _get(client, "/reconcile/summary", tid, None)
                        ["findings"].values())  # fmt: skip
    zone_a = client.post("/zones", json={"tenant_id": tid, "name": "Web A", "kind": "dmz",
                                         "customer_id": a,
                                         "selectors": [{"kind": "prefix",
                                                        "value": "10.50.1.0/24"}]},
                         headers={"X-Dev-Roles": "operator"})  # fmt: skip
    assert zone_a.status_code == 201, zone_a.text
    client.post("/zones", json={"tenant_id": tid, "name": "App B", "kind": "dmz",
                                "customer_id": b,
                                "selectors": [{"kind": "prefix", "value": "10.60.1.0/24"}]},
                headers={"X-Dev-Roles": "operator"})  # fmt: skip
    names = {z["name"] for z in _get(client, "/zones", tid, a)}
    assert "Web A" in names and "App B" not in names
    assert len(_get(client, "/zones", tid, None)) >= 2
    assert _get(client, "/zones/matrix", tid, a)  # Matrix aus Sicht von Kunde A

    vlans = {v["vid"] for v in _get(client, "/cabling/vlans", tid, a)}
    assert 201 not in vlans
    for path in ("/cabling/devices", "/cabling/links", "/cabling/ports", "/cabling/endpoints",
                 "/cabling/plan", "/routing/exits", "/routing/exits/suggestions",
                 "/routing/ha", "/routing/policy-routes", "/routing/l3outs",
                 "/systems/parallel", "/reconcile/ipam"):  # fmt: skip
        r = client.get(path, params={"tenant_id": tid, "customer_id": a})
        assert r.status_code == 200, (path, r.text)


def test_ohne_kunde_unveraendert(client):
    tid, *_ = _seed(client)
    rows = _get(client, "/inventory/subnets", tid, None)
    assert rows and all("shared" not in r for r in rows)
    assert all("shared" not in r for r in _get(client, "/routing/routes", tid, None))
    _ = TOKEN  # gemeinsame Testdaten
